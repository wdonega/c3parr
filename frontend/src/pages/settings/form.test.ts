import { describe, expect, it } from 'vitest'
import type { AppConfig, ArrConfig, SettingsView } from '../../api/types'
import {
  arrValue,
  changedFields,
  EMPTY,
  fieldValue,
  humanMinutes,
  isEdited,
  llmModelShown,
  llmUrlShown,
  setArrField,
  setField,
  setLlmModel,
  setLlmProvider,
  setLlmUrl,
  setSecret,
  toPatch,
} from './form'

const arr: ArrConfig = {
  enabled: false, host: '', port: 7878, base_url: '', ssl: false, timeout_s: 60,
  only_monitored: false, excluded_tags: [], excluded_series_types: [], exclude_season_zero: false,
  path_mappings: [],
}
const config = {
  radarr: arr,
  sonarr: { ...arr, port: 8989 },
  wait_minutes: 60,
  target_languages: ['pt-BR'],
  llm_provider: 'custom',
  llm_model: '',
  llm_base_url: '',
  forced_resolves: false,
} as unknown as AppConfig
const provider = (id: string, default_model: string, default_url = '') => ({
  id, label: id, site: '', default_url, default_model, key: `${id.toUpperCase()}_API_KEY`, effort: false,
})
const view = {
  config,
  options: {
    llm_providers: [provider('custom', 'qwen-env', 'http://box:8080/v1'), provider('openai', 'gpt-4.1-mini')],
  },
} as unknown as SettingsView

describe('rascunho de Settings', () => {
  it('guarda só o que mudou, e voltar ao valor salvo tira do rascunho', () => {
    let draft = setField(EMPTY, config, 'wait_minutes', 30)
    draft = setField(draft, config, 'forced_resolves', true)
    expect(changedFields(draft)).toEqual(['wait_minutes', 'forced_resolves'])
    expect(fieldValue(draft, config, 'wait_minutes')).toBe(30)

    draft = setField(draft, config, 'wait_minutes', 60)
    expect(changedFields(draft)).toEqual(['forced_resolves'])
    expect(fieldValue(draft, config, 'wait_minutes')).toBe(60)
    expect(setField(draft, config, 'forced_resolves', false)).toEqual(EMPTY)
  })

  it('compara listas pelo conteúdo', () => {
    const draft = setField(EMPTY, config, 'target_languages', ['pt-BR', 'es'])
    expect(isEdited(draft, 'target_languages')).toBe(true)
    expect(setField(draft, config, 'target_languages', ['pt-BR'])).toEqual(EMPTY)
  })

  it('manda do Radarr e do Sonarr só os campos mexidos', () => {
    let draft = setArrField(EMPTY, config, 'radarr', 'host', 'radarr.test')
    draft = setArrField(draft, config, 'radarr', 'enabled', true)
    draft = setArrField(draft, config, 'sonarr', 'port', null)
    expect(toPatch(draft).changes).toEqual({
      radarr: { host: 'radarr.test', enabled: true },
      sonarr: { port: null },
    })
    expect(changedFields(draft)).toEqual(['radarr.host', 'radarr.enabled', 'sonarr.port'])
    expect(arrValue(draft, config, 'radarr', 'host')).toBe('radarr.test')
    expect(arrValue(draft, config, 'sonarr', 'host')).toBe('')

    draft = setArrField(draft, config, 'sonarr', 'port', 8989) // voltou: o Sonarr sai inteiro
    expect(Object.keys(draft.changes)).toEqual(['radarr'])
  })

  it('trata chave digitada, campo esvaziado e pedido de apagar', () => {
    let draft = setSecret(EMPTY, 'RADARR_API_KEY', 'rk')
    expect(toPatch(draft).secrets).toEqual({ RADARR_API_KEY: 'rk' })
    expect(isEdited(draft, 'RADARR_API_KEY')).toBe(true)

    draft = setSecret(draft, 'RADARR_API_KEY', '') // apagou o que digitou: não mexe na salva
    expect(draft).toEqual(EMPTY)
    draft = setSecret(draft, 'RADARR_API_KEY', null) // botão Remove: apaga a salva
    expect(toPatch(draft).secrets).toEqual({ RADARR_API_KEY: null })
    expect(changedFields(draft)).toEqual(['RADARR_API_KEY'])
  })
})

describe('campos do LLM', () => {
  it('mostra o padrão do provedor, e digitar o padrão não conta como alteração', () => {
    expect(llmModelShown(EMPTY, view)).toBe('qwen-env')
    expect(llmUrlShown(EMPTY, view)).toBe('http://box:8080/v1')
    expect(setLlmModel(EMPTY, view, 'qwen-env')).toEqual(EMPTY)
    expect(setLlmUrl(EMPTY, view, 'http://box:8080/v1')).toEqual(EMPTY)

    const draft = setLlmModel(EMPTY, view, 'my-model')
    expect(llmModelShown(draft, view)).toBe('my-model')
    expect(toPatch(draft).changes).toEqual({ llm_model: 'my-model' })
  })

  it('trocar de provedor leva o padrão do novo, e modelo escolhido à mão fica', () => {
    const switched = setLlmProvider(EMPTY, view, 'openai')
    expect(llmModelShown(switched, view)).toBe('gpt-4.1-mini')
    expect(toPatch(switched).changes).toEqual({ llm_provider: 'openai' })
    expect(setLlmModel(switched, view, 'gpt-4.1-mini')).toEqual(switched)

    const chosen = setLlmProvider(setLlmModel(EMPTY, view, 'my-model'), view, 'openai')
    expect(llmModelShown(chosen, view)).toBe('my-model')
  })
})

it('escreve minutos como gente lê', () => {
  expect([0, 45, 60, 90, 360, 1440, 2880, 1500].map(humanMinutes)).toEqual([
    '0 min', '45 min', '1 h', '1 h 30 min', '6 h', '1 d', '2 d', '25 h',
  ])
})
