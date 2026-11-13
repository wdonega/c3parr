// As cinco abas de Settings. Cada seção tem o que é e o estado à esquerda, os campos à direita.
import { useState, type ReactNode } from 'react'
import { Link, NavLink } from 'react-router-dom'
import type { AppConfig, TestRecord, TestResult } from '../../api/types'
import { decimal, languageName, maybe, rich, said, t } from '../../i18n'
import { cleanBase, withBase } from '../../lib/base'
import { timeAgo } from '../../lib/format'
import { Field, Segmented, SwitchRow } from '../../ui/controls'
import { Icon } from '../../ui/Icon'
import {
  humanMinutes,
  llmModelShown,
  llmProvider,
  llmUrlShown,
  locksOut,
  setLlmModel,
  setLlmProvider,
  setLlmUrl,
  type ArrKind,
} from './form'
import type { Form } from './SettingsPage'

type Tab = { form: Form }
type BoolKey = { [K in keyof AppConfig]: AppConfig[K] extends boolean ? K : never }[keyof AppConfig]
type SubtitleFlag = Exclude<BoolKey, 'monitor_new'>

function Section({ title, about, state, children }: { title: string; about?: ReactNode; state?: ReactNode; children: ReactNode }) {
  return (
    <section className="card form-section" aria-label={title}>
      <div className="form-intro">
        <h2>{title}</h2>
        {about && <p>{about}</p>}
        {state}
      </div>
      <div className="form-fields">{children}</div>
    </section>
  )
}

function Text({
  label,
  value,
  onChange,
  edited,
  hint,
  code,
  short,
  type = 'text',
  placeholder,
  optional,
  min,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  edited?: boolean
  hint?: ReactNode
  code?: boolean
  short?: boolean
  type?: 'text' | 'url' | 'number'
  placeholder?: string
  optional?: boolean
  min?: number
}) {
  const classes = ['input', code && 'code', short && 'short', edited && 'changed'].filter(Boolean).join(' ')
  return (
    <Field label={label} hint={hint} edited={edited} optional={optional}>
      <input
        className={classes}
        type={type}
        min={min}
        value={value}
        placeholder={placeholder}
        autoComplete="off"
        spellCheck={false}
        onChange={(event) => onChange(event.target.value)}
      />
    </Field>
  )
}

/** Uma chave: o valor nunca vem do servidor, só de onde ela vem. Digitar troca; vazio mantém. */
function Secret({ form, name, label, hint, optional }: Tab & { name: string; label: string; hint?: ReactNode; optional?: boolean }) {
  const [shown, setShown] = useState(false)
  const typed = form.secret(name)
  const source = form.view.secrets[name]
  const removing = typed === null
  const placeholder = removing
    ? t('secret.removing')
    : source === 'settings'
      ? t('secret.saved')
      : source === 'environment'
        ? t('secret.environment')
        : t('secret.notSet')
  return (
    <Field label={label} hint={hint} edited={typed !== undefined} optional={optional}>
      <span className="input-row">
        <input
          className={`input ${typed !== undefined ? 'changed' : ''}`}
          type={shown ? 'text' : 'password'}
          value={typed ?? ''}
          placeholder={placeholder}
          autoComplete="off"
          spellCheck={false}
          onChange={(event) => form.setSecret(name, event.target.value)}
        />
        <button type="button" className="btn" onClick={() => setShown(!shown)}>
          {shown ? t('secret.hide') : t('secret.show')}
        </button>
        {source === 'settings' && (
          <button type="button" className="btn" onClick={() => form.setSecret(name, removing ? '' : null)}>
            {removing ? t('secret.keep') : t('secret.remove')}
          </button>
        )}
      </span>
    </Field>
  )
}

function Flag({ form, name, label, help, child, off }: Tab & { name: BoolKey; label: string; help?: ReactNode; child?: boolean; off?: boolean }) {
  return (
    <SwitchRow
      name={label}
      help={help}
      child={child}
      disabled={off}
      checked={form.value(name)}
      onChange={(checked) => form.set(name, checked)}
    />
  )
}

