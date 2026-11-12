// Visão geral: o que precisa de atenção, quanto da biblioteca está coberto e o que roda agora.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, LIVE } from '../api/client'
import type { Dashboard, JobRow, LanguageRow, TaskRow } from '../api/types'
import { languageName, maybe, t, titled, useLocale } from '../i18n'
import { clock, doneLabel, duration, jobName, percent, progressText, stepLabel, timeUntil } from '../lib/format'
import { PageHead, useShell } from '../shell/Shell'
import { Bar, Chips, Empty, Loading } from '../ui/controls'
import { useFeedback } from '../ui/feedback'
import { Icon } from '../ui/Icon'

function Coverage({ data }: { data: Dashboard }) {
  const targets = data.languages.filter((row) => row.target)
  const others = data.languages.filter((row) => !row.target)
  const [chosen, setChosen] = useState<string | null>(null)
  const row: LanguageRow | undefined = targets.find((t) => t.lang === chosen) ?? targets[0]

  if (!row) {
    return (
      <section className="card wide" aria-label={t('overview.coverage')}>
        <Empty icon="movies" title={t('overview.empty.title')}>
          <p>{t('overview.empty.body')}</p>
          <Link to="/settings/library" className="btn">
            {t('overview.empty.action')}
          </Link>
        </Empty>
      </section>
    )
  }

  const have = row.embedded + row.external + row.ours
  const total = have + row.missing
  const parts = [
    { key: 'embedded', label: t('overview.part.embedded'), n: row.embedded, color: '#6a5f82' },
    { key: 'external', label: t('overview.part.external'), n: row.external, color: 'var(--other)' },
    { key: 'ours', label: t('overview.part.ours'), n: row.ours, color: 'var(--ours)' },
    { key: 'missing', label: t('overview.part.missing'), n: row.missing, color: 'var(--warn)' },
  ].filter((part) => part.n > 0)
  const tile = (to: string, label: string, all: string, note: string, count: { total: number; done: number }) => (
    <Link to={to} className="card pad" style={{ gap: 8, background: 'var(--surface-2)', color: 'var(--text)' }}>
      <span style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span style={{ flex: 1, fontWeight: 500 }}>
          {label} <span className="muted" style={{ fontWeight: 400 }}>{note}</span>
        </span>
        <span className="num" style={{ fontSize: 20, fontWeight: 600 }}>
          {count.done}
          <span className="muted" style={{ fontSize: 13, fontWeight: 400 }}> / {count.total}</span>
        </span>
      </span>
      <Bar value={count.total ? count.done / count.total : 0} raised label={all} />
      {count.total - count.done > 0 ? (
        <span className="warn" style={{ fontSize: 12 }}>
          {t('overview.missing', { n: count.total - count.done })}
        </span>
      ) : (
        <span className="ok" style={{ fontSize: 12 }}>{t('overview.allInPlace')}</span>
      )}
    </Link>
  )

  return (
    <section className="card pad wide" aria-label={t('overview.coverage')} style={{ gap: 18 }}>
      <div className="card-head">
        <h2>{t('overview.coverage')}</h2>
        {targets.length > 1 ? (
          <Chips
            label={t('overview.targetLanguage')}
            options={targets.map((t) => ({ value: t.lang, label: t.lang }))}
            value={row.lang}
            onChange={setChosen}
          />
        ) : (
          <span className="pill working">{row.lang}</span>
        )}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', gap: 14 }}>
        <span className="num" style={{ fontSize: 56, fontWeight: 600, lineHeight: 1, letterSpacing: '-0.03em' }}>
          {total ? Math.round((have / total) * 100) : 0}%
        </span>
        <span style={{ fontSize: 15, color: 'var(--text-2)' }}>
          {t('overview.have', { have, total, language: languageName(row.lang, row.name) })}
        </span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div
          className="stack-bar"
          role="img"
          aria-label={parts.map((part) => `${part.n} ${part.label.toLocaleLowerCase()}`).join(', ')}
        >
          {parts.map((part) => (
            <span key={part.key} style={{ flex: `${part.n} 1 0`, background: part.color }} />
          ))}
        </div>
        <div className="legend">
          {parts.map((part) => (
            <span key={part.key}>
              <span className="swatch" style={{ background: part.color }} />
              {part.label} <b className="num">{part.n}</b>
            </span>
          ))}
        </div>
      </div>
      <div
        className="grid"
        style={{
          gridTemplateColumns: 'repeat(auto-fit, minmax(min(260px, 100%), 1fr))',
          gap: 14,
          paddingTop: 18,
          borderTop: '1px solid var(--line)',
        }}
      >
        {tile('/movies', t('overview.movies'), t('overview.moviesAll'), '', data.movies)}
        {tile(
          '/tv',
          t('overview.episodes'),
          t('overview.episodesAll'),
          t('overview.showsNote', { n: data.episodes.shows }),
          data.episodes,
        )}
      </div>
      {others.length > 0 && (
        <div className="legend muted" style={{ alignItems: 'center' }}>
          <span>{t('overview.also')}</span>
          {others.map((other) => (
            <span key={other.lang}>
              <span className="pill external">{other.lang}</span>
              {other.embedded + other.external + other.ours}
            </span>
          ))}
        </div>
      )}
    </section>
  )
}

