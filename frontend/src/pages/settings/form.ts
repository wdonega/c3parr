// O rascunho de Settings: só o que foi mexido, que é o que a API recebe. Voltar um campo ao
// valor salvo tira ele do rascunho, então a contagem de alterações é sempre a de verdade.
import type { AppConfig, ArrConfig, SettingsPatch, SettingsView } from '../../api/types'

export type ArrKind = 'radarr' | 'sonarr'
export type Draft = {
  changes: Record<string, unknown>
  /** Chave → valor novo; `null` apaga a salva. */
  secrets: Record<string, string | null>
}

export const EMPTY: Draft = { changes: {}, secrets: {} }

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

function without(record: Record<string, unknown>, key: string): Record<string, unknown> {
  const { [key]: _removed, ...rest } = record
  return rest
}

/** O valor que a tela mostra: o do rascunho, ou o salvo. */
export function fieldValue<K extends keyof AppConfig>(draft: Draft, saved: AppConfig, key: K): AppConfig[K] {
  return (key in draft.changes ? draft.changes[key] : saved[key]) as AppConfig[K]
}

export function setField<K extends keyof AppConfig>(
  draft: Draft,
  saved: AppConfig,
  key: K,
  value: AppConfig[K],
): Draft {
  const changes = same(value, saved[key])
    ? without(draft.changes, key)
    : { ...draft.changes, [key]: value }
  return { ...draft, changes }
}

export function arrValue<K extends keyof ArrConfig>(
  draft: Draft,
  saved: AppConfig,
  kind: ArrKind,
  key: K,
): ArrConfig[K] {
  const changed = (draft.changes[kind] ?? {}) as Partial<ArrConfig>
  return (key in changed ? changed[key] : saved[kind][key]) as ArrConfig[K]
}

export function setArrField<K extends keyof ArrConfig>(
  draft: Draft,
  saved: AppConfig,
  kind: ArrKind,
  key: K,
  value: ArrConfig[K],
): Draft {
  const before = (draft.changes[kind] ?? {}) as Record<string, unknown>
  const arr = same(value, saved[kind][key]) ? without(before, key) : { ...before, [key]: value }
  const changes = Object.keys(arr).length
    ? { ...draft.changes, [kind]: arr }
    : without(draft.changes, kind)
  return { ...draft, changes }
}

/** Digitar define a chave nova; campo vazio volta a "não mexi"; `null` pede para apagar. */
export function setSecret(draft: Draft, name: string, value: string | null): Draft {
  const { [name]: _removed, ...rest } = draft.secrets
  return { ...draft, secrets: value === '' ? rest : { ...rest, [name]: value } }
}

/** Cada campo mexido, pelo caminho dele: `wait_minutes`, `radarr.host`, `RADARR_API_KEY`. */
export function changedFields(draft: Draft): string[] {
  const fields: string[] = []
  for (const [key, value] of Object.entries(draft.changes)) {
    if (key === 'radarr' || key === 'sonarr') {
      fields.push(...Object.keys(value as object).map((field) => `${key}.${field}`))
    } else {
      fields.push(key)
    }
  }
  return [...fields, ...Object.keys(draft.secrets)]
}

export const isEdited = (draft: Draft, path: string) => changedFields(draft).includes(path)

export function toPatch(draft: Draft): SettingsPatch {
  return { changes: draft.changes, secrets: draft.secrets }
}

// --- LLM: o campo mostra o padrão do provedor quando nada foi escolhido ---

function provider(view: SettingsView, id: string) {
  return view.options.llm_providers.find((p) => p.id === id) ?? view.options.llm_providers[0]
}

export function llmProvider(draft: Draft, view: SettingsView) {
  return provider(view, fieldValue(draft, view.config, 'llm_provider'))
}

export function llmModelShown(draft: Draft, view: SettingsView): string {
  return fieldValue(draft, view.config, 'llm_model') || llmProvider(draft, view).default_model
}

/** Modelo igual ao padrão do provedor vale como "nenhum": acompanha o padrão se ele mudar. */
export function setLlmModel(draft: Draft, view: SettingsView, model: string): Draft {
  const text = model.trim() === llmProvider(draft, view).default_model ? '' : model
  return setField(draft, view.config, 'llm_model', text)
}

export function llmUrlShown(draft: Draft, view: SettingsView): string {
  return fieldValue(draft, view.config, 'llm_base_url') || provider(view, 'custom').default_url
}

export function setLlmUrl(draft: Draft, view: SettingsView, url: string): Draft {
  const text = url.trim() === provider(view, 'custom').default_url ? '' : url
  return setField(draft, view.config, 'llm_base_url', text)
}

/** Trocar de provedor leva o modelo padrão do novo, a não ser que um tenha sido escolhido. */
export function setLlmProvider(draft: Draft, view: SettingsView, id: string): Draft {
  return setField(draft, view.config, 'llm_provider', id)
}

/** `360` → `6 h`; `90` → `1 h 30 min`. */
export function humanMinutes(minutes: number): string {
  if (!Number.isFinite(minutes) || minutes < 60) return `${minutes || 0} min`
  const rest = minutes % 60
  const hours = Math.floor(minutes / 60)
  if (hours >= 24 && minutes % 1440 === 0) return `${minutes / 1440} d`
  return rest ? `${hours} h ${rest} min` : `${hours} h`
}
