import type {
  ActualDriverStrategy,
  Championship,
  ChampionshipHistory,
  DriverProfile,
  Roster,
  TrackListItem,
  TrackStats,
  Forecast,
  Calibration,
  CarsResponse,
  Health,
  IdealLap,
  LiveRecord,
  LongRuns,
  OvertakingIndex,
  Overtakes,
  PreStart,
  TeammatePairs,
  TeamSeason,
  Undercuts,
  WetRanking,
  PositionBattle,
  QualifyingRow,
  RaceDetail,
  RaceSessions,
  RaceTrace,
  RecordsHistory,
  SeasonResults,
  WinningStrategy,
  RaceListItem,
  StrategyPrediction,
  WhatIfRequest,
  WhatIfResponse,
  WinnerPrediction,
} from './types'

const BASE = import.meta.env.VITE_API_BASE ?? '/api'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* body was not JSON */
    }
    throw new ApiError(response.status, detail)
  }
  return response.json() as Promise<T>
}

/** Resolves to null on 404 (e.g. no forecast computed for this race yet). */
async function optional<T>(promise: Promise<T>): Promise<T | null> {
  try {
    return await promise
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null
    throw error
  }
}

export const api = {
  races: (season: number) => request<RaceListItem[]>(`/races?season=${season}`),
  race: (id: number) => request<RaceDetail>(`/races/${id}`),
  forecast: (id: number) => optional(request<Forecast>(`/races/${id}/forecast`)),
  actualStrategy: (id: number) =>
    optional(request<{ results: ActualDriverStrategy[] }>(`/races/${id}/strategy`)),
  strategyPrediction: (trackId: number, raceDate: string) =>
    optional(request<StrategyPrediction>(`/strategy/predict?track_id=${trackId}&race_date=${raceDate}`)),
  winnerPrediction: (id: number) => optional(request<WinnerPrediction>(`/races/${id}/winner-prediction`)),
  nextWinnerPrediction: () => optional(request<WinnerPrediction>('/races/next/winner-prediction')),
  championship: (season: number) => optional(request<Championship>(`/championship/${season}`)),
  championshipHistory: (season: number) =>
    optional(request<ChampionshipHistory>(`/championship/${season}/history`)),
  roster: (season: number) => optional(request<Roster>(`/drivers/roster/${season}`)),
  driverProfile: (id: number) => optional(request<DriverProfile>(`/drivers/${id}/profile`)),
  tracks: () => request<TrackListItem[]>('/tracks'),
  trackStats: (id: number) => optional(request<TrackStats>(`/tracks/${id}/stats`)),
  seasonResults: (season: number) => optional(request<SeasonResults>(`/seasons/${season}/results`)),
  raceSessions: (id: number) => optional(request<RaceSessions>(`/races/${id}/sessions`)),
  health: () => request<Health>('/health'),
  liveRecord: () => request<LiveRecord>('/records/live-predictions'),
  winningStrategy: (raceId: number) => optional(request<WinningStrategy>(`/strategy/winning?race_id=${raceId}`)),
  idealLap: (id: number, session?: string) =>
    optional(request<IdealLap>(`/races/${id}/ideal-lap${session ? `?session=${session}` : ''}`)),
  overtakes: (id: number) => optional(request<Overtakes>(`/races/${id}/overtakes`)),
  undercuts: (id: number) => optional(request<Undercuts>(`/races/${id}/undercuts`)),
  raceTrace: (id: number) => optional(request<RaceTrace>(`/races/${id}/race-trace`)),
  positionBattle: (id: number) => optional(request<PositionBattle>(`/races/${id}/position-battle`)),
  qualifying: (id: number) => optional(request<{ results: QualifyingRow[] }>(`/races/${id}/qualifying`)),
  longRuns: (id: number) => optional(request<LongRuns>(`/races/${id}/long-runs`)),
  teammates: (season: number) => optional(request<TeammatePairs>(`/seasons/${season}/teammates`)),
  recordsHistory: () => request<RecordsHistory>('/records/history'),
  calibration: () => request<Calibration>('/records/calibration'),
  cars: () => request<CarsResponse>('/cars'),
  overtaking: (trackId: number) => optional(request<OvertakingIndex>(`/tracks/${trackId}/overtaking-index`)),
  wetRanking: () => request<WetRanking>('/drivers/wet-weather-ranking'),
  teamSeason: (teamId: number, season: number) => optional(request<TeamSeason>(`/teams/${teamId}/season/${season}`)),
  preStart: (raceId: number) => optional(request<PreStart>(`/strategy/winning/pre-start?race_id=${raceId}`)),
  whatIf: (id: number, body: WhatIfRequest) =>
    request<WhatIfResponse>(`/races/${id}/what-if`, { method: 'POST', body: JSON.stringify(body) }),
}