/** Botão de teste com o resultado ao lado: o de agora, ou o último que ainda vale. */
function TestRow({ form, target, extra }: Tab & { target: string; extra?: ReactNode }) {
  const now: TestResult | undefined = form.tested[target]
  const kept: TestRecord | undefined = form.view.tests[target]
  const busy = form.testing === target
  const dirty = Object.keys(form.draft.changes).length + Object.keys(form.draft.secrets).length > 0
  let result: ReactNode = <span className="muted">{t('test.notYet')}</span>
  if (now) {
    result = (
      <span className={now.ok ? 'ok' : 'bad'} role="status">
        {said(now)}
        {now.ok && now.ms !== null ? t('test.answered', { seconds: decimal(now.ms / 1000) }) : ''}
      </span>
    )
  } else if (kept && !dirty) {
    result = (
      <span className={kept.ok ? 'ok' : 'bad'}>
        {said(kept)} <span className="muted">{t('test.tested', { when: timeAgo(kept.at) })}</span>
      </span>
    )
  } else if (dirty) {
    result = <span className="muted">{t('test.stale')}</span>
  }
  return (
    <div className="test-row">
      <button type="button" className="btn" disabled={busy} onClick={() => form.test(target)}>
        {busy && <Icon name="spinner" size={14} className="spin" />}
        {t('test.button')}
      </button>
      {result}
      {extra}
    </div>
  )
}

function StateLine({ tone, children }: { tone: 'ok' | 'warn' | 'muted'; children: ReactNode }) {
  return (
    <span className={tone} style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginTop: 4, fontSize: 13 }}>
      <span className="dot" style={{ marginTop: 6 }} />
      {children}
    </span>
  )
}

function Minutes({ form, name, label, hint, min }: Tab & { name: 'wait_minutes' | 'search_interval_minutes' | 'sync_interval_minutes'; label: string; hint?: string; min: number }) {
  const value = form.value(name)
  return (
    <Text
      label={label}
      type="number"
      min={min}
      short
      value={String(value)}
      edited={form.edited(name)}
      hint={`${humanMinutes(Number(value))}${hint ? `. ${hint}` : ''}`}
      onChange={(text) => form.set(name, Number(text))}
    />
  )
}

// --- Library ---

/** Lista separada por vírgulas, guardando o texto digitado para a vírgula não "sumir". */
function Tags({ label, value, onChange, edited, hint }: { label: string; value: string[]; onChange: (value: string[]) => void; edited: boolean; hint: ReactNode }) {
  const [text, setText] = useState(value.join(', '))
  const parsed = (raw: string) => raw.split(',').map((t) => t.trim()).filter(Boolean)
  const shown = JSON.stringify(parsed(text)) === JSON.stringify(value) ? text : value.join(', ')
  return (
    <Text
      label={label}
      value={shown}
      edited={edited}
      hint={hint}
      optional
      onChange={(raw) => {
        setText(raw)
        onChange(parsed(raw))
      }}
    />
  )
}

