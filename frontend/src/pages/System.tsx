// System: rotinas, alertas, logs e status, cada um numa aba.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { Link, NavLink, useParams } from 'react-router-dom'
import { api, LIVE } from '../api/client'
import type { AlertRow, LogRow } from '../api/types'
import { maybe, t, titled, useLocale } from '../i18n'
import { dayTime, timeAgo, timeUntil } from '../lib/format'
import { NotFound, PageHead } from '../shell/Shell'
import { Chips, Empty, Loading, SearchBox } from '../ui/controls'
import { useFeedback } from '../ui/feedback'
import { Icon } from '../ui/Icon'

const PAGES = ['tasks', 'alerts', 'logs', 'status'] as const

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
    <section className="card table-wrap" aria-label={t('system.tab.tasks')}>
      <table className="table">
        <thead>
          <tr>
            <th>{t('system.task')}</th>
            <th className="min">{t('system.every')}</th>
            <th className="min">{t('system.lastRun')}</th>
            <th className="min">{t('system.nextRun')}</th>
            <th className="min">
              <span className="sr-only">{t('system.run')}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {tasks.data.map((task) => (
            <tr key={task.name}>
              <td>
                <div style={{ fontWeight: 500 }}>{maybe(`routine.${task.name}`, task.label)}</div>
                {task.result && <div className="muted" style={{ fontSize: 12 }}>{task.result}</div>}
              </td>
              <td className="min">{task.interval}</td>
              <td className="min" title={dayTime(task.last_run)}>
                {timeAgo(task.last_run)}
              </td>
              <td className="min">
                {task.running ? (
                  <span className="accent">{t('task.running')}</span>
                ) : task.requested ? (
                  t('task.startingSoon')
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
                  {t('system.runNow')}
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
      ? t('system.andMore', { items: alert.items.slice(0, 3).join(', '), n: alert.items.length - 3 })
      : alert.items.join(', ')

  return (
    <>
      {setup.length > 0 && (
        <section className="card" aria-label={t('system.setup')}>
          <h2 className="section-label warn">{t('system.setup')}</h2>
          <div className="rows">
            {setup.map((item) => {
              const text = titled('setup', item)
              return (
                <div key={item.title} className="row">
                  <Icon name="settings" size={18} className="warn" />
                  <div className="grow">
                    <div style={{ fontWeight: 500 }}>{text.title}</div>
                    <div className="sub">{text.detail}</div>
                  </div>
                  <Link to={item.href} className="btn small">
                    {t('common.configure')}
                  </Link>
                </div>
              )
            })}
          </div>
        </section>
      )}
      <section className="card" aria-label={t('system.tab.alerts')}>
        {rows.length === 0 ? (
          <Empty icon="check" title={t('system.noAlerts')}>
            <p>{t('system.noAlertsBody')}</p>
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
                      {!alert.seen && !resolved && <span className="edited" style={{ marginLeft: 8 }}>{t('system.new')}</span>}
                    </div>
                    <div className="sub">
                      {maybe(`action.${alert.kind}`, alert.step)} · {t('system.times', { n: alert.count })} ·{' '}
                      {t('system.last', { when: timeAgo(alert.last_at) })}
                      {resolved && ` · ${t('system.solved', { when: timeAgo(alert.resolved_at) })}`}
                    </div>
                    {alert.items.length > 0 && <div className="sub truncate">{items(alert)}</div>}
                  </div>
                  {!resolved && (
                    <button type="button" className="btn small" disabled={retry.isPending} onClick={() => retry.mutate(alert.key)}>
                      <Icon name="refresh" size={13} />
                      {t('system.retryNow')}
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
      toast(t('system.copied', { n: rows.length }))
    } catch {
      toast(t('system.copyFailed'), true)
    }
  }
  const tone = (row: LogRow) => (LEVELS.error(row.level) ? 'bad' : LEVELS.warning(row.level) ? 'warn' : 'muted')

  return (
    <>
      <div className="toolbar">
        <Chips
          label={t('system.level')}
          options={[
            { value: '', label: t('system.level.all') },
            { value: 'warning', label: t('system.level.warning') },
            { value: 'error', label: t('system.level.error') },
          ]}
          value={level}
          onChange={setLevel}
        />
        <span className="spacer" />
        <SearchBox value={text} onChange={setText} placeholder={t('system.searchLogs')} />
        <button type="button" className="btn small" aria-pressed={paused} onClick={() => setPaused(!paused)}>
          <Icon name={paused ? 'play' : 'pause'} size={13} />
          {paused ? t('system.resume') : t('system.pause')}
        </button>
        <button type="button" className="btn small" onClick={copy}>
          <Icon name="copy" size={13} />
          {t('system.copy')}
        </button>
      </div>
      <section className="card table-wrap" aria-label={t('system.tab.logs')}>
        {rows.length === 0 ? (
          <Empty icon="list" title={t('system.noLogs')}>
            <p>{logs.data.length ? t('system.noLogsMatch') : t('system.noLogsYet')}</p>
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
      {warnings.map((warning) => {
        const text = titled('notice', warning)
        return (
          <div key={warning.title} className={`notice ${warning.level === 'warn' ? 'warn' : ''}`}>
            <Icon name={warning.level === 'warn' ? 'alert' : 'info'} size={20} className={warning.level === 'warn' ? 'warn' : 'muted'} />
            <div className="grow">
              <strong>{text.title}</strong>
              <span>{text.detail}</span>
            </div>
          </div>
        )
      })}
      <section className="card pad" aria-label={t('system.about')}>
        <h2>{t('system.about')}</h2>
        <dl className="facts">
          {about.map((fact) => (
            <div key={fact.label}>
              <dt>{maybe(`fact.${fact.id}`, fact.label)}</dt>
              <dd style={{ overflowWrap: 'anywhere' }}>{fact.value}</dd>
            </div>
          ))}
        </dl>
      </section>
      <section className="card table-wrap" aria-label={t('system.providers')}>
        <h2 className="section-label">{t('system.providers')}</h2>
        <table className="table">
          <tbody>
            {providers.map((p) => (
              <tr key={p.label}>
                <td>{maybe(`engine.${p.id}`, p.label)}</td>
                <td className="mono">{p.provider}</td>
                <td className={p.ok ? 'ok' : 'bad'}>{p.ok ? t('system.ready') : p.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="card table-wrap" aria-label={t('system.keys')}>
        <h2 className="section-label">{t('system.keys')}</h2>
        <table className="table">
          <tbody>
            {secrets.map((secret) => (
              <tr key={secret.name}>
                <td className="mono">{secret.name}</td>
                <td className={secret.source ? 'ok' : 'muted'}>
                  {secret.source ? t(`system.setIn.${secret.source}`) : t('system.notSet')}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  )
}

export function System() {
  useLocale()
  const { page } = useParams()
  if (!PAGES.some((slug) => slug === page)) return <NotFound />
  return (
    <>
      <PageHead title={t('system.title')} />
      <nav className="tabs" aria-label={t('system.pages')}>
        {PAGES.map((slug) => (
          <NavLink key={slug} to={`/system/${slug}`}>
            {t(`system.tab.${slug}`)}
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
