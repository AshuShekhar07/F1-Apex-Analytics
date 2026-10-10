// Shapes returned by the Apex21 FastAPI backend (app/routers/*).

export interface RaceListItem {
  id: number
  season_year: number
  round_number: number
  race_date: string
  weekend_format: string | null
  track_name: string
  country: string | null
  winner_name: string | null
  winner_team: string | null
  winner_color_hex: string | null
  has_forecast: boolean
  predicted_winner_name: string | null
  predicted_winner_color_hex: string | null
}

export interface RaceResult {
  driver_id: number
  driver_name: string
  car_number: number | null
  team_name: string
  color_hex: string | null
  finishing_position: number | null
  starting_grid_position: number | null
  points: number | null
  status: string | null
  display: string | null
}

export interface RaceDetail {
  id: number
  season_year: number
  round_number: number
  race_date: string
  weekend_format: string | null
  track_id: number
  track_name: string
  country: string | null
  length_km: number | null
  results: RaceResult[]
}

export interface StrategyPlan {
  sequence: string[]
  stop_laps: number[]
}

export interface ForecastDriver {
  driver_id: number
  driver_name: string
  team_name: string | null
  color_hex: string | null
  grid: number
  predicted_position: number
  p_win: number
  p_podium: number
  strategy: StrategyPlan | null
}

export interface Forecast {
  race_id: number
  model_version: string
  as_of_date: string
  grid_source: string
  computed_at: string
  total_laps: number | null
  drivers: ForecastDriver[]
  strategy_alternatives: (StrategyPlan & { weight: number })[]
  strategy_extras: { method: string; weather?: Record<string, unknown> }
  data_notes: string[]
  validation: Validation
}

export interface Validation {
  source: string
  predicted_position: { method: string; mean_abs_error_places: number; grid_only_mean_abs_error_places: number }
  p_win: { method: string; brier_score: number }
  p_podium: { method: string; brier_score: number }
  strategy: {
    method: string
    stop_count_accuracy: number
    sequence_accuracy: number
    first_stop_mean_abs_error_laps: number
  }
}

export interface Stint {
  compound: string
  start_lap: number
  end_lap: number
}

export interface StrategyOption {
  sequence: string[]
  stop_laps: number[]
  stints: Stint[]
  share?: number
}

export interface StrategyPrediction {
  model_version: string
  source: string
  track_name: string
  total_laps: number
  stage: string
  primary_strategy: StrategyOption
  alternatives: StrategyOption[]
  weather: { rain_expected: boolean | null; wet_strategy_trigger: boolean | null } | null
  validation: {
    dry: {
      races: string
      era_mode: { sequence_accuracy: number; stop_count_accuracy: number; first_stop_mean_abs_error_laps: number }
    }
  }
  data_notes: string[]
}

export interface ActualDriverStrategy {
  driver_id: number
  driver_name: string
  color_hex: string | null
  finishing_position: number | null
  stints: { compound: string | null; start_lap: number; end_lap: number }[]
}

export interface PositionSummary {
  expected_position: number
  median_position: number
  most_likely_position: number
  p_win: number
  p_podium: number
}

export interface WhatIfResult {
  driver_id: number
  driver_name?: string
  team_name?: string
  color_hex?: string | null
  grid: number
  baseline: PositionSummary
  scenario: PositionSummary
  expected_position_change: number
}

export interface WhatIfRequest {
  safety_car_laps: number[]
  vsc_laps: number[]
  strategies: Record<number, StrategyPlan>
  simulations: number
}

export interface WhatIfResponse {
  note: string
  results: WhatIfResult[]
}

// --- predictions (championship_model_v1, winner_prediction_v1) ---------------------

export type ConfidenceLevel = 'high' | 'medium' | 'low'

export interface WinnerConfidence {
  level: ConfidenceLevel
  basis: string
  data_basis?: string
  favourite_won_historically?: number
  races?: number
}

export interface WinnerDriver {
  driver_id: number
  driver_name: string | null
  team_name: string | null
  color_hex?: string | null
  grid: number | null
  p_win: number
  p_podium: number
}

export interface WinnerStage {
  favourite: {
    driver_id: number
    driver_name: string | null
    team_name: string | null
    color_hex?: string | null
    p_win: number
    p_podium: number
  }
  confidence: WinnerConfidence
  drivers: WinnerDriver[]
}