export function LibraryTab({ form, kind }: Tab & { kind: ArrKind }) {
  const name = kind === 'radarr' ? 'Radarr' : 'Sonarr'
  const edited = (field: string) => form.edited(`${kind}.${field}`)
  const mappings = form.arr(kind, 'path_mappings')
  const setMappings = (next: [string, string][]) => form.setArr(kind, 'path_mappings', next)
  const key = form.view.options.arr_keys[kind]
  const types = form.arr(kind, 'excluded_series_types')
  const enabled = (which: ArrKind) => form.arr(which, 'enabled')

  return (
    <>
      <nav className="chips" aria-label={t('lib.which')}>
        {(['radarr', 'sonarr'] as const).map((which) => (
          <NavLink key={which} to={`/settings/library/${which}`} className="chip" aria-pressed={which === kind}>
            <span className={`dot ${enabled(which) ? 'ok' : 'muted'}`} />
            {which === 'radarr' ? 'Radarr' : 'Sonarr'}
          </NavLink>
        ))}
      </nav>

      <Section
        title={t('lib.connect', { name })}
        about={t(`lib.about.${kind}`, { name, interval: humanMinutes(form.value('sync_interval_minutes')) })}
        state={
          enabled(kind) ? (
            <StateLine tone="ok">{t('lib.inUse')}</StateLine>
          ) : (
            <StateLine tone="muted">{t('lib.notInUse')}</StateLine>
          )
        }
      >
        <SwitchRow
          name={t('lib.use', { name })}
          checked={form.arr(kind, 'enabled')}
          onChange={(checked) => form.setArr(kind, 'enabled', checked)}
        />
        <Text
          label={t('lib.address')}
          value={form.arr(kind, 'host')}
          edited={edited('host')}
          code
          hint={t('lib.addressHint', { kind })}
          onChange={(value) => form.setArr(kind, 'host', value)}
        />
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16 }}>
          <Text
            label={t('lib.port')}
            type="number"
            min={1}
            short
            value={form.arr(kind, 'port') === null ? '' : String(form.arr(kind, 'port'))}
            edited={edited('port')}
            onChange={(value) => form.setArr(kind, 'port', value === '' ? null : Number(value))}
          />
          <Text
            label={t('lib.baseUrl')}
            optional
            code
            value={form.arr(kind, 'base_url')}
            edited={edited('base_url')}
            onChange={(value) => form.setArr(kind, 'base_url', value)}
          />
          <Field label={t('lib.timeout')} edited={edited('timeout_s')}>
            <select
              className="input short"
              value={form.arr(kind, 'timeout_s')}
              onChange={(event) => form.setArr(kind, 'timeout_s', Number(event.target.value))}
            >
              {[30, 60, 120, 300].map((seconds) => (
                <option key={seconds} value={seconds}>
                  {seconds} s
                </option>
              ))}
            </select>
          </Field>
        </div>
        <Secret form={form} name={key} label={t('lib.apiKey')} hint={t('lib.apiKeyHint', { name })} />
        <SwitchRow
          name={t('lib.https')}
          help={t('lib.httpsHint')}
          checked={form.arr(kind, 'ssl')}
          onChange={(checked) => form.setArr(kind, 'ssl', checked)}
        />
        <TestRow form={form} target={kind} />
      </Section>

      <Section title={t('lib.comesIn')} about={t('lib.comesInAbout', { name })}>
        <SwitchRow
          name={t(`lib.onlyMonitored.${kind}`)}
          help={t('lib.onlyMonitoredHint', { name })}
          checked={form.arr(kind, 'only_monitored')}
          onChange={(checked) => form.setArr(kind, 'only_monitored', checked)}
        />
        <Tags
          key={kind}
          label={t('lib.excludedTags')}
          value={form.arr(kind, 'excluded_tags')}
          edited={edited('excluded_tags')}
          hint={t('lib.excludedTagsHint', { name })}
          onChange={(value) => form.setArr(kind, 'excluded_tags', value)}
        />
        {kind === 'sonarr' && (
          <>
            <div className="field">
              <span className="label">{t('lib.excludedTypes')}</span>
              <div className="chips">
                {form.view.options.series_types.map((type) => (
                  <button
                    key={type}
                    type="button"
                    className="chip"
                    aria-pressed={types.includes(type)}
                    onClick={() =>
                      form.setArr(
                        'sonarr',
                        'excluded_series_types',
                        types.includes(type) ? types.filter((t) => t !== type) : [...types, type],
                      )
                    }
                  >
                    {type}
                  </button>
                ))}
              </div>
            </div>
            <SwitchRow
              name={t('lib.seasonZero')}
              help={t('lib.seasonZeroHint')}
              checked={form.arr('sonarr', 'exclude_season_zero')}
              onChange={(checked) => form.setArr('sonarr', 'exclude_season_zero', checked)}
            />
          </>
        )}
      </Section>

      <Section
        title={t('lib.mappings')}
        about={t('lib.mappingsAbout', { name })}
      >
        {mappings.map(([remote, local], index) => (
          <div key={index} className="map-row">
            <input
              className="input code"
              aria-label={t('lib.remotePathN', { name, n: index + 1 })}
              placeholder={t('lib.remotePath', { name })}
              value={remote}
              onChange={(event) => setMappings(mappings.map((pair, i) => (i === index ? [event.target.value, pair[1]] : pair)))}
            />
            <Icon name="right" className="muted" />
            <input
              className="input code"
              aria-label={t('lib.localPathN', { n: index + 1 })}
              placeholder={t('lib.localPath')}
              value={local}
              onChange={(event) => setMappings(mappings.map((pair, i) => (i === index ? [pair[0], event.target.value] : pair)))}
            />
            <button
              type="button"
              className="icon-btn outlined"
              aria-label={t('lib.removeMapping', { n: index + 1 })}
              onClick={() => setMappings(mappings.filter((_, i) => i !== index))}
            >
              <Icon name="trash" size={14} />
            </button>
          </div>
        ))}
        <div>
          <button type="button" className="btn" onClick={() => setMappings([...mappings, ['', '']])}>
            <Icon name="plus" size={14} />
            {t('lib.addMapping')}
          </button>
        </div>
      </Section>

      <Section title={t('lib.both')} about={t('lib.bothAbout')}>
        <Flag form={form} name="monitor_new" label={t('lib.monitorNew')} help={t('lib.monitorNewHint')} />
        <Minutes form={form} name="sync_interval_minutes" label={t('lib.syncEvery')} min={5} />
      </Section>
    </>
  )
}

