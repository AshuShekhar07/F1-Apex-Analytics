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
