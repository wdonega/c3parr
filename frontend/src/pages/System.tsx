// System: rotinas, alertas, logs e status, cada um numa aba.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { Link, NavLink, useParams } from 'react-router-dom'
import { api, LIVE } from '../api/client'
import type { AlertRow, LogRow } from '../api/types'
import { dayTime, timeAgo, timeUntil } from '../lib/format'
import { NotFound, PageHead } from '../shell/Shell'
import { Chips, Empty, Loading, SearchBox } from '../ui/controls'
import { useFeedback } from '../ui/feedback'
import { Icon } from '../ui/Icon'

const PAGES = [
  ['tasks', 'Tasks'],
  ['alerts', 'Alerts'],
  ['logs', 'Logs'],
  ['status', 'Status'],
] as const

function Tasks() {
  const client = useQueryClient()
  const { report } = useFeedback()
  const tasks = useQuery({ queryKey: ['tasks'], queryFn: api.tasks, refetchInterval: LIVE.fast })
  const run = useMutation({
    mutationFn: api.runTask,
    onSuccess: (result) => {
      report(result)
      void client.invalidateQueries({ queryKey: ['tasks'] })
    },
  })
  if (!tasks.data) return <Loading error={tasks.error} />
  return (
    <section className="card table-wrap" aria-label="Tasks">
      <table className="table">
        <thead>
          <tr>
            <th>Task</th>
            <th className="min">Every</th>
            <th className="min">Last run</th>
            <th className="min">Next run</th>
            <th className="min">
              <span className="sr-only">Run</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {tasks.data.map((task) => (
            <tr key={task.name}>
              <td>
                <div style={{ fontWeight: 500 }}>{task.label}</div>
                {task.result && <div className="muted" style={{ fontSize: 12 }}>{task.result}</div>}
              </td>
              <td className="min">{task.interval}</td>
              <td className="min" title={dayTime(task.last_run)}>
                {timeAgo(task.last_run)}
              </td>
              <td className="min">
                {task.running ? (
                  <span className="accent">running…</span>
                ) : task.requested ? (
                  'starting soon'
                ) : (
                  timeUntil(task.next_run)
                )}
              </td>
              <td className="min">
                <button
                  type="button"
                  className="btn small"
                  disabled={task.running || task.requested || run.isPending}
                  onClick={() => run.mutate(task.name)}
                >
                  <Icon name={task.running ? 'spinner' : 'play'} size={12} className={task.running ? 'spin' : ''} />
                  Run now
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

function Alerts() {
  const client = useQueryClient()
  const { report } = useFeedback()
  const alerts = useQuery({ queryKey: ['alerts'], queryFn: api.alerts, refetchInterval: LIVE.slow })
  const seen = useRef(false)
  const retry = useMutation({
    mutationFn: api.retryAlert,
    onSuccess: (result) => {
      report(result)
      for (const key of ['alerts', 'shell', 'queue']) void client.invalidateQueries({ queryKey: [key] })
    },
  })

  // abrir a tela é ver: depois de mostrar o que era novo, o contador do menu zera
  useEffect(() => {
    if (!alerts.data || seen.current) return
    seen.current = true
    void api.alertsSeen().then(() => client.invalidateQueries({ queryKey: ['shell'] }))
  }, [alerts.data, client])

  if (!alerts.data) return <Loading error={alerts.error} />
  const { setup, alerts: rows } = alerts.data
  const items = (alert: AlertRow) =>
    alert.items.length > 3
      ? `${alert.items.slice(0, 3).join(', ')} and ${alert.items.length - 3} more`
      : alert.items.join(', ')

  return (
    <>
      {setup.length > 0 && (
        <section className="card" aria-label="Setup needed">
          <h2 className="section-label warn">Setup needed</h2>
          <div className="rows">
            {setup.map((item) => (
              <div key={item.title} className="row">
                <Icon name="settings" size={18} className="warn" />
                <div className="grow">
                  <div style={{ fontWeight: 500 }}>{item.title}</div>
                  <div className="sub">{item.detail}</div>
                </div>
                <Link to={item.href} className="btn small">
                  Configure
                </Link>
              </div>
            ))}
          </div>
        </section>
      )}
      <section className="card" aria-label="Alerts">
        {rows.length === 0 ? (
          <Empty icon="check" title="No alerts">
            <p>Steps that fail show up here, and the next library run tries them again.</p>
          </Empty>
        ) : (
          <div className="rows">
            {rows.map((alert, index) => {
              const resolved = alert.resolved_at !== null
              return (
                <div key={alert.key} className="row" style={{ alignItems: 'flex-start', borderTop: index ? undefined : 0, opacity: resolved ? 0.6 : 1 }}>
                  <Icon name={resolved ? 'check' : 'alert'} size={18} className={resolved ? 'ok' : 'bad'} />
                  <div className="grow">
                    <div>
                      {alert.message}
                      {!alert.seen && !resolved && <span className="edited" style={{ marginLeft: 8 }}>new</span>}
                    </div>
                    <div className="sub">
                      {alert.step} · {alert.count === 1 ? 'once' : `${alert.count} times`} · last {timeAgo(alert.last_at)}
                      {resolved && ` · solved ${timeAgo(alert.resolved_at)}`}
                    </div>
                    {alert.items.length > 0 && <div className="sub truncate">{items(alert)}</div>}
                  </div>
                  {!resolved && (
                    <button type="button" className="btn small" disabled={retry.isPending} onClick={() => retry.mutate(alert.key)}>
                      <Icon name="refresh" size={13} />
                      Retry now
                    </button>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </section>
    </>
  )
}

type Level = '' | 'warning' | 'error'
const LEVELS: Record<Level, (level: string) => boolean> = {
  '': () => true,
  warning: (level) => level !== 'INFO' && level !== 'DEBUG',
  error: (level) => level === 'ERROR' || level === 'CRITICAL',
}

function Logs() {
  const { toast } = useFeedback()
  const [paused, setPaused] = useState(false)
  const [level, setLevel] = useState<Level>('')
  const [text, setText] = useState('')
  const logs = useQuery({
    queryKey: ['logs'],
    queryFn: api.logs,
    refetchInterval: paused ? false : LIVE.normal,
  })
  if (!logs.data) return <Loading error={logs.error} />
  const needle = text.trim().toLowerCase()
  const rows = logs.data.filter(
    (row) => LEVELS[level](row.level) && (!needle || `${row.name} ${row.message}`.toLowerCase().includes(needle)),
  )
  const line = (row: LogRow) => `${row.time} ${row.level} ${row.name}: ${row.message}`
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(rows.map(line).join('\n'))
      toast(`${rows.length} lines copied.`)
    } catch {
      toast('Could not copy. Pause the list and select the lines by hand.', true)
    }
  }
  const tone = (row: LogRow) => (LEVELS.error(row.level) ? 'bad' : LEVELS.warning(row.level) ? 'warn' : 'muted')

  return (
    <>
      <div className="toolbar">
        <Chips
          label="Level"
          options={[
            { value: '', label: 'Everything' },
            { value: 'warning', label: 'Warnings and errors' },
            { value: 'error', label: 'Errors' },
          ]}
          value={level}
          onChange={setLevel}
        />
        <span className="spacer" />
        <SearchBox value={text} onChange={setText} placeholder="Search the logs" />
        <button type="button" className="btn small" aria-pressed={paused} onClick={() => setPaused(!paused)}>
          <Icon name={paused ? 'play' : 'pause'} size={13} />
          {paused ? 'Resume' : 'Pause'}
        </button>
        <button type="button" className="btn small" onClick={copy}>
          <Icon name="copy" size={13} />
          Copy
        </button>
      </div>
      <section className="card table-wrap" aria-label="Logs">
        {rows.length === 0 ? (
          <Empty icon="list" title="No log lines">
            <p>{logs.data.length ? 'Nothing matches these filters.' : 'Nothing was logged since c3parr started.'}</p>
          </Empty>
        ) : (
          <table className="table log">
            <tbody>
              {rows.map((row, index) => (
                <tr key={`${row.time}-${index}`}>
                  <td className="min muted">{row.time}</td>
                  <td className={`min ${tone(row)}`}>{row.level}</td>
                  <td className="min muted">{row.name}</td>
                  <td style={{ overflowWrap: 'anywhere' }}>{row.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  )
}

function Status() {
  const status = useQuery({ queryKey: ['status'], queryFn: api.status })
  if (!status.data) return <Loading error={status.error} />
  const { about, providers, secrets, warnings } = status.data
  return (
    <>
      {warnings.map((warning) => (
        <div key={warning.title} className={`notice ${warning.level === 'warn' ? 'warn' : ''}`}>
          <Icon name={warning.level === 'warn' ? 'alert' : 'info'} size={20} className={warning.level === 'warn' ? 'warn' : 'muted'} />
          <div className="grow">
            <strong>{warning.title}</strong>
            <span>{warning.detail}</span>
          </div>
        </div>
      ))}
      <section className="card pad" aria-label="About">
        <h2>About</h2>
        <dl className="facts">
          {about.map((fact) => (
            <div key={fact.label}>
              <dt>{fact.label}</dt>
              <dd style={{ overflowWrap: 'anywhere' }}>{fact.value}</dd>
            </div>
          ))}
        </dl>
      </section>
      <section className="card table-wrap" aria-label="Providers in use">
        <h2 className="section-label">Providers in use</h2>
        <table className="table">
          <tbody>
            {providers.map((p) => (
              <tr key={p.label}>
                <td>{p.label}</td>
                <td className="mono">{p.provider}</td>
                <td className={p.ok ? 'ok' : 'bad'}>{p.ok ? 'Ready' : p.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="card table-wrap" aria-label="Keys">
        <h2 className="section-label">Keys</h2>
        <table className="table">
          <tbody>
            {secrets.map((secret) => (
              <tr key={secret.name}>
                <td className="mono">{secret.name}</td>
                <td className={secret.source ? 'ok' : 'muted'}>{secret.source ? `Set in ${secret.source}` : 'Not set'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  )
}

export function System() {
  const { page } = useParams()
  if (!PAGES.some(([slug]) => slug === page)) return <NotFound />
  return (
    <>
      <PageHead title="System" />
      <nav className="tabs" aria-label="System pages">
        {PAGES.map(([slug, label]) => (
          <NavLink key={slug} to={`/system/${slug}`}>
            {label}
          </NavLink>
        ))}
      </nav>
      {page === 'tasks' && <Tasks />}
      {page === 'alerts' && <Alerts />}
      {page === 'logs' && <Logs />}
      {page === 'status' && <Status />}
    </>
  )
}
