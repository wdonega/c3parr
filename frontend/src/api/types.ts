// Gerado do contrato da API (/api/openapi.json) por tests/test_frontend_types.py.
// Não edite à mão: mude web/schemas.py e regrave com
//   C3PARR_UPDATE_GOLDEN=1 uv run pytest tests/test_frontend_types.py

export interface ActionResult {
  ok: boolean
  message: string
}

export interface Activity {
  translations: number
  transcriptions: number
  extracted: number
  synced: number
  queued: number
  failed: number
}

export interface AlertRetry {
  key: string
}

export interface AlertRow {
  key: string
  kind: string
  step: string
  message: string
  target: string
  count: number
  first_at: string
  last_at: string
  seen: boolean
  resolved_at: string | null
  items: string[]
}

export interface Alerts {
  setup: SetupItem[]
  alerts: AlertRow[]
}

export interface AppConfig {
  radarr: ArrConfig
  sonarr: ArrConfig
  sync_interval_minutes: number
  target_languages: string[]
  fallback_languages: string[]
  monitor_new: boolean
  wait_minutes: number
  search_interval_minutes: number
  theme: string
  url_base: string
  instance_name: string
  allowed_hosts: string[]
  components: Record<string, string>
  llm_provider: string
  llm_base_url: string
  llm_model: string
  llm_effort: "low" | "medium"
  asr_mode: "local" | "openai" | "asr-webservice"
  asr_url: string
  asr_model: string
  use_embedded: boolean
  embedded_resolves: boolean
  external_resolves: boolean
  forced_resolves: boolean
  sync_new_external: boolean
  fix_external_in_place: boolean
  delete_ours_on_human: boolean
  new_external_becomes_main: boolean
  derived_on_main_change: "regenerate" | "keep" | "mark"
  asr_enabled: boolean
  asr_always: boolean
  foreign_tag: "first" | "every" | "off"
}

export interface ArrConfig {
  enabled: boolean
  host: string
  port: number | null
  base_url: string
  ssl: boolean
  timeout_s: number
  only_monitored: boolean
  excluded_tags: string[]
  excluded_series_types: string[]
  exclude_season_zero: boolean
  path_mappings: [string, string][]
}

export interface Dashboard {
  movies: LibraryCount
  episodes: EpisodeCount
  activity: Activity
  made: LanguageCount[]
  languages: LanguageRow[]
  alerts_open: number
  warnings: Notice[]
}

export interface Degraded {
  stage: string
  label: string
  reason: string
  effect: string
}

export interface EngineChoice {
  id: string
  description: string
}

export interface EngineInterface {
  id: string
  label: string
  selected: string
  providers: EngineChoice[]
}

export interface EpisodeCount {
  total: number
  done: number
  shows: number
}

export interface EpisodeRow {
  id: number
  season: number | null
  episode: number | null
  title: string | null
  name: string
  video: string
  subs: Sub[]
  missing: string[]
  job: JobRef | null
}

export interface Fact {
  label: string
  value: string
}

export interface HTTPValidationError {
  detail: ValidationError[]
}

export interface JobDetail {
  id: number
  name: string
  video: string
  status: "queued" | "running" | "done" | "done_with_warnings" | "failed" | "cancelled"
  action: string | null
  language: string | null
  step: string
  progress: number
  message: string | null
  error: string | null
  priority: boolean
  origin: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  item: JobItem | null
  stage: string | null
  steps: Step[]
  pipeline: PipelineStep[]
  warnings: Degraded[]
  has_subtitle: boolean
  has_report: boolean
  can_cancel: boolean
  can_retry: boolean
}

export interface JobItem {
  kind: "movie" | "episode"
  id: number
  title: string
  year: number | null
  show_id: number | null
  season: number | null
  episode: number | null
  episode_title: string | null
}

export interface JobPage {
  rows: JobRow[]
  total: number
  page: number
  pages: number
  counts: Record<string, number>
}

export interface JobRef {
  id: number
  label: string
  language: string | null
  status: "queued" | "running"
  progress: number
  position: number | null
}

export interface JobRow {
  id: number
  name: string
  video: string
  status: "queued" | "running" | "done" | "done_with_warnings" | "failed" | "cancelled"
  action: string | null
  language: string | null
  step: string
  progress: number
  message: string | null
  error: string | null
  priority: boolean
  origin: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  item: JobItem | null
}