export type PredictionStage = 'pre_qualifying' | 'post_qualifying'

export interface WinnerPrediction {
  race_id: number
  season: number
  round: number
  track: string
  race_date: string
  stage: PredictionStage
  grid_source: string | null
  lineup_source: string
  evidence: {
    races_this_season: number
    races_under_these_regulations: number
    new_regulations: boolean
    bucket: string
  }
  predicted_winner: WinnerStage['favourite']
  confidence: WinnerConfidence
  stages: Partial<Record<PredictionStage, WinnerStage>>
}

export interface TitleConfidence {
  stage: string
  level: ConfidenceLevel
  basis: string
  chance_basis?: string
  measured?: boolean
}

export interface TitleEntry {
  driver_id?: number
  team_id?: number
  driver_name?: string | null
  team_name?: string | null
  color_hex?: string | null
  p_title: number
  points_now: number
  expected_points: number
}

export interface Championship {
  season: number
  stored: boolean
  after_round: number
  weekends_left: number
  computed_at: string | null
  drivers: TitleEntry[]
  constructors: TitleEntry[]
  confidence: { drivers: TitleConfidence; constructors: TitleConfidence }
}

export interface TitleSeries {
  id: number
  name: string
  rounds: { after_round: number; p_title: number }[]
}

export interface ChampionshipHistory {
  season: number
  drivers: TitleSeries[]
  constructors: TitleSeries[]
}

// --- new frontend: roster, tracks, driver profile -------------------------------------

export interface DriverStats {
  races: number
  wins: number
  podiums: number
  points: number
  poles: number
  best_finish: number | null
  average_finish: number | null
  average_qualifying: number | null
  dnfs: number
}

export interface RosterDriver {
  id: number
  name: string
  number: number | null
  nationality: string | null
  country_code: string | null
  photo_url: string | null
  world_championships: number
  team: { id: number; name: string; color_hex: string | null; logo_url: string | null }
  season: DriverStats
  career_since_2018: DriverStats
}

export interface Roster {
  season: number
  drivers: RosterDriver[]
  note: string
}

export interface DriverProfile {
  id: number
  name: string
  nationality: string | null
  photo_url: string | null
  total_world_championships: number | null
  current_team: { id: number; name: string; color_hex: string | null; logo_url: string | null } | null
  team_history: { team_name: string; logo_url: string | null; start_year: number; end_year: number }[]
  track_records_held: { track_name: string; lap_time: number; season_year: number }[]
  track_records_note: string
  last_gp_win: { track_name: string; season_year: number; round_number: number; race_date: string } | null
}

export interface TrackListItem {
  id: number
  name: string
  country: string | null
  length_km: number | string | null
  lap_record: string | null
  races: number
  first_season: number | null
  last_season: number | null
}

export interface TrackStats {
  id: number
  name: string
  country: string | null
  length_km: number | string | null
  lap_record: string | null
  first_year_raced: number | null
  total_races_recorded: number
  historical_winners: { season_year: number; driver_name: string; team_name: string; color_hex: string | null }[]
}

// --- pit wall redesign: season matrix, weekend sessions, live record, strategy, laps ----------

export interface MatrixResult {
  position: number | null
  grid: number | null
  points: number
  status: string | null
  classified: boolean
  dnf: boolean
}

export interface MatrixCell {
  round: number
  team_id: number | null
  race: MatrixResult | null
  sprint: MatrixResult | null
}

export interface MatrixTeam {
  id: number
  name: string | null
  color_hex: string | null
  logo_url: string | null
}

export interface SeasonResults {
  season: number
  latest_round: number | null
  rounds: { race_id: number; round: number; race_date: string; track_name: string; country: string | null;
    weekend_format: string | null; sprint: boolean; raced: boolean }[]
  drivers: { id: number; name: string; number: number | null; team: MatrixTeam | null; points: number; wins: number;
    podiums: number; results: MatrixCell[] }[]
  teams: (MatrixTeam & { points: number; wins: number; by_round: { round: number; points: number }[] })[]
  note: string
}

export interface WeekendSession {
  type: string
  name: string
  loaded: boolean
  start_time_utc: string | null
  has_laps: boolean
  has_results: boolean
}

export interface RaceSessions {
  race_id: number
  season: number
  round: number
  race_date: string
  track_name: string
  weekend_format: string
  sessions: WeekendSession[]
}