// --- Integrations ---

export function IntegrationsTab({ form }: Tab) {
  const { view, draft } = form
  const provider = llmProvider(draft, view)
  const custom = provider.id === 'custom'
  const mode = form.value('asr_mode')
  const remote = mode !== 'local'
  const secrets = view.secrets
  const llmReady = custom ? !!llmUrlShown(draft, view) && !!llmModelShown(draft, view) : secrets[provider.key] !== ''
  const tmdbSource = secrets.TMDB_READ_ACCESS_TOKEN

  return (
    <>
      <Section
        title={t('int.llm')}
        about={t('int.llmAbout')}
        state={
          llmReady ? (
            <StateLine tone="ok">{t('int.configured')}</StateLine>
          ) : (
            <StateLine tone="warn">{custom ? t('int.needsUrl') : t('int.needsKey')}</StateLine>
          )
        }
      >
        <Field
          label={t('int.provider')}
          edited={form.edited('llm_provider')}
          hint={
            provider.site && (
              <a href={provider.site} target="_blank" rel="noopener">
                {custom ? t('int.noServer') : t('int.getKey', { host: new URL(provider.site).host })}
              </a>
            )
          }
        >
          <select
            className="input"
            value={provider.id}
            onChange={(event) => form.update((d) => setLlmProvider(d, view, event.target.value))}
          >
            {view.options.llm_providers.map((option) => (
              <option key={option.id} value={option.id}>
                {maybe(`llm.${option.id}`, option.label)}
              </option>
            ))}
          </select>
        </Field>
        {custom && (
          <Text
            label={t('int.serverUrl')}
            type="url"
            code
            value={llmUrlShown(draft, view)}
            edited={form.edited('llm_base_url')}
            hint={
              t('int.urlHint') +
              (view.shown.llm_url_from_env && !form.edited('llm_base_url') ? `. ${t('int.fromEnv')}` : '')
            }
            onChange={(value) => form.update((d) => setLlmUrl(d, view, value))}
          />
        )}
        <Text
          label={t('int.model')}
          code
          value={llmModelShown(draft, view)}
          edited={form.edited('llm_model')}
          hint={
            form.value('llm_model')
              ? t('int.modelDefaultIs', { model: provider.default_model || t('int.notSet') })
              : custom && view.shown.llm_model_from_env
                ? t('int.fromEnv')
                : t('int.modelDefault')
          }
          onChange={(value) => form.update((d) => setLlmModel(d, view, value))}
        />
        {provider.effort && (
          <Field label={t('int.effort')} edited={form.edited('llm_effort')} hint={t('int.effortHint')}>
            <Segmented
              label={t('int.effort')}
              options={[
                { value: 'low', label: t('int.effort.low') },
                { value: 'medium', label: t('int.effort.medium') },
              ]}
              value={form.value('llm_effort')}
              onChange={(value) => form.set('llm_effort', value)}
            />
          </Field>
        )}
        <Secret form={form} name={provider.key} label={t('lib.apiKey')} optional={custom} />
        <TestRow form={form} target="llm" />
      </Section>

      <Section
        title={t('int.asr')}
        about={t('int.asrAbout')}
        state={
          remote ? (
            <StateLine tone="ok">{t('int.asrRemote')}</StateLine>
          ) : (
            <StateLine tone="muted">{t('int.asrLocal')}</StateLine>
          )
        }
      >
        <Field label={t('int.where')} edited={form.edited('asr_mode')}>
          <Segmented
            label={t('int.where')}
            options={[
              { value: 'local', label: t('int.mode.local') },
              { value: 'openai', label: t('int.mode.openai') },
              { value: 'asr-webservice', label: 'whisper-asr-webservice' },
            ]}
            value={mode}
            onChange={(value) => form.set('asr_mode', value)}
          />
        </Field>
        <p className="hint muted">
          {mode === 'local' &&
            rich('int.localHint', { engines: (text) => <Link to="/settings/engines">{text}</Link> })}
          {mode === 'openai' && t('int.openaiHint')}
          {mode === 'asr-webservice' && t('int.webserviceHint')}
        </p>
        {remote && (
          <>
            <Text
              label={t('int.serverUrl')}
              type="url"
              code
              value={form.value('asr_url')}
              edited={form.edited('asr_url')}
              onChange={(value) => form.set('asr_url', value)}
            />
            {mode === 'openai' && (
              <Text
                label={t('int.model')}
                code
                optional
                placeholder="whisper-1"
                value={form.value('asr_model')}
                edited={form.edited('asr_model')}
                onChange={(value) => form.set('asr_model', value)}
              />
            )}
            <Secret form={form} name="C3PARR_ASR_API_KEY" label={t('lib.apiKey')} optional />
          </>
        )}
        <TestRow form={form} target="asr" />
      </Section>

      <Section
        title="TMDb"
        about={t('int.tmdbAbout')}
        state={
          tmdbSource !== '' ? (
            <StateLine tone="ok">{t('int.tmdbYours')}</StateLine>
          ) : view.options.tmdb_builtin ? (
            <StateLine tone="ok">{t('int.tmdbBuiltin')}</StateLine>
          ) : (
            <StateLine tone="warn">{t('int.tmdbNeeds')}</StateLine>
          )
        }
      >
        <Secret
          form={form}
          name="TMDB_READ_ACCESS_TOKEN"
          label={t('int.tmdbToken')}
          optional={view.options.tmdb_builtin}
          hint={t('int.tmdbHint')}
        />
        <TestRow form={form} target="tmdb" />
        <p className="hint muted" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <img src={withBase('/tmdb.svg')} alt="TMDB" style={{ height: 12 }} />
          {view.options.tmdb_attribution}
        </p>
      </Section>
    </>
  )
}

