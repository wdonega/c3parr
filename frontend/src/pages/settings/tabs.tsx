// As cinco abas de Settings. Cada seção tem o que é e o estado à esquerda, os campos à direita.
import { useState, type ReactNode } from 'react'
import { Link, NavLink } from 'react-router-dom'
import type { AppConfig, TestRecord, TestResult } from '../../api/types'
import { cleanBase, withBase } from '../../lib/base'
import { timeAgo } from '../../lib/format'
import { Field, Segmented, SwitchRow } from '../../ui/controls'
import { Icon } from '../../ui/Icon'
import {
  humanMinutes,
  llmModelShown,
  llmProvider,
  llmUrlShown,
  setLlmModel,
  setLlmProvider,
  setLlmUrl,
  type ArrKind,
} from './form'
import type { Form } from './SettingsPage'

type Tab = { form: Form }
type BoolKey = { [K in keyof AppConfig]: AppConfig[K] extends boolean ? K : never }[keyof AppConfig]

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
    ? 'Will be removed when you save'
    : source === 'settings'
      ? 'Saved. Type to replace it'
      : source === 'environment'
        ? 'From the environment (.env). Type to use another'
        : 'Not set'
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
          {shown ? 'Hide' : 'Show'}
        </button>
        {source === 'settings' && (
          <button type="button" className="btn" onClick={() => form.setSecret(name, removing ? '' : null)}>
            {removing ? 'Keep' : 'Remove'}
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
  let result: ReactNode = <span className="muted">Not tested yet</span>
  if (now) {
    result = (
      <span className={now.ok ? 'ok' : 'bad'} role="status">
        {now.message}
        {now.ok && now.ms !== null ? ` Answered in ${(now.ms / 1000).toFixed(1)} s.` : ''}
      </span>
    )
  } else if (kept && !dirty) {
    result = (
      <span className={kept.ok ? 'ok' : 'bad'}>
        {kept.message} <span className="muted">Tested {timeAgo(kept.at)}.</span>
      </span>
    )
  } else if (dirty) {
    result = <span className="muted">Not tested with what is on screen</span>
  }
  return (
    <div className="test-row">
      <button type="button" className="btn" disabled={busy} onClick={() => form.test(target)}>
        {busy && <Icon name="spinner" size={14} className="spin" />}
        Test connection
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
function Tags({ label, value, onChange, edited, hint }: { label: string; value: string[]; onChange: (value: string[]) => void; edited: boolean; hint: string }) {
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
  const items = kind === 'radarr' ? 'movies' : 'series'
  const edited = (field: string) => form.edited(`${kind}.${field}`)
  const mappings = form.arr(kind, 'path_mappings')
  const setMappings = (next: [string, string][]) => form.setArr(kind, 'path_mappings', next)
  const key = form.view.options.arr_keys[kind]
  const types = form.arr(kind, 'excluded_series_types')
  const enabled = (which: ArrKind) => form.arr(which, 'enabled')

  return (
    <>
      <nav className="chips" aria-label="Radarr or Sonarr">
        {(['radarr', 'sonarr'] as const).map((which) => (
          <NavLink key={which} to={`/settings/library/${which}`} className="chip" aria-pressed={which === kind}>
            <span className={`dot ${enabled(which) ? 'ok' : 'muted'}`} />
            {which === 'radarr' ? 'Radarr' : 'Sonarr'}
          </NavLink>
        ))}
      </nav>

      <Section
        title={`Connect ${name}`}
        about={`c3parr reads its ${items} from ${name}, like Bazarr does: ${name} keeps the metadata and the file names in order. It syncs every ${humanMinutes(form.value('sync_interval_minutes'))} and right after you save.`}
        state={
          enabled(kind) ? <StateLine tone="ok">In use</StateLine> : <StateLine tone="muted">Not in use</StateLine>
        }
      >
        <SwitchRow
          name={`Use ${name}`}
          checked={form.arr(kind, 'enabled')}
          onChange={(checked) => form.setArr(kind, 'enabled', checked)}
        />
        <Text
          label="Address"
          value={form.arr(kind, 'host')}
          edited={edited('host')}
          code
          hint={`Hostname or IP (${kind}, 192.168.1.10), or the full URL (https://${kind}.example.com)`}
          onChange={(value) => form.setArr(kind, 'host', value)}
        />
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16 }}>
          <Text
            label="Port"
            type="number"
            min={1}
            short
            value={form.arr(kind, 'port') === null ? '' : String(form.arr(kind, 'port'))}
            edited={edited('port')}
            onChange={(value) => form.setArr(kind, 'port', value === '' ? null : Number(value))}
          />
          <Text
            label="Base URL"
            optional
            code
            value={form.arr(kind, 'base_url')}
            edited={edited('base_url')}
            onChange={(value) => form.setArr(kind, 'base_url', value)}
          />
          <Field label="HTTP timeout" edited={edited('timeout_s')}>
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
        <Secret form={form} name={key} label="API key" hint={`In ${name}: Settings → General → API Key`} />
        <SwitchRow
          name="Use HTTPS"
          help="Ignored when the address is a full URL."
          checked={form.arr(kind, 'ssl')}
          onChange={(checked) => form.setArr(kind, 'ssl', checked)}
        />
        <TestRow form={form} target={kind} />
      </Section>

      <Section title="What comes in" about={`Filters on what c3parr takes from ${name}.`}>
        <SwitchRow
          name={`Only monitored ${kind === 'radarr' ? 'movies' : 'series and episodes'}`}
          help={`What is not monitored in ${name} stays out of c3parr.`}
          checked={form.arr(kind, 'only_monitored')}
          onChange={(checked) => form.setArr(kind, 'only_monitored', checked)}
        />
        <Tags
          key={kind}
          label="Excluded tags"
          value={form.arr(kind, 'excluded_tags')}
          edited={edited('excluded_tags')}
          hint={`Comma separated, as written in ${name} (case sensitive). What has one of them stays out.`}
          onChange={(value) => form.setArr(kind, 'excluded_tags', value)}
        />
        {kind === 'sonarr' && (
          <>
            <div className="field">
              <span className="label">Excluded series types</span>
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
              name="Leave season zero out"
              help="Specials and extras."
              checked={form.arr('sonarr', 'exclude_season_zero')}
              onChange={(checked) => form.setArr('sonarr', 'exclude_season_zero', checked)}
            />
          </>
        )}
      </Section>

      <Section
        title="Path mappings"
        about={`Only when ${name} and c3parr see the same files under different paths (other host, other container volume): the start of the ${name} path is replaced by the c3parr one. If both see the same paths, leave it empty.`}
      >
        {mappings.map(([remote, local], index) => (
          <div key={index} className="map-row">
            <input
              className="input code"
              aria-label={`${name} path ${index + 1}`}
              placeholder={`${name} path`}
              value={remote}
              onChange={(event) => setMappings(mappings.map((pair, i) => (i === index ? [event.target.value, pair[1]] : pair)))}
            />
            <Icon name="right" className="muted" />
            <input
              className="input code"
              aria-label={`c3parr path ${index + 1}`}
              placeholder="c3parr path"
              value={local}
              onChange={(event) => setMappings(mappings.map((pair, i) => (i === index ? [pair[0], event.target.value] : pair)))}
            />
            <button
              type="button"
              className="icon-btn outlined"
              aria-label={`Remove mapping ${index + 1}`}
              onClick={() => setMappings(mappings.filter((_, i) => i !== index))}
            >
              <Icon name="trash" size={14} />
            </button>
          </div>
        ))}
        <div>
          <button type="button" className="btn" onClick={() => setMappings([...mappings, ['', '']])}>
            <Icon name="plus" size={14} />
            Add a mapping
          </button>
        </div>
      </Section>

      <Section title="Radarr and Sonarr" about="These two are shared by both.">
        <Flag
          form={form}
          name="monitor_new"
          label="Monitor new movies and shows"
          help="What shows up in Radarr or Sonarr gets its missing subtitles in the routine."
        />
        <Minutes form={form} name="sync_interval_minutes" label="Sync every (minutes)" min={5} />
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
        title="Translation model"
        about="Translates the subtitles and builds the bible: characters, gender, formality. A cloud provider receives the subtitle text."
        state={
          llmReady ? (
            <StateLine tone="ok">Configured</StateLine>
          ) : (
            <StateLine tone="warn">{custom ? 'Needs the server URL and a model' : 'Needs an API key'}</StateLine>
          )
        }
      >
        <Field
          label="Provider"
          edited={form.edited('llm_provider')}
          hint={
            provider.site && (
              <a href={provider.site} target="_blank" rel="noopener">
                {custom ? 'No server yet? Ollama runs one locally' : `Get an API key at ${new URL(provider.site).host}`}
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
                {option.label}
              </option>
            ))}
          </select>
        </Field>
        {custom && (
          <Text
            label="Server URL"
            type="url"
            code
            value={llmUrlShown(draft, view)}
            edited={form.edited('llm_base_url')}
            hint={`OpenAI-compatible, ends in /v1${view.shown.llm_url_from_env && !form.edited('llm_base_url') ? '. This one comes from the environment (.env)' : ''}`}
            onChange={(value) => form.update((d) => setLlmUrl(d, view, value))}
          />
        )}
        <Text
          label="Model"
          code
          value={llmModelShown(draft, view)}
          edited={form.edited('llm_model')}
          hint={
            form.value('llm_model')
              ? `The provider default is ${provider.default_model || 'not set'}`
              : custom && view.shown.llm_model_from_env
                ? 'This one comes from the environment (.env)'
                : 'The provider default. It follows the default if a new version changes it'
          }
          onChange={(value) => form.update((d) => setLlmModel(d, view, value))}
        />
        {provider.effort && (
          <Field label="Reasoning effort" edited={form.edited('llm_effort')} hint="Low costs less and translated the same in our test.">
            <Segmented
              label="Reasoning effort"
              options={[
                { value: 'low', label: 'Low' },
                { value: 'medium', label: 'Medium' },
              ]}
              value={form.value('llm_effort')}
              onChange={(value) => form.set('llm_effort', value)}
            />
          </Field>
        )}
        <Secret form={form} name={provider.key} label="API key" optional={custom} />
        <TestRow form={form} target="llm" />
      </Section>

      <Section
        title="Transcription"
        about="Turns the audio into text when there is no usable subtitle. It can run here or on a separate server: another container, a machine with a GPU."
        state={remote ? <StateLine tone="ok">On a separate server</StateLine> : <StateLine tone="muted">On this machine</StateLine>}
      >
        <Field label="Where it runs" edited={form.edited('asr_mode')}>
          <Segmented
            label="Where it runs"
            options={[
              { value: 'local', label: 'This machine' },
              { value: 'openai', label: 'OpenAI-compatible server' },
              { value: 'asr-webservice', label: 'whisper-asr-webservice' },
            ]}
            value={mode}
            onChange={(value) => form.set('asr_mode', value)}
          />
        </Field>
        <p className="hint muted">
          {mode === 'local' && (
            <>
              Uses the engine picked in <Link to="/settings/engines">Engines</Link>. On the CPU it takes about 10 min or
              more per hour of video; with a GPU build of whisper.cpp, 1 to 2 min.
            </>
          )}
          {mode === 'openai' &&
            'Any server with /v1/audio/transcriptions: whisper.cpp (whisper-server), speaches or LocalAI. The URL ends in /v1.'}
          {mode === 'asr-webservice' &&
            'The onerahmet/openai-whisper-asr-webservice image. If Bazarr already uses one, point to the same server. The URL is the server root, like http://whisper:9000.'}
        </p>
        {remote && (
          <>
            <Text
              label="Server URL"
              type="url"
              code
              value={form.value('asr_url')}
              edited={form.edited('asr_url')}
              onChange={(value) => form.set('asr_url', value)}
            />
            {mode === 'openai' && (
              <Text
                label="Model"
                code
                optional
                placeholder="whisper-1"
                value={form.value('asr_model')}
                edited={form.edited('asr_model')}
                onChange={(value) => form.set('asr_model', value)}
              />
            )}
            <Secret form={form} name="C3PARR_ASR_API_KEY" label="API key" optional />
          </>
        )}
        <TestRow form={form} target="asr" />
      </Section>

      <Section
        title="TMDb"
        about="Used in the first step of every item: the original language, the cast (right names in the translation) and season and episode numbers."
        state={
          tmdbSource !== '' ? (
            <StateLine tone="ok">Using your token</StateLine>
          ) : view.options.tmdb_builtin ? (
            <StateLine tone="ok">Using the key that ships with c3parr</StateLine>
          ) : (
            <StateLine tone="warn">Needs a read access token</StateLine>
          )
        }
      >
        <Secret
          form={form}
          name="TMDB_READ_ACCESS_TOKEN"
          label="Read access token"
          optional={view.options.tmdb_builtin}
          hint="Yours comes from themoviedb.org, under Settings → API."
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
  const names = new Map(form.view.options.languages.map((l) => [l.code, l.name]))
  const free = form.view.options.languages.filter((l) => !chosen.includes(l.code))
  const move = (code: string) => form.set(name, [code, ...chosen.filter((c) => c !== code)])
  return (
    <>
      <div className="chips" role="list" aria-label={label}>
        {chosen.map((code, index) => (
          <span key={code} className="lang-chip" role="listitem" data-code={code}>
            {index === 0 && <span className="edited">first</span>}
            {names.get(code) ?? code} <span className="mono muted">{code}</span>
            {index > 0 && (
              <button type="button" className="icon-btn" aria-label={`Make ${code} the first`} onClick={() => move(code)}>
                <Icon name="up" size={13} />
              </button>
            )}
            <button
              type="button"
              className="icon-btn"
              aria-label={`Remove ${code}`}
              onClick={() => form.set(name, chosen.filter((c) => c !== code))}
            >
              <Icon name="close" size={13} />
            </button>
          </span>
        ))}
        {chosen.length === 0 && <span className="muted">None chosen.</span>}
      </div>
      <Field label="Add a language" edited={form.edited(name)}>
        <select
          className="input"
          value=""
          onChange={(event) => event.target.value && form.set(name, [...chosen, event.target.value])}
        >
          <option value="">Pick one to add…</option>
          {free.map((language) => (
            <option key={language.code} value={language.code}>
              {language.name} ({language.code})
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
        title="Target languages"
        about="Every monitored item should end up with a subtitle in each of these. The first one is the main language."
        state={form.value('target_languages').length === 0 && <StateLine tone="warn">Pick at least one</StateLine>}
      >
        <LanguageList form={form} name="target_languages" label="Target languages" />
      </Section>
      <Section
        title="Fallback languages"
        about="When there is no subtitle in the original language, the main subtitle comes from the first of these that exists, inside the video or next to it."
      >
        <LanguageList form={form} name="fallback_languages" label="Fallback languages" />
      </Section>
    </>
  )
}

// --- Engines ---

export function EnginesTab({ form }: Tab) {
  const chosen = form.value('components')
  return (
    <Section
      title="Engines"
      about="What runs inside c3parr for each job. Transcription on a separate server is picked in Integrations."
    >
      {form.view.options.engines.map((engine) => {
        const selected = chosen[engine.id] ?? engine.selected
        const known = engine.providers.some((p) => p.id === selected)
        return (
          <Field
            key={engine.id}
            label={engine.label}
            edited={form.edited('components')}
            hint={engine.providers.find((p) => p.id === selected)?.description}
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
  return (
    <>
      <Section title="Sources" about="Which subtitles count, and which ones settle a language so c3parr makes none for it.">
        <Flag form={form} name="use_embedded" label="Use embedded subtitles" help="Text subtitles inside the video can become the main one. Image subtitles (PGS, VobSub) need OCR and are skipped." />
        <Flag form={form} name="embedded_resolves" label="An embedded subtitle covers its language" help="No subtitle of ours is made for a language the video already has." />
        <Flag form={form} name="external_resolves" label="A subtitle from another source covers its language" help="Subtitles next to the video (Bazarr, human) count. Ours is named <video>.<lang>.c3parr.srt." />
        <Flag form={form} name="forced_resolves" label="Forced subtitles cover their language" help="Forced subtitles only have signs and foreign lines. Off by default." />
      </Section>

      <Section title="When a new subtitle shows up" about="What to do when Bazarr, or you, put a subtitle next to a video.">
        <Flag form={form} name="sync_new_external" label="Sync it" help="Fixes its timing against the audio, or the main subtitle." />
        <Flag form={form} name="fix_external_in_place" label="Write the fixed timing into the file itself" help="Keeps a copy as <file>.orig.c3parr." child off={!syncs} />
        <Flag form={form} name="delete_ours_on_human" label="Delete ours in the same language" help="Off: both tracks stay." />
        <Flag form={form} name="new_external_becomes_main" label="It becomes the main subtitle when it is a better source" help="Human or embedded is better than generated from the audio." />
        <Field label="When the main subtitle changes" edited={form.edited('derived_on_main_change')}>
          <select
            className="input"
            value={form.value('derived_on_main_change')}
            onChange={(event) => form.set('derived_on_main_change', event.target.value as AppConfig['derived_on_main_change'])}
          >
            <option value="regenerate">Make the translations again</option>
            <option value="mark">Mark them as outdated</option>
            <option value="keep">Keep them</option>
          </select>
        </Field>
      </Section>

      <Section title="Transcription and translation">
        <Flag form={form} name="asr_enabled" label="Transcribe the audio when there is no subtitle" help="Without this, items with no usable subtitle are left alone." />
        <Flag form={form} name="asr_always" label="Transcribe even when a subtitle exists" help="More context for the translation, and much slower." child off={!transcribes} />
        <Field label="Mark lines in another language" edited={form.edited('foreign_tag')} hint="Like [in Arabic], when a character switches language.">
          <select
            className="input"
            value={form.value('foreign_tag')}
            onChange={(event) => form.set('foreign_tag', event.target.value as AppConfig['foreign_tag'])}
          >
            <option value="first">On the first line of each stretch</option>
            <option value="every">On every line</option>
            <option value="off">Never</option>
          </select>
        </Field>
      </Section>

      <Section
        title="Automation"
        about="A monitored file waits before the routine works on it, so a download or copy can finish first. Radarr and Sonarr imports and your own requests skip the wait and go ahead of the routine."
      >
        <Minutes form={form} name="wait_minutes" label="Wait time (minutes)" min={0} />
        <Minutes form={form} name="search_interval_minutes" label="Process the library every (minutes)" min={5} />
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
      title="Host"
      about="How c3parr is reached. This is what lets it sit behind a reverse proxy, at the root of a name or under a path."
      state={
        <StateLine tone="muted">
          Listening on {listening.address}:{listening.port}
        </StateLine>
      }
    >
      <Field
        label="Bind address and port"
        hint="Set by C3PARR_HOST and C3PARR_PORT when c3parr starts. In Docker, the compose file decides the port the network sees."
      >
        <input className="input code short" value={`${listening.address}:${listening.port}`} readOnly />
      </Field>
      <Text
        label="URL base"
        optional
        code
        placeholder="/c3parr"
        value={typed}
        edited={form.edited('url_base')}
        hint={
          <>
            For a reverse proxy that serves c3parr under a path. Empty serves it at the root. It takes effect when you
            save, with no restart, and c3parr keeps answering without it too. With this, the address is{' '}
            <span className="mono">{where}</span>
            {!typed && listening.url_base_from_env ? ' (from C3PARR_URL_BASE)' : ''}.
          </>
        }
        onChange={(value) => form.set('url_base', value)}
      />
      <Tags
        label="Allowed hosts"
        value={form.value('allowed_hosts')}
        edited={form.edited('allowed_hosts')}
        hint="Comma separated names and addresses c3parr answers to, like c3parr.example.com, 192.168.0.100. Leave empty to allow all. localhost always works."
        onChange={(value) => form.set('allowed_hosts', value)}
      />
      <Text
        label="Instance name"
        value={form.value('instance_name')}
        edited={form.edited('instance_name')}
        hint="Shown in the browser tab and as the name of the installed app."
        onChange={(value) => form.set('instance_name', value)}
      />
    </Section>
  )
}