export interface Health {
  status: string
  problems: string[]
  latest_forecast: { race_id: number; grid_source: string; computed_at: string } | null
  last_pipeline_run: { job: string; started_at: string; finished_at: string | null; status: string } | null
  last_pipeline_run_minutes_ago: number | null
}

export interface LiveCall {
  race_id: number
  season: number
  round: number
  track: string
  stage: string
  confidence: string
  favourite: string
  favourite_p_win: number
  winner: string | null
  winner_rank: number | null
  winner_p_win: number
  favourite_won: boolean
  winner_in_top3: boolean
}

export interface LiveRecord {
  model_version: string
  summary: Record<string, { calls: number; favourite_won: number; winner_in_top3: number; mean_favourite_p_win: number }>
  calls: LiveCall[]
}

export interface PitWindow {
  stop: number
  first_lap: number
  last_lap: number
  typical_lap: number
}

export interface WinningPlan {
  sequence: string[]
  stops: number
  pit_windows: PitWindow[]
  confidence: string
  front_grid_share: number | null
  stop_chances: Record<string, number>
  alternative: { sequence: string[]; stops: number; pit_windows: PitWindow[]; front_grid_share: number } | null
  track_cars: number
  track_races: number
  note: string
}

export interface WinningStrategy {
  race_id: number
  track: string
  season: number
  race_date: string
  total_laps: number | null
  practice_wear_measured: boolean
  prediction: WinningPlan
  track_record: { race_id: number; season: number; winner_grid: number | null; winner_sequence: string[] | null;
    winner_stop_laps: number[] }[]
}

export interface PositionBattle {
  race_id: number
  track_name: string
  results: { driver_id: number; driver_name: string; team_name: string; color_hex: string | null;
    starting_grid_position: number | null; finishing_position: number | null; status: string | null;
    positions: { lap: number; position: number | null }[] }[]
}

export interface QualifyingRow {
  driver_id: number
  driver_name: string
  car_number: number | null
  team_name: string
  color_hex: string | null
  final_position: number | null
  q1_time: number | null
  q2_time: number | null
  q3_time: number | null
  gap_to_pole: number | null
  eliminated_in: string | null
}

// --- second wave: long runs, teammates, calibration, overtaking, wet ranking, teams, pre-start

export interface LongRuns {
  race_id: number
  session: string | null
  wet: boolean
  reference_compound?: string
  reference_lap?: number
  drivers: { driver_id: number; driver_name: string | null; team_name: string | null; color_hex: string | null;
    gap_pct: number; gap_s: number; compounds: Record<string, { laps: number; median: number }> }[]
  note?: string
}

export interface TeammatePairs {
  season: number
  pairs: { team: { id: number; name: string | null; color_hex: string | null }; rounds: number;
    drivers: { id: number; name: string; quali_wins: number; race_wins: number; points: number }[] }[]
  note: string
}

export interface Calibration {
  source: string
  stages: Record<string, { races: number; favourite_won: number; mean_favourite_p: number;
    bins: { lo: number; hi: number; races: number; mean_p: number; favourite_won: number }[] }>
}

export interface OvertakingIndex {
  track_id: number
  track_name: string
  overtaking: {
    overall: { avg_position_change: number | null; avg_position_change_classified: number | null; races_counted: number }
    by_regulation_era: { regulation_era: string; avg_position_change: number; avg_position_change_classified: number | null; races_counted: number }[]
  }
  safety_car_likelihood_pct: number | null
  safety_car_sample_races: number
}

export interface WetRanking {
  note: string
  race_rankings: { driver_id: number; driver_name: string | null; wet_avg_margin_over_teammate: number;
    dry_avg_margin_over_teammate: number; specialist_score: number; wet_sessions_counted: number; dry_sessions_counted: number }[]
}

export interface TeamSeason {
  team_id: number
  team_name: string
  color_hex: string | null
  logo_url: string | null
  season: number
  races: number
  wins: number
  podiums: number
  race_points: number
  sprint_points: number
  total_points: number
  average_finish: number | null
  average_qualifying: number | null
  dnfs: number
  drivers: { driver_id: number; driver_name: string; car_number: number | null; races_entered: number }[]
}

export interface PreStart {
  race_id: number
  cars: { grid: number; driver: string | null; start: string }[]
  plans: Record<string, WinningPlan>
  note: string
}