// --- Languages ---

function LanguageList({ form, name, label }: Tab & { name: 'target_languages' | 'fallback_languages'; label: string }) {
  const chosen = form.value(name)
  const names = new Map(form.view.options.languages.map((l) => [l.code, languageName(l.code, l.name)]))
  const free = form.view.options.languages.filter((l) => !chosen.includes(l.code))
  const move = (code: string) => form.set(name, [code, ...chosen.filter((c) => c !== code)])
  return (
    <>
      <div className="chips" role="list" aria-label={label}>
        {chosen.map((code, index) => (
          <span key={code} className="lang-chip" role="listitem" data-code={code}>
            {index === 0 && <span className="edited">{t('lang.first')}</span>}
            {names.get(code) ?? code} <span className="mono muted">{code}</span>
            {index > 0 && (
              <button type="button" className="icon-btn" aria-label={t('lang.makeFirst', { code })} onClick={() => move(code)}>
                <Icon name="up" size={13} />
              </button>
            )}
            <button
              type="button"
              className="icon-btn"
              aria-label={t('lang.remove', { code })}
              onClick={() => form.set(name, chosen.filter((c) => c !== code))}
            >
              <Icon name="close" size={13} />
            </button>
          </span>
        ))}
        {chosen.length === 0 && <span className="muted">{t('lang.none')}</span>}
      </div>
      <Field label={t('lang.add')} edited={form.edited(name)}>
        <select
          className="input"
          value=""
          onChange={(event) => event.target.value && form.set(name, [...chosen, event.target.value])}
        >
          <option value="">{t('lang.pick')}</option>
          {free.map((language) => (
            <option key={language.code} value={language.code}>
              {languageName(language.code, language.name)} ({language.code})
            </option>
          ))}
        </select>
      </Field>
    </>
  )
}

