"""Probabilistic F1 race-strategy simulator (v1 foundation).

Pure-Python simulation/optimization primitives. This module intentionally has
no database or FastAPI dependency: the existing backend can feed it validated,
as-of-timestamped inputs later.

The v1 objective is P(P1) for a specific car from a specific grid position.
It models uncertainty explicitly and enforces tyre-allocation constraints before
an otherwise plausible strategy can enter the simulator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
import random
from statistics import mean
from typing import Iterable, Sequence

DRY_COMPOUNDS = ("SOFT", "MEDIUM", "HARD")
WET_COMPOUNDS = ("INTERMEDIATE", "WET")
ALL_COMPOUNDS = DRY_COMPOUNDS + WET_COMPOUNDS


@dataclass(frozen=True)
class Distribution:
    mean: float
    std: float = 0.0
    lower: float | None = None
    upper: float | None = None

    def sample(self, rng: random.Random) -> float:
        value = self.mean if self.std <= 0 else rng.gauss(self.mean, self.std)
        if self.lower is not None:
            value = max(self.lower, value)
        if self.upper is not None:
            value = min(self.upper, value)
        return value


@dataclass(frozen=True)
class TyreAllocation:
    sets: dict[str, int]
    mandatory_start_compound: str | None = None

    def validate(self) -> None:
        for compound, count in self.sets.items():
            compound = compound.upper()
            if compound not in ALL_COMPOUNDS:
                raise ValueError(f"Unknown compound: {compound}")
            if count < 0:
                raise ValueError("Tyre set counts cannot be negative")
        if self.mandatory_start_compound:
            start = self.mandatory_start_compound.upper()
            if self.sets.get(start, 0) < 1:
                raise ValueError(f"No set available for mandatory start tyre {start}")


@dataclass(frozen=True)
class StrategyStint:
    compound: str
    start_lap: int
    end_lap: int

    def __post_init__(self) -> None:
        compound = self.compound.upper()
        object.__setattr__(self, "compound", compound)
        if compound not in ALL_COMPOUNDS:
            raise ValueError(f"Unknown compound: {compound}")
        if self.start_lap < 1 or self.end_lap < self.start_lap:
            raise ValueError("Invalid stint lap range")


@dataclass(frozen=True)
class Strategy:
    name: str
    stints: tuple[StrategyStint, ...]
    source: str = "bounded_search"

    @property
    def sequence(self) -> tuple[str, ...]:
        return tuple(s.compound for s in self.stints)

    @property
    def stop_laps(self) -> tuple[int, ...]:
        return tuple(s.end_lap for s in self.stints[:-1])


@dataclass(frozen=True)
class WeatherTrajectory:
    rain_onset_lap: int | None
    rain_duration_laps: int
    intensity_mm_h: float
    track_temp_c: float

    def is_wet(self, lap: int) -> bool:
        if self.rain_onset_lap is None or self.rain_duration_laps <= 0:
            return False
        return self.rain_onset_lap <= lap < self.rain_onset_lap + self.rain_duration_laps

    def is_severe(self, lap: int) -> bool:
        return self.is_wet(lap) and self.intensity_mm_h > 4.0

    def is_crossover(self, lap: int) -> bool:
        return self.is_wet(lap) and 0.4 <= self.intensity_mm_h <= 4.0


@dataclass(frozen=True)
class CompetitorProfile:
    name: str
    grid_position: int
    base_pace_seconds: Distribution
    pit_stop_seconds: Distribution = field(default_factory=lambda: Distribution(2.4, 0.15, 1.8, 4.5))
    strategic_aggressiveness: float = 0.5


@dataclass(frozen=True)
class RaceContext:
    total_laps: int
    starting_grid: int
    our_base_pace_seconds: Distribution
    tyre_degradation_per_lap: dict[str, Distribution]
    pit_stop_seconds: Distribution
    pit_lane_loss_seconds: Distribution
    sc_probability_per_lap: float = 0.012
    vsc_probability_per_lap: float = 0.018
    red_flag_probability_per_lap: float = 0.002
    track_position_seconds_per_place: float = 0.15

    def validate(self) -> None:
        if self.total_laps <= 0 or self.starting_grid <= 0:
            raise ValueError("Race distance and grid position must be positive")
        for name in ("sc_probability_per_lap", "vsc_probability_per_lap", "red_flag_probability_per_lap"):
            value = getattr(self, name)
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")


@dataclass(frozen=True)
class SimulationParameters:
    weather_onset_lap: Distribution | None = None
    weather_duration_laps: Distribution = field(default_factory=lambda: Distribution(0.0, 0.0, 0.0, 80.0))
    rain_intensity_mm_h: Distribution = field(default_factory=lambda: Distribution(0.0, 0.0, 0.0, 15.0))
    track_temp_c: Distribution = field(default_factory=lambda: Distribution(35.0, 2.5, 10.0, 60.0))
    pace_multiplier: Distribution = field(default_factory=lambda: Distribution(1.0, 0.003, 0.985, 1.015))
    tyre_deg_multiplier: Distribution = field(default_factory=lambda: Distribution(1.0, 0.08, 0.75, 1.30))
    model_source: str = "calibrated_inputs"


@dataclass(frozen=True)
class RaceEvents:
    safety_car_laps: tuple[int, ...] = ()
    vsc_laps: tuple[int, ...] = ()
    red_flag_laps: tuple[int, ...] = ()


@dataclass
class SimulationResult:
    winner: bool
    finish_position: int
    total_time_seconds: float
    strategy_name: str
    weather: WeatherTrajectory
    events: RaceEvents


@dataclass(frozen=True)
class StrategyEvaluation:
    strategy: Strategy
    simulations: int
    win_probability: float
    podium_probability: float
    expected_finish: float
    p1_low: float
    p1_high: float


def validate_strategy(strategy: Strategy, allocation: TyreAllocation, total_laps: int) -> None:
    allocation.validate()
    if not strategy.stints:
        raise ValueError("Strategy must contain at least one stint")
    if strategy.stints[0].start_lap != 1 or strategy.stints[-1].end_lap != total_laps:
        raise ValueError("Strategy must cover the entire race from lap 1 to the final lap")

    for previous, current in zip(strategy.stints, strategy.stints[1:]):
        if current.start_lap != previous.end_lap + 1:
            raise ValueError("Stints must be contiguous")

    used: dict[str, int] = {}
    for stint in strategy.stints:
        used[stint.compound] = used.get(stint.compound, 0) + 1

    for compound, count in used.items():
        available = allocation.sets.get(compound, 0)
        if count > available:
            raise ValueError(f"Strategy uses {count} {compound} sets; only {available} available")

    required = allocation.mandatory_start_compound
    if required and strategy.stints[0].compound != required.upper():
        raise ValueError(f"Strategy must start on {required.upper()}")


def sample_weather(params: SimulationParameters, total_laps: int, rng: random.Random) -> WeatherTrajectory:
    onset = None
    if params.weather_onset_lap is not None:
        value = params.weather_onset_lap.sample(rng)
        if value >= 1:
            onset = min(total_laps, max(1, round(value)))

    duration = max(0, round(params.weather_duration_laps.sample(rng)))
    intensity = max(0.0, params.rain_intensity_mm_h.sample(rng))
    track_temp = params.track_temp_c.sample(rng)

    if onset is None or duration == 0 or intensity <= 0:
        return WeatherTrajectory(None, 0, 0.0, track_temp)

    return WeatherTrajectory(onset, duration, intensity, track_temp)


def sample_events(context: RaceContext, weather: WeatherTrajectory, rng: random.Random) -> RaceEvents:
    sc: list[int] = []
    vsc: list[int] = []
    red: list[int] = []

    for lap in range(1, context.total_laps + 1):
        severity = 1.75 if weather.is_severe(lap) else 1.0
        # One event per lap; later calibration can replace these priors with
        # track/era/phase hazard models.
        if rng.random() < min(0.25, context.red_flag_probability_per_lap * severity):
            red.append(lap)
        elif rng.random() < min(0.40, context.sc_probability_per_lap * severity):
            sc.append(lap)
        elif rng.random() < min(0.50, context.vsc_probability_per_lap * severity):
            vsc.append(lap)

    return RaceEvents(tuple(sc), tuple(vsc), tuple(red))


def tyre_penalty(compound: str, tyre_age: int, lap: int, weather: WeatherTrajectory, context: RaceContext, deg_multiplier: float) -> float:
    deg = context.tyre_degradation_per_lap.get(compound.upper(), Distribution(0.08, 0.02, 0.0, 0.30))
    penalty = max(0.0, deg.mean) * max(0, tyre_age) * deg_multiplier

    if weather.is_wet(lap):
        if compound in DRY_COMPOUNDS:
            penalty += 3.5 + 0.35 * weather.intensity_mm_h
        elif compound == "INTERMEDIATE":
            penalty += max(0.0, weather.intensity_mm_h - 5.0) * 0.25
        elif compound == "WET":
            penalty += max(0.0, 3.0 - weather.intensity_mm_h) * 0.20
    elif compound in WET_COMPOUNDS:
        penalty += 1.8

    if compound in DRY_COMPOUNDS:
        penalty += max(0.0, weather.track_temp_c - 38.0) * 0.015

    return penalty


def _compound_at_lap(strategy: Strategy, lap: int) -> str:
    for stint in strategy.stints:
        if stint.start_lap <= lap <= stint.end_lap:
            return stint.compound
    raise RuntimeError(f"No compound for lap {lap}")


def _simulate_car(strategy: Strategy, base_pace: float, context: RaceContext, weather: WeatherTrajectory, events: RaceEvents, rng: random.Random, pace_multiplier: float, tyre_deg_multiplier: float) -> float:
    total = 0.0
    tyre_age = 0
    current_compound = strategy.stints[0].compound
    pit_laps = set(strategy.stop_laps)

    for lap in range(1, context.total_laps + 1):
        compound = _compound_at_lap(strategy, lap)
        if compound != current_compound:
            current_compound = compound
            tyre_age = 0

        lap_time = base_pace * pace_multiplier + rng.gauss(0.0, 0.10)
        lap_time += tyre_penalty(compound, tyre_age, lap, weather, context, tyre_deg_multiplier)

        if lap in events.safety_car_laps:
            lap_time -= min(3.0, lap_time * 0.15)
        elif lap in events.vsc_laps:
            lap_time -= min(1.5, lap_time * 0.08)

        if lap in pit_laps:
            lap_time += context.pit_stop_seconds.sample(rng)
            lap_time += context.pit_lane_loss_seconds.sample(rng)
            tyre_age = -1

        total += max(40.0, lap_time)
        tyre_age += 1

    return total


def simulate_once(strategy: Strategy, context: RaceContext, competitors: Sequence[CompetitorProfile], allocation: TyreAllocation, params: SimulationParameters, rng: random.Random) -> SimulationResult:
    context.validate()
    validate_strategy(strategy, allocation, context.total_laps)

    weather = sample_weather(params, context.total_laps, rng)
    events = sample_events(context, weather, rng)

    our_time = _simulate_car(
        strategy,
        context.our_base_pace_seconds.sample(rng),
        context,
        weather,
        events,
        rng,
        params.pace_multiplier.sample(rng),
        params.tyre_deg_multiplier.sample(rng),
    )

    competitor_times: list[float] = []
    for competitor in competitors:
        pace = competitor.base_pace_seconds.sample(rng) * params.pace_multiplier.sample(rng)
        total = pace * context.total_laps
        if weather.rain_onset_lap is not None:
            wet_penalty = weather.rain_duration_laps * (0.08 + 0.06 * competitor.strategic_aggressiveness)
            total += wet_penalty
        total += max(0, competitor.grid_position - context.starting_grid) * context.track_position_seconds_per_place
        # v1 competitor behaviour is deliberately coarse: reactive event/weather
        # impact without a static prediction of exact pit laps.
        pit_count = 1 if competitor.strategic_aggressiveness > 0.75 else 2
        total += sum(
            competitor.pit_stop_seconds.sample(rng) + context.pit_lane_loss_seconds.sample(rng)
            for _ in range(pit_count)
        )
        competitor_times.append(total)

    finish_position = 1 + sum(other < our_time for other in competitor_times)
    return SimulationResult(
        winner=finish_position == 1,
        finish_position=finish_position,
        total_time_seconds=our_time,
        strategy_name=strategy.name,
        weather=weather,
        events=events,
    )


def _percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * q
    lo = int(index)
    hi = min(lo + 1, len(ordered) - 1)
    frac = index - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def beta_interval(successes: int, trials: int, seed: int = 7, draws: int = 2000) -> tuple[float, float]:
    if trials <= 0:
        return 0.0, 1.0
    rng = random.Random(seed)
    values = [rng.betavariate(1 + successes, 1 + trials - successes) for _ in range(max(200, draws))]
    return _percentile(values, 0.10), _percentile(values, 0.90)


def evaluate_strategy(strategy: Strategy, context: RaceContext, competitors: Sequence[CompetitorProfile], allocation: TyreAllocation, params: SimulationParameters, simulations: int = 5000, seed: int = 7) -> StrategyEvaluation:
    if simulations <= 0:
        raise ValueError("simulations must be positive")
    validate_strategy(strategy, allocation, context.total_laps)

    rng = random.Random(seed)
    positions: list[int] = []
    wins = 0
    podiums = 0

    for _ in range(simulations):
        result = simulate_once(strategy, context, competitors, allocation, params, rng)
        positions.append(result.finish_position)
        wins += int(result.winner)
        podiums += int(result.finish_position <= 3)

    low, high = beta_interval(wins, simulations, seed + 1)
    return StrategyEvaluation(
        strategy=strategy,
        simulations=simulations,
        win_probability=wins / simulations,
        podium_probability=podiums / simulations,
        expected_finish=mean(positions),
        p1_low=low,
        p1_high=high,
    )


def build_candidate_strategies(total_laps: int, allowed_sequences: Iterable[Sequence[str]], allocation: TyreAllocation, stop_offsets: Sequence[int] = (-4, 0, 4)) -> list[Strategy]:
    """Build a bounded and legal fixed-sequence search space for v1."""
    allocation.validate()
    candidates: list[Strategy] = []
    seen: set[tuple[tuple[str, int, int], ...]] = set()

    for raw in allowed_sequences:
        sequence = tuple(c.upper() for c in raw)
        if not sequence or any(c not in ALL_COMPOUNDS for c in sequence):
            continue
        if allocation.mandatory_start_compound and sequence[0] != allocation.mandatory_start_compound.upper():
            continue

        base_stops = [round(total_laps * (i + 1) / len(sequence)) for i in range(len(sequence) - 1)]
        for offset in stop_offsets:
            stops = [max(2, min(total_laps - 1, lap + offset)) for lap in base_stops]
            if any(a >= b for a, b in zip(stops, stops[1:])):
                continue

            boundaries = [0, *stops, total_laps]
            stints = tuple(
                StrategyStint(sequence[i], boundaries[i] + 1, boundaries[i + 1])
                for i in range(len(sequence))
            )
            key = tuple((s.compound, s.start_lap, s.end_lap) for s in stints)
            if key in seen:
                continue
            seen.add(key)
            strategy = Strategy(
                name=" → ".join(sequence) + f" [{','.join(map(str, stops))}]",
                stints=stints,
            )
            try:
                validate_strategy(strategy, allocation, total_laps)
            except ValueError:
                continue
            candidates.append(strategy)

    return candidates


def optimize_strategies(
    candidates: Sequence[Strategy],
    context: RaceContext,
    competitors: Sequence[CompetitorProfile],
    allocation: TyreAllocation,
    params: SimulationParameters,
    simulations_per_strategy: int = 3000,
    seed: int = 7,
    objective: str = "p1",
) -> list[StrategyEvaluation]:
    """Evaluate candidates and rank them by an explicit strategy objective.

    ``p1`` preserves the v1 product objective. ``expected_finish`` is useful for
    walk-forward experiments because the backtest scores predicted finishing
    position rather than only win probability.
    """
    if objective not in {"p1", "expected_finish"}:
        raise ValueError("objective must be p1 or expected_finish")

    evaluations = [
        evaluate_strategy(
            strategy, context, competitors, allocation, params,
            simulations_per_strategy, seed + i,
        )
        for i, strategy in enumerate(candidates)
    ]
    if objective == "expected_finish":
        return sorted(
            evaluations,
            key=lambda x: (x.expected_finish, -x.podium_probability, -x.win_probability),
        )
    return sorted(
        evaluations,
        key=lambda x: (x.win_probability, x.podium_probability, -x.expected_finish),
        reverse=True,
    )