function RunningNow({ running, pending }: { running: JobRow | null; pending: number }) {
  const upcoming = useQuery({
    queryKey: ['queue', 'upcoming', 3],
    queryFn: () => api.upcoming(3),
    refetchInterval: LIVE.normal,
  })
  const waiting = running ? pending - 1 : pending
  const name = running && jobName(running)
  const step = running ? stepLabel(running.action, running.language, running.step) : ''
  return (
    <section className="card pad narrow" aria-label={t('common.runningNow')}>
      {running && name ? (
        <>
          <div className="card-head">
            <h2 className="eyebrow live" style={{ color: 'var(--accent)' }}>
              <span className="dot" />
              {t('common.runningNow')}
            </h2>
            <span className="muted" style={{ fontSize: 12 }}>
              {t('common.elapsed', { time: duration(running.started_at, null) })}
            </span>
          </div>
          <div>
            {running.item?.kind === 'episode' && name.sub && (
              <p style={{ fontSize: 13, color: 'var(--text-2)' }}>{name.sub}</p>
            )}
            <p style={{ fontSize: 18, fontWeight: 600, letterSpacing: '-0.01em' }}>
              {name.title}
              {running.item?.kind === 'movie' && name.sub && (
                <span className="muted num" style={{ fontSize: 14, fontWeight: 400 }}> {name.sub}</span>
              )}
            </p>
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
            <span className="status running">{step}</span>
            <span className="muted" style={{ fontSize: 12 }}>
              {maybe(`overview.origin.${running.origin}`, '')}
            </span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <Bar value={running.progress} thick label={step} />
            <div style={{ display: 'flex', gap: 8, fontSize: 13 }}>
              <span className="num" style={{ flex: 1, fontWeight: 600 }}>
                {percent(running.progress)}
              </span>
              <span style={{ color: 'var(--text-2)' }}>{progressText(running.message)}</span>
            </div>
          </div>
        </>
      ) : (
        <Empty icon="queue" title={t('overview.nothingRunning')}>
          <p>{waiting > 0 ? t('overview.waiting', { n: waiting }) : t('overview.queueEmpty')}</p>
        </Empty>
      )}
      {!!upcoming.data?.length && (
        <div style={{ paddingTop: 14, borderTop: '1px solid var(--line)' }}>
          <p className="eyebrow" style={{ paddingBottom: 6 }}>
            {t('overview.upNext', { n: waiting })}
          </p>
          {upcoming.data.map((job) => (
            <div key={job.id} style={{ display: 'flex', alignItems: 'center', gap: 10, height: 30, fontSize: 13 }}>
              <span className="grow truncate" style={{ flex: 1 }}>
                {jobName(job).title}
              </span>
              <span className="muted" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>
                {stepLabel(job.action, job.language, job.step)}
              </span>
            </div>
          ))}
        </div>
      )}
      <Link to="/queue" className="btn block" style={{ marginTop: 'auto' }}>
        {t('overview.openQueue')}
      </Link>
    </section>
  )
}

function Tasks() {
  const client = useQueryClient()
  const { report } = useFeedback()
  const tasks = useQuery({ queryKey: ['tasks'], queryFn: api.tasks, refetchInterval: LIVE.normal })
  const run = useMutation({
    mutationFn: api.runTask,
    onSuccess: (result) => {
      report(result)
      void client.invalidateQueries({ queryKey: ['tasks'] })
    },
  })
  const next = (task: TaskRow) =>
    task.running
      ? t('task.running')
      : task.requested
        ? t('task.startingSoon')
        : t('overview.next', { when: timeUntil(task.next_run) })
  const label = (task: TaskRow) => maybe(`routine.${task.name}`, task.label)
  return (
    <section className="card pad" aria-label={t('overview.tasks')} style={{ gap: 10 }}>
      <h2>{t('overview.tasks')}</h2>
      <div className="rows">
        {tasks.data?.map((task) => (
          <div key={task.name} className="row tight">
            <div className="grow">
              <div className="truncate" style={{ fontWeight: 500 }}>
                {label(task)}
              </div>
              <div className="sub truncate">{task.result ?? t('overview.every', { interval: task.interval })}</div>
            </div>
            <span style={{ fontSize: 12, color: 'var(--text-2)', whiteSpace: 'nowrap' }}>{next(task)}</span>
            <button
              type="button"
              className="icon-btn outlined"
              aria-label={t('overview.runNow', { task: label(task) })}
              disabled={task.running || task.requested}
              onClick={() => run.mutate(task.name)}
            >
              <Icon name={task.running ? 'spinner' : 'play'} size={13} className={task.running ? 'spin' : ''} />
            </button>
          </div>
        ))}
      </div>
    </section>
  )
}