export function LanguagesTab({ form }: Tab) {
  return (
    <>
      <Section
        title={t('lang.targets')}
        about={t('lang.targetsAbout')}
        state={
          form.value('target_languages').length === 0 && <StateLine tone="warn">{t('lang.pickOne')}</StateLine>
        }
      >
        <LanguageList form={form} name="target_languages" label={t('lang.targets')} />
      </Section>
      <Section
        title={t('lang.fallbacks')}
        about={t('lang.fallbacksAbout')}
      >
        <LanguageList form={form} name="fallback_languages" label={t('lang.fallbacks')} />
      </Section>
    </>
  )
}

// --- Engines ---

export function EnginesTab({ form }: Tab) {
  const chosen = form.value('components')
  return (
    <Section title={t('eng.title')} about={t('eng.about')}>
      {form.view.options.engines.map((engine) => {
        const selected = chosen[engine.id] ?? engine.selected
        const known = engine.providers.some((p) => p.id === selected)
        const picked = engine.providers.find((p) => p.id === selected)
        return (
          <Field
            key={engine.id}
            label={maybe(`engine.${engine.id}`, engine.label)}
            edited={form.edited('components')}
            hint={picked && maybe(`provider.${picked.id}`, picked.description)}
          >
            <select
              className="input"
              value={selected}
              onChange={(event) => form.set('components', { ...chosen, [engine.id]: event.target.value })}
            >
              {!known && <option value={selected}>{selected}</option>}
              {engine.providers.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.id}
                </option>
              ))}
            </select>
          </Field>
        )
      })}
    </Section>
  )
}

// --- Subtitles ---

