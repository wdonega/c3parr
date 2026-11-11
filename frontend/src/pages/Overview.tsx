// Visão geral: o que precisa de atenção, quanto da biblioteca está coberto e o que roda agora.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, LIVE } from '../api/client'
import type { Dashboard, JobRow, LanguageRow, TaskRow } from '../api/types'
import { clock, doneLabel, duration, jobName, percent, plural, timeUntil } from '../lib/format'
import { PageHead, useShell } from '../shell/Shell'
import { Bar, Chips, Empty, Loading } from '../ui/controls'
import { useFeedback } from '../ui/feedback'
import { Icon } from '../ui/Icon'

const ORIGIN: Record<string, string> = {
  manual: 'requested by you',
  webhook: 'asked by Radarr/Sonarr',
  rotina: 'from the routine',
}

function Coverage({ data }: { data: Dashboard }) {
  const targets = data.languages.filter((row) => row.target)
  const others = data.languages.filter((row) => !row.target)
  const [chosen, setChosen] = useState<string | null>(null)
  const row: LanguageRow | undefined = targets.find((t) => t.lang === chosen) ?? targets[0]

  if (!row) {
    return (
      <section className="card wide" aria-label="Subtitle coverage">
        <Empty icon="movies" title="Nothing synced yet">
          <p>Movies and shows show up here after c3parr syncs with Radarr or Sonarr.</p>
          <Link to="/settings/library" className="btn">
            Connect Radarr or Sonarr
          </Link>
        </Empty>
      </section>
    )
  }

  const have = row.embedded + row.external + row.ours
  const total = have + row.missing
  const parts = [
    { key: 'embedded', label: 'Inside the video', n: row.embedded, color: '#6a5f82' },
    { key: 'external', label: 'Other source', n: row.external, color: 'var(--other)' },
    { key: 'ours', label: 'Made by c3parr', n: row.ours, color: 'var(--ours)' },
    { key: 'missing', label: 'Missing', n: row.missing, color: 'var(--warn)' },
  ].filter((part) => part.n > 0)
  const tile = (to: string, label: string, note: string, count: { total: number; done: number }) => (
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
      <Bar value={count.total ? count.done / count.total : 0} raised label={`${label} with every subtitle`} />
      {count.total - count.done > 0 ? (
        <span className="warn" style={{ fontSize: 12 }}>
          {count.total - count.done} missing a subtitle
        </span>
      ) : (
        <span className="ok" style={{ fontSize: 12 }}>Every subtitle in place</span>
      )}
    </Link>
  )

  return (
    <section className="card pad wide" aria-label="Subtitle coverage" style={{ gap: 18 }}>
      <div className="card-head">
        <h2>Subtitle coverage</h2>
        {targets.length > 1 ? (
          <Chips
            label="Target language"
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
          {have} of {total} movies and episodes have a {row.name} subtitle
        </span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div
          className="stack-bar"
          role="img"
          aria-label={parts.map((part) => `${part.n} ${part.label.toLowerCase()}`).join(', ')}
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
        {tile('/movies', 'Movies', '', data.movies)}
        {tile('/tv', 'Episodes', `· ${plural(data.episodes.shows, 'show')}`, data.episodes)}
      </div>
      {others.length > 0 && (
        <div className="legend muted" style={{ alignItems: 'center' }}>
          <span>Also in the library</span>
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
  return (
    <section className="card pad narrow" aria-label="Running now">
      {running && name ? (
        <>
          <div className="card-head">
            <h2 className="eyebrow live" style={{ color: 'var(--accent)' }}>
              <span className="dot" />
              Running now
            </h2>
            <span className="muted" style={{ fontSize: 12 }}>
              {duration(running.started_at, null)} elapsed
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
            <span className="status running">{running.step}</span>
            <span className="muted" style={{ fontSize: 12 }}>
              {ORIGIN[running.origin ?? ''] ?? ''}
            </span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <Bar value={running.progress} thick label={running.step} />
            <div style={{ display: 'flex', gap: 8, fontSize: 13 }}>
              <span className="num" style={{ flex: 1, fontWeight: 600 }}>
                {percent(running.progress)}
              </span>
              <span style={{ color: 'var(--text-2)' }}>{running.message}</span>
            </div>
          </div>
        </>
      ) : (
        <Empty icon="queue" title="Nothing is running">
          <p>{waiting > 0 ? `${plural(waiting, 'task')} waiting to start.` : 'The queue is empty.'}</p>
        </Empty>
      )}
      {!!upcoming.data?.length && (
        <div style={{ paddingTop: 14, borderTop: '1px solid var(--line)' }}>
          <p className="eyebrow" style={{ paddingBottom: 6 }}>
            Up next · {waiting} queued
          </p>
          {upcoming.data.map((job) => (
            <div key={job.id} style={{ display: 'flex', alignItems: 'center', gap: 10, height: 30, fontSize: 13 }}>
              <span className="grow truncate" style={{ flex: 1 }}>
                {jobName(job).title}
              </span>
              <span className="muted" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>
                {job.step}
              </span>
            </div>
          ))}
        </div>
      )}
      <Link to="/queue" className="btn block" style={{ marginTop: 'auto' }}>
        Open the queue
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
    task.running ? 'running…' : task.requested ? 'starting soon' : `next ${timeUntil(task.next_run)}`
  return (
    <section className="card pad" aria-label="Scheduled tasks" style={{ gap: 10 }}>
      <h2>Scheduled tasks</h2>
      <div className="rows">
        {tasks.data?.map((task) => (
          <div key={task.name} className="row tight">
            <div className="grow">
              <div className="truncate" style={{ fontWeight: 500 }}>
                {task.label}
              </div>
              <div className="sub truncate">{task.result ?? `every ${task.interval}`}</div>
            </div>
            <span style={{ fontSize: 12, color: 'var(--text-2)', whiteSpace: 'nowrap' }}>{next(task)}</span>
            <button
              type="button"
              className="icon-btn outlined"
              aria-label={`Run ${task.label} now`}
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
    <section className="card pad" aria-label="Recently finished" style={{ gap: 10 }}>
      <h2>Recently finished</h2>
      {recent.data?.length === 0 && <p className="muted">Nothing finished yet.</p>}
      <div className="rows">
        {recent.data?.map((job) => {
          const failed = job.status === 'failed'
          return (
            <Link key={job.id} to={`/queue?job=${job.id}`} className="row tight" style={{ color: 'var(--text)' }}>
              <Icon name={failed ? 'alert' : 'check'} className={failed ? 'bad' : 'ok'} />
              <div className="grow">
                <div className="truncate">{jobName(job).title}</div>
                <div className="sub truncate">
                  {failed
                    ? `${job.step} failed`
                    : `${doneLabel(job.action, job.language, job.step)} · ${duration(job.started_at, job.finished_at)}`}
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
        <PageHead title="Overview" />
        <Loading error={dashboard.error} />
      </>
    )
  }
  const targets = data.languages.filter((row) => row.target).map((row) => row.lang)
  const activity = [
    { n: data.activity.translations, label: 'Translations' },
    { n: data.activity.transcriptions, label: 'Transcriptions from audio' },
    { n: data.activity.extracted, label: 'Embedded subtitles extracted' },
    { n: data.activity.synced, label: 'Subtitles re-synced' },
  ]
  return (
    <>
      <PageHead
        title="Overview"
        sub={targets.length ? `Target ${targets.length > 1 ? 'languages' : 'language'} ${targets.join(', ')}` : undefined}
      />

      {shell.data?.setup.map((item) => (
        <div key={item.title} className="notice warn" role="status">
          <Icon name="settings" size={20} className="warn" />
          <div className="grow">
            <strong>{item.title}</strong>
            <span>{item.detail}</span>
          </div>
          <Link to={item.href} className="btn">
            Configure
          </Link>
        </div>
      ))}
      {data.alerts_open > 0 && (
        <div className="notice bad" role="status">
          <Icon name="alert" size={20} className="bad" />
          <div className="grow">
            <strong>
              {data.alerts_open === 1 ? '1 problem needs' : `${data.alerts_open} problems need`} a look
            </strong>
            <span>Failed steps wait for the next library run, or you can retry them now.</span>
          </div>
          <Link to="/system/alerts" className="btn danger">
            View alerts
          </Link>
        </div>
      )}
      {data.warnings.map((warning) => (
        <div key={warning.title} className="notice warn" role="status">
          <Icon name="info" size={20} className="warn" />
          <div className="grow">
            <strong>{warning.title}</strong>
            <span>{warning.detail}</span>
          </div>
        </div>
      ))}

      <div className="split">
        <Coverage data={data} />
        <RunningNow running={shell.data?.running ?? null} pending={shell.data?.pending ?? 0} />
      </div>

      <div className="grid cols-3">
        <section className="card pad" aria-label="What c3parr did">
          <h2>What c3parr did</h2>
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
              ? `${plural(data.activity.failed, 'step')} waiting after a failure`
              : 'No step waiting after a failure'}
          </p>
        </section>
        <Tasks />
        <Recent />
      </div>
    </>
  )
}