function Recent() {
  const recent = useQuery({
    queryKey: ['queue', 'recent', 4],
    queryFn: () => api.recent(4),
    refetchInterval: LIVE.normal,
  })
  return (
    <section className="card pad" aria-label={t('overview.recent')} style={{ gap: 10 }}>
      <h2>{t('overview.recent')}</h2>
      {recent.data?.length === 0 && <p className="muted">{t('overview.noneFinished')}</p>}
      <div className="rows">
        {recent.data?.map((job) => {
          const failed = job.status === 'failed'
          const step = stepLabel(job.action, job.language, job.step)
          return (
            <Link key={job.id} to={`/queue?job=${job.id}`} className="row tight" style={{ color: 'var(--text)' }}>
              <Icon name={failed ? 'alert' : 'check'} className={failed ? 'bad' : 'ok'} />
              <div className="grow">
                <div className="truncate">{jobName(job).title}</div>
                <div className="sub truncate">
                  {failed
                    ? t('job.stepFailed', { step })
                    : `${doneLabel(job.action, job.language, step)} · ${duration(job.started_at, job.finished_at)}`}
                </div>
              </div>
              <span className="muted num" style={{ fontSize: 12 }}>
                {job.finished_at ? clock(job.finished_at) : ''}
              </span>
            </Link>
          )
        })}
      </div>
    </section>
  )
}

export function Overview() {
  useLocale()
  const shell = useShell()
  const dashboard = useQuery({
    queryKey: ['dashboard'],
    queryFn: api.dashboard,
    refetchInterval: LIVE.normal,
  })
  const data = dashboard.data
  if (!data) {
    return (
      <>
        <PageHead title={t('overview.title')} />
        <Loading error={dashboard.error} />
      </>
    )
  }
  const targets = data.languages.filter((row) => row.target).map((row) => row.lang)
  const activity = [
    { n: data.activity.translations, label: t('overview.translations') },
    { n: data.activity.transcriptions, label: t('overview.transcriptions') },
    { n: data.activity.extracted, label: t('overview.extracted') },
    { n: data.activity.synced, label: t('overview.synced') },
  ]
  return (
    <>
      <PageHead
        title={t('overview.title')}
        sub={targets.length ? t('overview.target', { n: targets.length, langs: targets.join(', ') }) : undefined}
      />

      {shell.data?.setup.map((item) => {
        const text = titled('setup', item)
        return (
          <div key={item.title} className="notice warn" role="status">
            <Icon name="settings" size={20} className="warn" />
            <div className="grow">
              <strong>{text.title}</strong>
              <span>{text.detail}</span>
            </div>
            <Link to={item.href} className="btn">
              {t('common.configure')}
            </Link>
          </div>
        )
      })}
      {data.alerts_open > 0 && (
        <div className="notice bad" role="status">
          <Icon name="alert" size={20} className="bad" />
          <div className="grow">
            <strong>{t('overview.problems', { n: data.alerts_open })}</strong>
            <span>{t('overview.problemsBody')}</span>
          </div>
          <Link to="/system/alerts" className="btn danger">
            {t('overview.viewAlerts')}
          </Link>
        </div>
      )}
      {data.warnings.map((warning) => {
        const text = titled('notice', warning)
        return (
          <div key={warning.title} className="notice warn" role="status">
            <Icon name="info" size={20} className="warn" />
            <div className="grow">
              <strong>{text.title}</strong>
              <span>{text.detail}</span>
            </div>
          </div>
        )
      })}

      <div className="split">
        <Coverage data={data} />
        <RunningNow running={shell.data?.running ?? null} pending={shell.data?.pending ?? 0} />
      </div>

      <div className="grid cols-3">
        <section className="card pad" aria-label={t('overview.did')}>
          <h2>{t('overview.did')}</h2>
          <div className="grid" style={{ gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '14px 18px' }}>
            {activity.map((item) => (
              <div key={item.label}>
                <div className="num" style={{ fontSize: 26, fontWeight: 600, lineHeight: 1.1 }}>
                  {item.n}
                </div>
                <div className="muted" style={{ fontSize: 12 }}>
                  {item.label}
                </div>
              </div>
            ))}
          </div>
          <p
            className={data.activity.failed ? 'bad' : 'muted'}
            style={{ marginTop: 'auto', paddingTop: 12, borderTop: '1px solid var(--line)', fontSize: 13 }}
          >
            {data.activity.failed
              ? t('overview.failedSteps', { n: data.activity.failed })
              : t('overview.noFailed')}
          </p>
        </section>
        <Tasks />
        <Recent />
      </div>
    </>
  )
}
