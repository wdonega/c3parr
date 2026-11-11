// A API do c3parr (/api/v1). Os formatos vêm de ./types, gerados do contrato do servidor.
import type {
  ActionResult,
  Alerts,
  Dashboard,
  JobDetail,
  JobPage,
  JobRow,
  LogRow,
  Monitored,
  MoviePage,
  SearchResults,
  SettingsPatch,
  SettingsView,
  Shell,
  ShowDetail,
  ShowPage,
  Status,
  TaskRow,
  TestResult,
} from './types'

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

const BASE = '/api/v1'

type Params = Record<string, string | number | undefined>

function query(params?: Params): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== undefined && value !== '') search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(BASE + path, {
    ...init,
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
  })
  const text = await response.text()
  let body: unknown = null
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = null
  }
  if (response.ok) return body as T
  // ação recusada por uma regra (422): vem {ok: false, message} e a tela mostra a frase
  if (body && typeof body === 'object' && 'ok' in body) return body as T
  const detail = body && typeof body === 'object' && 'detail' in body ? String(body.detail) : ''
  throw new ApiError(response.status, detail || `HTTP ${response.status}`)
}

const get = <T>(path: string, params?: Params) => request<T>(path + query(params))

const send = <T>(method: string, path: string, body?: unknown) =>
  request<T>(path, { method, body: body === undefined ? undefined : JSON.stringify(body) })

export type ListParams = { q?: string; show?: string; page?: number; sort?: string }
export type QueueParams = { q?: string; status?: string; page?: number }
export type ItemKind = 'movie' | 'series'

export const api = {
  shell: () => get<Shell>('/shell'),
  dashboard: () => get<Dashboard>('/dashboard'),
  search: (q: string) => get<SearchResults>('/search', { q }),

  movies: (params: ListParams) => get<MoviePage>('/movies', params),
  shows: (params: ListParams) => get<ShowPage>('/shows', params),
  show: (id: number) => get<ShowDetail>(`/shows/${id}`),
  monitor: (kind: ItemKind, id: number, monitored: boolean) =>
    send<Monitored>('POST', `/library/${kind}/${id}/monitor`, { monitored }),
  addToQueue: (items: string[]) => send<ActionResult>('POST', '/library/queue', { items }),

  queue: (params: QueueParams) => get<JobPage>('/queue', params),
  upcoming: (limit: number) => get<JobRow[]>('/queue/upcoming', { limit }),
  recent: (limit: number) => get<JobRow[]>('/queue/recent', { limit }),
  job: (id: number) => get<JobDetail>(`/queue/${id}`),
  cancelJob: (id: number) => send<JobDetail>('POST', `/queue/${id}/cancel`),
  retryJob: (id: number) => send<JobDetail>('POST', `/queue/${id}/retry`),
  topJob: (id: number) => send<JobDetail>('POST', `/queue/${id}/top`),
  cancelQueued: () => send<ActionResult>('POST', '/queue/cancel-queued'),
  retryFailed: () => send<ActionResult>('POST', '/queue/retry-failed'),

  settings: () => get<SettingsView>('/settings'),
  saveSettings: (patch: SettingsPatch) => send<ActionResult>('PATCH', '/settings', patch),
  testConnection: (target: string, patch: SettingsPatch) =>
    send<TestResult>('POST', `/settings/test/${target}`, patch),

  tasks: () => get<TaskRow[]>('/system/tasks'),
  runTask: (name: string) => send<ActionResult>('POST', `/system/tasks/${name}/run`),
  alerts: () => get<Alerts>('/system/alerts'),
  alertsSeen: () => send<ActionResult>('POST', '/system/alerts/seen'),
  retryAlert: (key: string) => send<ActionResult>('POST', '/system/alerts/retry', { key }),
  logs: () => get<LogRow[]>('/system/logs'),
  status: () => get<Status>('/system/status'),
}

export const subtitleUrl = (jobId: number) => `${BASE}/queue/${jobId}/subtitle`
export const reportUrl = (jobId: number) => `${BASE}/queue/${jobId}/report`

// De quanto em quanto tempo cada dado é relido enquanto a aba está aberta.
export const LIVE = { fast: 2000, normal: 4000, slow: 15000 } as const
