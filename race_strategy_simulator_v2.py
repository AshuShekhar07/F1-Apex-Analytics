"""Probabilistic F1 race-strategy simulator v2 (dry races).

Fixes the structural problems of v1 (race_strategy_simulator_v1):
  * v1 simulated only "our" car lap by lap; competitors were pace x laps plus a
    fixed 1-2 stops with no tyre wear. v2 simulates EVERY car with the same
    lap engine: its own strategy (sampled from weighted options), tyre wear,
    stops and event exposure.
  * v1 made Safety Car laps faster for our car only and never discounted pit
    loss. v2 slows the whole field under SC/VSC, bunches it at the SC restart,
    and charges condition-specific pit loss (green / sc / vsc).
  * v1 had no running order (fixed 0.15 s per grid place). v2 tracks position
    every lap: a car passes only with a per-lap pace advantage of at least
    overtake_threshold_seconds, otherwise it is held behind. Undercuts and
    overcuts therefore emerge instead of being assumed.
  * Reaction rule: a car due to stop within sc_pit_window_laps pits under SC/VSC.

Scope: dry compounds only (wet strategy stays with the conservative baseline).
Retirements: each car retires with probability dnf_probability on a uniformly
random lap and is classified behind all finishers (later retirements ahead).
Not modelled: lapping/blue flags, DRS trains beyond the pass threshold.
Every input is explicit so it can be replaced by a validated calibration
(pit loss: race_strategy_pit_loss_v2; events: race_neutralisations_v1).

Vectorised over simulations with numpy. evaluate_candidates() uses common
random numbers: every candidate strategy sees the same events and noise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from race_strategy_simulator_v1 import (
    DRY_COMPOUNDS,
    Distribution,
    Strategy,
    TyreAllocation,
    beta_interval,
    validate_strategy,
)

COMPOUND_INDEX = {c: i for i, c in enumerate(DRY_COMPOUNDS)}
GREEN, SC, VSC, RED = 0, 1, 2, 3
NO_STOP = 10_000
RETIRED_LAP_SECONDS = 1e5


@dataclass(frozen=True)
class CarSpec:
    name: str
    grid_position: int
    base_pace: Distribution  # green-flag lap time on fresh reference tyres, seconds
    strategy_options: tuple[tuple[Strategy, float], ...]  # (strategy, weight)


@dataclass(frozen=True)
class TrackModel:
    total_laps: int
    tyre_deg_per_lap: dict[str, Distribution]      # seconds lost per lap of tyre age
    pit_loss: dict[str, Distribution]              # keys: green, sc, vsc
    overtake_threshold_seconds: float              # pace advantage per lap needed to pass
    compound_offset: dict[str, float] = field(default_factory=dict)  # seconds vs reference
    min_following_gap_seconds: float = 0.4
    grid_gap_seconds: float = 0.25
    lap_noise_seconds: float = 0.15
    sc_lap_factor: float = 1.40
    vsc_lap_factor: float = 1.35
    sc_restart_gap_seconds: float = 0.6
    dnf_probability: float = 0.0  # per car per race


@dataclass(frozen=True)
class EventModel:
    sc_per_lap: float
    vsc_per_lap: float
    red_per_lap: float = 0.0
    sc_duration_laps: tuple[int, int] = (3, 5)
    vsc_duration_laps: tuple[int, int] = (1, 3)
    sc_pit_window_laps: int = 5


@dataclass(frozen=True)
class CandidateResult:
    strategy: Strategy
    simulations: int
    win_probability: float
    podium_probability: float
    expected_finish: float
    p1_low: float
    p1_high: float
    finish_distribution: tuple[float, ...]


def _sample(dist: Distribution, rng: np.random.Generator, size) -> np.ndarray:
    values = np.full(size, dist.mean, dtype=float) if dist.std <= 0 else rng.normal(dist.mean, dist.std, size)
    if dist.lower is not None or dist.upper is not None:
        values = np.clip(values, dist.lower, dist.upper)
    return values


def _encode(strategy: Strategy) -> tuple[list[int], list[int]]:
    compounds = []
    for compound in strategy.sequence:
        if compound not in COMPOUND_INDEX:
            raise ValueError(f"simulator v2 is dry-only; got {compound}")
        compounds.append(COMPOUND_INDEX[compound])
    return compounds, list(strategy.stop_laps)


def sample_events(model: EventModel, total_laps: int, sims: int, rng: np.random.Generator) -> np.ndarray:
    """Per-lap track state (GREEN/SC/VSC/RED) for each simulation, shape (sims, laps+1)."""
    state = np.zeros((sims, total_laps + 1), dtype=np.int8)
    busy_until = np.zeros(sims, dtype=int)
    for lap in range(1, total_laps + 1):
        free = busy_until < lap
        draw = rng.random(sims)
        red = free & (draw < model.red_per_lap)
        sc = free & ~red & (draw < model.red_per_lap + model.sc_per_lap)
        vsc = free & ~red & ~sc & (draw < model.red_per_lap + model.sc_per_lap + model.vsc_per_lap)
        for mask, code, (lo, hi) in ((red, RED, (1, 1)), (sc, SC, model.sc_duration_laps), (vsc, VSC, model.vsc_duration_laps)):
            if not mask.any():
                continue
            durations = rng.integers(lo, hi + 1, size=sims)
            for s in np.flatnonzero(mask):
                end = min(total_laps, lap + durations[s] - 1)
                state[s, lap:end + 1] = code
                busy_until[s] = end
        # red flag is followed by a Safety Car restart lap
        if lap < total_laps and red.any():
            follow = state[red, lap + 1]
            state[red, lap + 1] = np.where(follow == GREEN, SC, follow)
            busy_until[red] = np.maximum(busy_until[red], lap + 1)
    return state


def simulate_race(
    cars: Sequence[CarSpec],
    track: TrackModel,
    events: EventModel,
    *,
    sims: int,
    seed: int,
    allocation: TyreAllocation | None = None,
    event_state: np.ndarray | None = None,
) -> np.ndarray:
    """Finishing position (1-based) of every car in every simulation, shape (sims, cars).

    event_state: optional fixed per-lap track state (GREEN/SC/VSC/RED), shape
    (laps+1,) or (sims, laps+1), e.g. a real race's windows replayed from
    race_neutralisations_v1. When omitted, events are sampled from `events`.
    """
    n, laps = len(cars), track.total_laps
    rng = np.random.default_rng(seed)
    for car in cars:
        for strategy, _ in car.strategy_options:
            if allocation is not None:
                validate_strategy(strategy, allocation, laps)
            elif strategy.stints[-1].end_lap != laps:
                raise ValueError(f"{car.name}: strategy does not cover {laps} laps")

    # --- per-simulation draws (order fixed so candidates share random numbers) ---
    state = sample_events(events, laps, sims, rng)
    if event_state is not None:
        state = np.broadcast_to(np.asarray(event_state, dtype=np.int8), (sims, laps + 1)).copy()
    deg = np.stack([_sample(track.tyre_deg_per_lap[c], rng, sims) for c in DRY_COMPOUNDS], axis=1)
    offset = np.array([track.compound_offset.get(c, 0.0) for c in DRY_COMPOUNDS])
    base = np.stack([_sample(car.base_pace, rng, sims) for car in cars], axis=1)
    noise = rng.normal(0.0, track.lap_noise_seconds, (sims, n, laps + 1))
    pit_loss = {k: _sample(track.pit_loss[k], rng, (sims, n)) for k in ("green", "sc", "vsc")}
    choice_draw = rng.random((sims, n))
    retires = rng.random((sims, n)) < track.dnf_probability
    retire_lap = rng.integers(1, laps + 1, size=(sims, n))

    # --- strategies -> (sims, cars, stints) arrays ---
    max_stints = max(len(s.stints) for car in cars for s, _ in car.strategy_options)
    seq = np.zeros((sims, n, max_stints), dtype=int)
    plan = np.full((sims, n, max_stints), NO_STOP, dtype=int)
    for j, car in enumerate(cars):
        weights = np.array([w for _, w in car.strategy_options], dtype=float)
        picks = np.searchsorted(np.cumsum(weights / weights.sum()), choice_draw[:, j], side="right")
        picks = np.minimum(picks, len(weights) - 1)
        for k, (strategy, _) in enumerate(car.strategy_options):
            compounds, stops = _encode(strategy)
            rows = picks == k
            seq[rows, j, :len(compounds)] = compounds
            plan[rows, j, :len(stops)] = stops

    stint = np.zeros((sims, n), dtype=int)
    age = np.zeros((sims, n), dtype=float)
    idx_s = np.arange(sims)[:, None]
    grid = np.array([car.grid_position for car in cars])
    cum = np.broadcast_to((grid - 1) * track.grid_gap_seconds, (sims, n)).astype(float).copy()
    reference_pace = np.median(base, axis=1, keepdims=True)

    for lap in range(1, laps + 1):
        status = state[:, lap][:, None]
        neutral = (status == SC) | (status == VSC)
        next_stop = plan[idx_s, np.arange(n), np.minimum(stint, max_stints - 1)]
        next_stop = np.where(stint < max_stints, next_stop, NO_STOP)
        # reaction: stop now under SC/VSC if the planned stop is close
        early = neutral & (next_stop > lap) & (next_stop - lap <= events.sc_pit_window_laps)
        pitting = (next_stop == lap) | early
        red = status[:, 0] == RED

        compound = seq[idx_s, np.arange(n), np.minimum(stint, max_stints - 1)]
        green_time = base + offset[compound] + deg[idx_s, compound] * age + noise[:, :, lap]
        lap_time = np.where(status == SC, reference_pace * track.sc_lap_factor,
                   np.where(status == VSC, reference_pace * track.vsc_lap_factor, green_time))
        lap_time = np.where(red[:, None], reference_pace, lap_time)
        loss = np.where(status == SC, pit_loss["sc"], np.where(status == VSC, pit_loss["vsc"], pit_loss["green"]))
        lap_time = lap_time + np.where(pitting & ~red[:, None], loss, 0.0)
        # retired cars drop out of the running order for good
        lap_time = lap_time + np.where(retires & (retire_lap <= lap), RETIRED_LAP_SECONDS, 0.0)

        new_cum = cum + lap_time
        # running order: blocked unless faster than the car ahead by the threshold
        green_lap = (status[:, 0] == GREEN)
        order = np.argsort(cum, axis=1, kind="stable")
        for k in range(1, n):
            me, ahead = order[:, k], order[:, k - 1]
            mine, theirs = new_cum[np.arange(sims), me], new_cum[np.arange(sims), ahead]
            advantage = lap_time[np.arange(sims), ahead] - lap_time[np.arange(sims), me]
            # a car pitting past one staying out is not an on-track pass; two cars
            # pitting on the same lap keep their order unless one is clearly faster
            exempt = (pitting[np.arange(sims), me] ^ pitting[np.arange(sims), ahead]) | ~green_lap
            blocked = ~exempt & (advantage < track.overtake_threshold_seconds) & (mine < theirs + track.min_following_gap_seconds)
            new_cum[np.arange(sims)[blocked], me[blocked]] = theirs[blocked] + track.min_following_gap_seconds

        # stops: new stint, fresh tyres; red flag = free tyre change for everyone
        stint = stint + pitting.astype(int)
        age = np.where(pitting | red[:, None], 0.0, age + 1.0)
        # Safety Car bunches the field before the restart
        ending_sc = (status[:, 0] == SC) & (state[:, min(lap + 1, laps)] != SC) & (lap < laps)
        if ending_sc.any():
            rows = np.flatnonzero(ending_sc)
            order_now = np.argsort(new_cum[rows], axis=1, kind="stable")
            leader = new_cum[rows, order_now[:, 0]][:, None]
            bunched = leader + np.arange(n)[None, :] * track.sc_restart_gap_seconds
            new_cum[rows[:, None], order_now] = bunched
        cum = new_cum

    return np.argsort(np.argsort(cum, axis=1, kind="stable"), axis=1) + 1


def evaluate_candidates(
    our_car: CarSpec,
    candidates: Sequence[Strategy],
    competitors: Sequence[CarSpec],
    track: TrackModel,
    events: EventModel,
    *,
    sims: int = 1000,
    seed: int = 7,
    allocation: TyreAllocation | None = None,
    objective: str = "p1",
    event_state: np.ndarray | None = None,
) -> list[CandidateResult]:
    """Rank our candidate strategies against simulated competitors (common random numbers)."""
    if objective not in {"p1", "expected_finish"}:
        raise ValueError("objective must be p1 or expected_finish")
    field_size = len(competitors) + 1
    results = []
    for strategy in candidates:
        car = CarSpec(our_car.name, our_car.grid_position, our_car.base_pace, ((strategy, 1.0),))
        positions = simulate_race([car, *competitors], track, events, sims=sims, seed=seed,
                                  allocation=allocation, event_state=event_state)[:, 0]
        wins = int((positions == 1).sum())
        low, high = beta_interval(wins, sims, seed + 1)
        distribution = np.bincount(positions, minlength=field_size + 1)[1:] / sims
        results.append(CandidateResult(
            strategy, sims, wins / sims, float((positions <= 3).mean()), float(positions.mean()),
            low, high, tuple(round(float(x), 4) for x in distribution),
        ))
    if objective == "expected_finish":
        return sorted(results, key=lambda r: (r.expected_finish, -r.win_probability))
    return sorted(results, key=lambda r: (-r.win_probability, -r.podium_probability, r.expected_finish))