export interface Language {
  code: string
  name: string
}

export interface LanguageCount {
  lang: string
  count: number
}

export interface LanguageRow {
  lang: string
  name: string
  embedded: number
  external: number
  ours: number
  missing: number
  target: boolean
}

export interface LibraryCount {
  total: number
  done: number
}

export interface Listening {
  address: string
  port: number
  url_base_from_env: string
}

export interface LlmProvider {
  id: string
  label: string
  site: string
  default_url: string
  default_model: string
  key: string
  effort: boolean
}

export interface LogRow {
  level: string
  time: string
  name: string
  message: string
}

export interface MonitorRequest {
  monitored?: boolean | null
}

export interface Monitored {
  monitored: boolean
}

export interface MoviePage {
  rows: MovieRow[]
  total: number
  page: number
  pages: number
  counts: Record<string, number>
}

export interface MovieRow {
  id: number
  title: string
  year: number | null
  folder: string
  video: string
  monitored: boolean
  added_at: string
  subs: Sub[]
  missing: string[]
  job: JobRef | null
  poster: string | null
}

export interface Notice {
  level: "warn" | "info"
  title: string
  detail: string
}

export interface PipelineStep {
  action: string
  label: string
  language: string | null
  state: "done" | "not_found" | "failed"
  detail: string | null
  attempts: number
  updated_at: string
}

export interface ProviderStatus {
  label: string
  provider: string
  ok: boolean
  detail: string
}

export interface QueueRequest {
  items: string[]
}

export interface SearchHit {
  id: number
  title: string
  year: number | null
  poster: string | null
}

export interface SearchResults {
  movies: SearchHit[]
  shows: SearchHit[]
}

export interface Season {
  season: number | null
  episodes: EpisodeRow[]
}

export interface SecretSource {
  name: string
  source: string
}

export interface SettingsOptions {
  languages: Language[]
  llm_providers: LlmProvider[]
  series_types: string[]
  engines: EngineInterface[]
  themes: string[]
  arr_keys: Record<string, string>
  tmdb_builtin: boolean
  tmdb_attribution: string
}

export interface SettingsPatch {
  changes?: Record<string, unknown>
  secrets?: Record<string, string | null>
}

export interface SettingsShown {
  llm_model: string
  llm_base_url: string
  llm_model_from_env: boolean
  llm_url_from_env: boolean
}

export interface SettingsView {
  listening: Listening
  tests: Record<string, TestRecord>
  config: AppConfig
  shown: SettingsShown
  secrets: Record<string, "settings" | "environment" | "">
  options: SettingsOptions
}

export interface SetupItem {
  title: string
  detail: string
  href: string
}

export interface Shell {
  version: string
  repo_url: string
  release_url: string
  name: string
  theme: string
  pending: number
  alerts_unseen: number
  setup: SetupItem[]
  movies: number
  shows: number
  running: JobRow | null
}

export interface ShowDetail {
  id: number
  title: string
  year: number | null
  folder: string
  monitored: boolean
  poster: string | null
  seasons: Season[]
}

export interface ShowPage {
  rows: ShowRow[]
  total: number
  page: number
  pages: number
  counts: Record<string, number>
}

export interface ShowRow {
  id: number
  title: string
  year: number | null
  folder: string
  monitored: boolean
  added_at: string
  total: number
  done: number
  missing: number
  queued: boolean
  poster: string | null
}

export interface Status {
  about: Fact[]
  providers: ProviderStatus[]
  secrets: SecretSource[]
  warnings: Notice[]
}

export interface Step {
  label: string
  state: "done" | "cached" | "skipped" | "failed" | "warning" | "running" | "pending"
  text: string
}

export interface Sub {
  lang: string
  kind: "embedded" | "external" | "ours"
}

export interface TaskRow {
  name: string
  label: string
  interval: string
  last_run: string | null
  next_run: string | null
  result: string | null
  running: boolean
  requested: boolean
}

export interface TestRecord {
  ok: boolean
  message: string
  at: string
  ms: number
}

export interface TestResult {
  ok: boolean
  message: string
  ms: number | null
}

export interface ValidationError {
  loc: (string | number)[]
  msg: string
  type: string
  input: unknown
  ctx: Record<string, unknown>
}