export function SubtitlesTab({ form }: Tab) {
  const syncs = form.value('sync_new_external')
  const transcribes = form.value('asr_enabled')
  const concerts = form.value('concert_mode') !== 'off'
  // `off` vem para as opções que dependem de outra: ficam recuadas e apagadas quando ela está desligada
  const flag = (name: SubtitleFlag, off?: boolean) => (
    <Flag
      form={form}
      name={name}
      label={t(`subs.${name}`)}
      help={t(`subs.${name}.help`)}
      child={off !== undefined}
      off={off}
    />
  )
  return (
    <>
      <Section title={t('subs.sources')} about={t('subs.sourcesAbout')}>
        {flag('use_embedded')}
        {flag('embedded_resolves')}
        {flag('external_resolves')}
        {flag('forced_resolves')}
      </Section>

      <Section title={t('subs.new')} about={t('subs.newAbout')}>
        {flag('sync_new_external')}
        {flag('fix_external_in_place', !syncs)}
        {flag('delete_ours_on_human')}
        {flag('new_external_becomes_main')}
        <Field label={t('subs.mainChange')} edited={form.edited('derived_on_main_change')}>
          <select
            className="input"
            value={form.value('derived_on_main_change')}
            onChange={(event) => form.set('derived_on_main_change', event.target.value as AppConfig['derived_on_main_change'])}
          >
            <option value="regenerate">{t('subs.mainChange.regenerate')}</option>
            <option value="mark">{t('subs.mainChange.mark')}</option>
            <option value="keep">{t('subs.mainChange.keep')}</option>
          </select>
        </Field>
      </Section>

      <Section title={t('subs.asr')}>
        {flag('asr_enabled')}
        {flag('asr_always', !transcribes)}
        <Field label={t('subs.foreign')} edited={form.edited('foreign_tag')} hint={t('subs.foreignHint')}>
          <select
            className="input"
            value={form.value('foreign_tag')}
            onChange={(event) => form.set('foreign_tag', event.target.value as AppConfig['foreign_tag'])}
          >
            <option value="first">{t('subs.foreign.first')}</option>
            <option value="every">{t('subs.foreign.every')}</option>
            <option value="off">{t('subs.foreign.off')}</option>
          </select>
        </Field>
      </Section>

      <Section title={t('subs.concerts')} about={t('subs.concertsAbout')}>
        <Field label={t('subs.concertMode')} edited={form.edited('concert_mode')} hint={t('subs.concertModeHint')}>
          <select
            className="input"
            value={form.value('concert_mode')}
            onChange={(event) => form.set('concert_mode', event.target.value as AppConfig['concert_mode'])}
          >
            <option value="off">{t('subs.concertMode.off')}</option>
            <option value="manual">{t('subs.concertMode.manual')}</option>
            <option value="auto">{t('subs.concertMode.auto')}</option>
          </select>
        </Field>
        <Tags
          label={t('subs.concertPaths')}
          value={form.value('concert_paths')}
          edited={form.edited('concert_paths')}
          hint={t('subs.concertPathsHint')}
          onChange={(value) => form.set('concert_paths', value)}
        />
        {flag('concert_lyrics', !concerts)}
        {flag('concert_publish_original', !concerts)}
      </Section>

      <Section
        title={t('subs.automation')}
        about={t('subs.automationAbout')}
      >
        <Minutes form={form} name="wait_minutes" label={t('subs.wait')} min={0} />
        <Minutes form={form} name="search_interval_minutes" label={t('subs.searchEvery')} min={5} />
      </Section>
    </>
  )
}

// --- General ---

export function GeneralTab({ form }: Tab) {
  const { listening } = form.view
  const typed = form.value('url_base')
  const base = cleanBase(typed) || listening.url_base_from_env
  const where = `${window.location.origin}${base}/`
  return (
    <Section
      title={t('gen.host')}
      about={t('gen.about')}
      state={
        <StateLine tone="muted">
          {t('gen.listening', { address: `${listening.address}:${listening.port}` })}
        </StateLine>
      }
    >
      <Field label={t('gen.bind')} hint={t('gen.bindHint')}>
        <input className="input code short" value={`${listening.address}:${listening.port}`} readOnly />
      </Field>
      <Text
        label={t('gen.urlBase')}
        optional
        code
        placeholder="/c3parr"
        value={typed}
        edited={form.edited('url_base')}
        hint={rich(
          'gen.urlBaseHint',
          { mono: (text) => <span className="mono">{text}</span> },
          {
            where,
            from: !typed && listening.url_base_from_env ? t('gen.fromEnv', { name: 'C3PARR_URL_BASE' }) : '',
          },
        )}
        onChange={(value) => form.set('url_base', value)}
      />
      <Tags
        label={t('gen.allowed')}
        value={form.value('allowed_hosts')}
        edited={form.edited('allowed_hosts')}
        hint={
          <>
            {t('gen.allowedHint')}
            {locksOut(form.value('allowed_hosts'), window.location.hostname) && (
              <span className="warn" role="alert" style={{ display: 'block', marginTop: 6 }}>
                {t('gen.lockout', { host: window.location.hostname })}
              </span>
            )}
          </>
        }
        onChange={(value) => form.set('allowed_hosts', value)}
      />
      <Text
        label={t('gen.name')}
        value={form.value('instance_name')}
        edited={form.edited('instance_name')}
        hint={t('gen.nameHint')}
        onChange={(value) => form.set('instance_name', value)}
      />
    </Section>
  )
}
