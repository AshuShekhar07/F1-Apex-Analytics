import type {
  ActualDriverStrategy,
  Forecast,
  RaceDetail,
  RaceListItem,
  StrategyPrediction,
  WhatIfRequest,
  WhatIfResponse,
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
  whatIf: (id: number, body: WhatIfRequest) =>
    request<WhatIfResponse>(`/races/${id}/what-if`, { method: 'POST', body: JSON.stringify(body) }),
}
