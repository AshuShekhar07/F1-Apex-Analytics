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
