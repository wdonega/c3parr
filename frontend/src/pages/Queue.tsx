// A fila: o que roda agora, o que vem a seguir na ordem real e o que terminou; com busca ou
// filtro vira uma lista paginada. O detalhe abre ao lado (ou em tela cheia no celular).
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, LIVE, reportUrl, subtitleUrl } from '../api/client'
import type { JobDetail, JobRow, Step } from '../api/types'
import { maybe, rich, t, useLocale } from '../i18n'
import { clock, dayTime, doneLabel, duration, jobName, percent, progressText, stepLabel } from '../lib/format'
import { PageHead, useShell } from '../shell/Shell'
import { Bar, Chips, Empty, Loading, Pager, SearchBox, type ChipOption } from '../ui/controls'
import { useFeedback } from '../ui/feedback'
import { Icon, type IconName } from '../ui/Icon'

type Filter = '' | 'running' | 'queued' | 'done' | 'failed' | 'cancelled'

const STATUS_ICON: Record<JobRow['status'], { name: IconName; tone: string }> = {
  queued: { name: 'more', tone: 'muted' },
  running: { name: 'spinner', tone: 'accent spin' },
  done: { name: 'check', tone: 'ok' },
  done_with_warnings: { name: 'alert', tone: 'warn' },
  failed: { name: 'alert', tone: 'bad' },
  cancelled: { name: 'close', tone: 'muted' },
}

function useIsSmall(): boolean {
  const query = '(max-width: 899px)'
  const [small, setSmall] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const media = window.matchMedia(query)
    const onChange = () => setSmall(media.matches)
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])
  return small
}

function useQueueActions() {
  const client = useQueryClient()
  const { report, toast } = useFeedback()
  const refresh = () => {
    for (const key of ['queue', 'job', 'shell', 'movies', 'shows', 'show']) {
      void client.invalidateQueries({ queryKey: [key] })
    }
  }
  const options = { onSuccess: refresh, onError: (error: Error) => toast(error.message, true) }
  return {
    cancel: useMutation({ mutationFn: api.cancelJob, ...options }),
    retry: useMutation({ mutationFn: api.retryJob, ...options }),
    top: useMutation({ mutationFn: api.topJob, ...options }),
    cancelQueued: useMutation({
      mutationFn: api.cancelQueued,
      onSuccess: (result) => {
        report(result)
        refresh()
      },
    }),
    retryFailed: useMutation({
      mutationFn: api.retryFailed,
      onSuccess: (result) => {
        report(result)
        refresh()
      },
    }),
  }
}

const stepOf = (job: JobRow) => stepLabel(job.action, job.language, job.step)

function summary(job: JobRow): string {
  const step = stepOf(job)
  if (job.status === 'running') return [step, progressText(job.message)].filter(Boolean).join(' · ')
  if (job.status === 'failed') {
    return t('queue.failedWith', { step, error: job.error ?? t('queue.unknownError') })
  }
  if (job.status === 'queued') return step
  if (job.status === 'cancelled') return t('queue.cancelledStep', { step })
  const took = duration(job.started_at, job.finished_at)
  return [doneLabel(job.action, job.language, step), took].filter(Boolean).join(' · ')
}

/** O que uma etapa fez, em palavras: `done · 3 s · llm`, ou o andamento da que roda agora. */
function stepText(step: Step): string {
  if (step.state === 'running') return progressText(step.reason) || t('stepState.running')
  const parts = [t(`stepState.${step.state}`)]
  if (step.seconds) parts.push(`${Math.round(step.seconds)} s`)
  if (step.provider) parts.push(step.provider)
  if (step.reason) parts.push(step.reason)
  return parts.join(' · ')
}

function JobLine({
  job,
  selected,
  onSelect,
  lead,
  children,
}: {
  job: JobRow
  selected: boolean
  onSelect: () => void
  lead?: ReactNode
  children?: ReactNode
}) {
  const name = jobName(job)
  const icon = STATUS_ICON[job.status]
  const when = job.finished_at ?? job.started_at ?? job.created_at
  return (
    <div className="row" style={{ padding: 0, background: job.status === 'failed' ? '#221416' : undefined }}>
      <button
        type="button"
        className="row row-btn grow"
        style={{ border: 0 }}
        aria-pressed={selected}
        onClick={onSelect}
      >
        {lead ?? <Icon name={icon.name} size={18} className={icon.tone} />}
        <span className="grow">
          <span className="truncate" style={{ display: 'block' }}>
            {name.title}
            {name.sub && <span className="muted"> · {name.sub}</span>}
          </span>
          <span
            className={`sub truncate ${job.status === 'failed' ? 'bad' : ''}`}
            style={{ display: 'block' }}
          >
            {summary(job)}
          </span>
        </span>
        <span className="muted num" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>
          {clock(when)}
        </span>
      </button>
      {children && <span style={{ display: 'flex', gap: 2, paddingRight: 10 }}>{children}</span>}
    </div>
  )
}

function Steps({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div style={{ paddingTop: 14, borderTop: '1px solid var(--line)' }}>
      <h3 className="eyebrow" style={{ marginBottom: 6 }}>
        {title}
      </h3>
      {children}
    </div>
  )
}

function JobPanel({ id, onClose }: { id: number; onClose?: () => void }) {
  const { confirm, toast } = useFeedback()
  const actions = useQueueActions()
  const detail = useQuery({
    queryKey: ['job', id],
    queryFn: () => api.job(id),
    refetchInterval: (query) => (query.state.data?.can_cancel ? LIVE.fast : false),
    retry: false,
  })
  const job: JobDetail | undefined = detail.data
  if (!job) return <Loading error={detail.error} />

  const name = jobName(job)
  const finished = !job.can_cancel
  const item = job.item
  const where = item?.kind === 'episode' && item.show_id ? `/tv/${item.show_id}` : null
  const cancel = async () => {
    const running = job.status === 'running'
    const sure =
      !running ||
      (await confirm({
        title: t('queue.cancel.title'),
        body: t('queue.cancel.body'),
        confirm: t('queue.cancel.confirm'),
        danger: true,
      }))
    if (sure) actions.cancel.mutate(job.id)
  }
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(job.video)
      toast(t('queue.pathCopied'))
    } catch {
      toast(t('queue.copyFailed'), true)
    }
  }

  return (
    <>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
        <span className={`status ${job.status}`}>
          {job.status === 'running' && <span className="dot" />}
          {t(`status.${job.status}`)}
        </span>
        <span className="spacer" />
        {job.status === 'queued' && (
          <button type="button" className="btn small" onClick={() => actions.top.mutate(job.id)}>
            <Icon name="up" size={14} />
            {t('queue.moveTop')}
          </button>
        )}
        {job.can_cancel && (
          <button type="button" className="btn danger small" onClick={cancel}>
            {t('queue.cancelTask')}
          </button>
        )}
        {job.can_retry && (
          <button type="button" className="btn small" onClick={() => actions.retry.mutate(job.id)}>
            <Icon name="refresh" size={14} />
            {t('queue.runAgain')}
          </button>
        )}
        {onClose && (
          <button type="button" className="icon-btn" aria-label={t('common.close')} onClick={onClose}>
            <Icon name="close" />
          </button>
        )}
      </div>

      <div>
        <h2 style={{ fontSize: 20, fontWeight: 600, letterSpacing: '-0.01em', color: 'var(--text)', textTransform: 'none' }}>
          {name.title}
        </h2>
        {name.sub && (where ? <Link to={where}>{name.sub}</Link> : <span className="muted">{name.sub}</span>)}
      </div>

      <div className="path">
        <span className="mono">{job.video}</span>
        <button type="button" className="icon-btn" aria-label={t('queue.copyPath')} onClick={copy}>
          <Icon name="copy" size={14} />
        </button>
      </div>

      {job.status === 'running' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <Bar value={job.progress} thick label={stepOf(job)} />
          <div style={{ display: 'flex', gap: 8, fontSize: 13 }}>
            <span className="num" style={{ flex: 1, fontWeight: 600 }}>
              {percent(job.progress)}
            </span>
            <span style={{ color: 'var(--text-2)' }}>
              {[job.stage && maybe(`step.${job.stage_name}`, job.stage), progressText(job.message)]
                .filter(Boolean)
                .join(' · ')}
            </span>
          </div>
        </div>
      )}

      {job.error && (
        <div className="notice bad" role="alert">
          <Icon name="alert" size={18} className="bad" />
          <div className="grow">
            <strong>{t('queue.failed')}</strong>
            <span>{job.error}</span>
          </div>
        </div>
      )}
      {job.warnings.map((warning) => (
        <div key={warning.stage} className="notice warn">
          <Icon name="info" size={18} className="warn" />
          <div className="grow">
            <strong>{maybe(`step.${warning.stage}`, warning.label)}</strong>
            <span>{[warning.reason, warning.effect].filter(Boolean).join('. ')}</span>
          </div>
        </div>
      ))}

      <dl className="facts">
        <div>
          <dt>{t('queue.fact.step')}</dt>
          <dd>{stepOf(job)}</dd>
        </div>
        <div>
          <dt>{t('queue.fact.requested')}</dt>
          <dd>{maybe(`queue.origin.${job.origin}`, t('queue.origin.none'))}</dd>
        </div>
        <div>
          <dt>{t('queue.fact.added')}</dt>
          <dd>{dayTime(job.created_at)}</dd>
        </div>
        {job.started_at && (
          <div>
            <dt>{finished ? t('queue.fact.took') : t('queue.fact.runningFor')}</dt>
            <dd>{duration(job.started_at, job.finished_at)}</dd>
          </div>
        )}
      </dl>

      {job.pipeline.length > 0 && (
        <Steps title={t('queue.pipeline')}>
          {job.pipeline.map((step) => (
            <div key={`${step.action}-${step.language}`} className={`step ${step.state}`}>
              <span className="mark" />
              <span className="label">
                {maybe(`action.${step.action}`, step.label)}
                {step.language && <span className="mono muted"> {step.language}</span>}
              </span>
              <span className="text">
                {step.state === 'failed' ? step.detail : step.state === 'not_found' ? t('queue.nothingFound') : ''}
                {step.attempts > 1 ? t('queue.tries', { n: step.attempts }) : ''} · {dayTime(step.updated_at)}
              </span>
            </div>
          ))}
        </Steps>
      )}

      {finished && job.steps.length > 0 && (
        <Steps title={t('queue.did')}>
          {job.steps.map((step) => (
            <div key={step.label} className={`step ${step.state}`}>
              <span className="mark" />
              <span className="label">
                {maybe(`step.${step.name}`, maybe(`action.${step.name}`, step.label))}
              </span>
              <span className="text">{stepText(step)}</span>
            </div>
          ))}
        </Steps>
      )}

      {(job.has_subtitle || job.has_report) && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {job.has_subtitle && (
            <a className="btn" href={subtitleUrl(job.id)}>
              <Icon name="download" size={14} />
              {t('queue.download')}
            </a>
          )}
          {job.has_report && (
            <a className="btn" href={reportUrl(job.id)} target="_blank" rel="noopener">
              {t('queue.report')}
            </a>
          )}
        </div>
      )}
    </>
  )
}

export function Queue() {
  useLocale()
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? ''
  const status = (params.get('status') ?? '') as Filter
  const page = Number(params.get('page')) || 1
  const selected = Number(params.get('job')) || null
  const small = useIsSmall()
  const sheet = useRef<HTMLDialogElement>(null)
  const shell = useShell()
  const actions = useQueueActions()
  const { confirm } = useFeedback()
  const flat = q !== '' || status !== ''

  const set = (changes: Record<string, string | number | null>, keepPage = false) => {
    const next = new URLSearchParams(params)
    if (!keepPage) next.delete('page')
    for (const [key, value] of Object.entries(changes)) {
      if (value === null || value === '' || (key === 'page' && value === 1)) next.delete(key)
      else next.set(key, String(value))
    }
    setParams(next, { replace: true })
  }
  const select = (id: number | null) => set({ job: id }, true)

  const list = useQuery({
    queryKey: ['queue', 'list', q, status, page],
    queryFn: () => api.queue({ q, status, page }),
    refetchInterval: LIVE.fast,
    placeholderData: (previous) => previous,
  })
  const upcoming = useQuery({
    queryKey: ['queue', 'upcoming', 100],
    queryFn: () => api.upcoming(100),
    refetchInterval: LIVE.fast,
    enabled: !flat,
  })
  const recent = useQuery({
    queryKey: ['queue', 'recent', 8],
    queryFn: () => api.recent(8),
    refetchInterval: LIVE.fast,
    enabled: !flat,
  })

  useEffect(() => {
    if (small && selected) sheet.current?.showModal()
    else sheet.current?.close()
  }, [small, selected])

  const counts = list.data?.counts ?? {}
  const filters: ChipOption<Filter>[] = [
    { value: '', label: t('common.all'), count: counts.all },
    { value: 'running', label: t('queue.filter.running'), count: counts.running },
    { value: 'queued', label: t('queue.filter.queued'), count: counts.queued },
    { value: 'done', label: t('queue.filter.done'), count: counts.done },
    { value: 'failed', label: t('queue.filter.failed'), count: counts.failed, tone: 'bad' },
    { value: 'cancelled', label: t('queue.filter.cancelled'), count: counts.cancelled },
  ]
  const running = shell.data?.running ?? null
  const cancelQueued = async () => {
    const sure = await confirm({
      title: t('queue.cancelAll.title', { n: counts.queued ?? 0 }),
      body: t('queue.cancelAll.body'),
      confirm: t('queue.cancelAll.confirm'),
      danger: true,
    })
    if (sure) actions.cancelQueued.mutate()
  }
  const line = (job: JobRow, extra?: { lead?: ReactNode; children?: ReactNode }) => (
    <JobLine key={job.id} job={job} selected={job.id === selected} onSelect={() => select(job.id)} lead={extra?.lead}>
      {extra?.children}
    </JobLine>
  )
  const queuedActions = (job: JobRow, first: boolean) => (
    <>
      {!first && (
        <button
          type="button"
          className="icon-btn"
          aria-label={t('queue.moveItemTop', { title: jobName(job).title })}
          onClick={() => actions.top.mutate(job.id)}
        >
          <Icon name="up" size={14} />
        </button>
      )}
      <button
        type="button"
        className="icon-btn"
        aria-label={t('queue.cancelItem', { title: jobName(job).title })}
        onClick={() => actions.cancel.mutate(job.id)}
      >
        <Icon name="close" size={14} />
      </button>
    </>
  )

  return (
    <>
      <PageHead
        title={t('queue.title')}
        sub={
          list.data
            ? t('queue.counts', {
                running: counts.running ?? 0,
                queued: counts.queued ?? 0,
                failed: counts.failed ?? 0,
              })
            : undefined
        }
      >
        <SearchBox value={q} onChange={(value) => set({ q: value })} placeholder={t('queue.search')} hotkey="/" />
        {(counts.failed ?? 0) > 0 && (
          <button type="button" className="btn" disabled={actions.retryFailed.isPending} onClick={() => actions.retryFailed.mutate()}>
            {t('queue.retryFailed', { n: counts.failed ?? 0 })}
          </button>
        )}
        {(counts.queued ?? 0) > 0 && (
          <button type="button" className="btn" disabled={actions.cancelQueued.isPending} onClick={cancelQueued}>
            {t('queue.cancelQueued', { n: counts.queued ?? 0 })}
          </button>
        )}
      </PageHead>

      <Chips label={t('common.filter')} options={filters} value={status} onChange={(value) => set({ status: value })} />

      <div className="queue-layout">
        <section className="card queue-list" aria-label={t('queue.tasks')}>
          {!list.data ? (
            <Loading error={list.error} />
          ) : flat ? (
            list.data.rows.length === 0 ? (
              <Empty icon="search" title={t('queue.noMatch')}>
                <button type="button" className="btn" onClick={() => set({ q: '', status: '' })}>
                  {t('common.clearFilters')}
                </button>
              </Empty>
            ) : (
              <div className="rows">{list.data.rows.map((job) => line(job))}</div>
            )
          ) : (counts.all ?? 0) === 0 ? (
            <Empty icon="queue" title={t('queue.empty')}>
              <p>
                {rich('queue.emptyBody', {
                  movies: (text) => <Link to="/movies">{text}</Link>,
                  shows: (text) => <Link to="/tv">{text}</Link>,
                })}
              </p>
            </Empty>
          ) : (
            <>
              <h2 className="section-label live">{t('common.runningNow')}</h2>
              {running ? (
                <button
                  type="button"
                  className="running-card"
                  aria-pressed={running.id === selected}
                  onClick={() => select(running.id)}
                >
                  <Icon name="spinner" size={22} className="accent spin" />
                  <span className="grow" style={{ display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0 }}>
                    <span className="truncate" style={{ fontWeight: 600 }}>
                      {jobName(running).title}
                      {jobName(running).sub && (
                        <span className="muted" style={{ fontWeight: 400 }}> · {jobName(running).sub}</span>
                      )}
                    </span>
                    <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                      <span style={{ flex: 1 }}>
                        <Bar value={running.progress} raised label={stepOf(running)} />
                      </span>
                      <span className="mono">{percent(running.progress)}</span>
                    </span>
                    <span className="sub truncate" style={{ fontSize: 12, color: 'var(--text-2)' }}>
                      {summary(running)} · {t('common.elapsed', { time: duration(running.started_at, null) })}
                    </span>
                  </span>
                </button>
              ) : (
                <p className="muted" style={{ padding: '0 18px 12px' }}>
                  {t('queue.nothingRunning')}
                </p>
              )}

              {!!upcoming.data?.length && (
                <>
                  <h2 className="section-label" style={{ borderTop: '1px solid var(--line)' }}>
                    {t('queue.upNext', { n: counts.queued ?? upcoming.data.length })}
                  </h2>
                  <div className="rows">
                    {upcoming.data.map((job, index) =>
                      line(job, {
                        lead: (
                          <span className="mono muted" style={{ width: 18, textAlign: 'right' }}>
                            {index + 1}
                          </span>
                        ),
                        children: queuedActions(job, index === 0),
                      }),
                    )}
                  </div>
                </>
              )}

              {!!recent.data?.length && (
                <>
                  <h2 className="section-label" style={{ borderTop: '1px solid var(--line)' }}>
                    {t('queue.finished')}
                  </h2>
                  <div className="rows">{recent.data.map((job) => line(job))}</div>
                  <div className="row" style={{ borderTop: '1px solid var(--line-soft)' }}>
                    <button type="button" className="btn small" onClick={() => set({ status: 'done' })}>
                      {t('queue.showFinished')}
                    </button>
                  </div>
                </>
              )}
            </>
          )}
          {flat && list.data && (
            <div style={{ padding: 12 }}>
              <Pager page={list.data.page} pages={list.data.pages} onPage={(value) => set({ page: value }, true)} />
            </div>
          )}
        </section>

        <section className="card queue-detail" aria-label={t('queue.details')}>
          {selected && !small ? (
            <JobPanel id={selected} />
          ) : (
            <Empty icon="queue" title={t('queue.selectTask')}>
              <p>{t('queue.selectBody')}</p>
            </Empty>
          )}
        </section>
      </div>

      <dialog ref={sheet} className="sheet" onClose={() => select(null)} aria-label={t('queue.details')}>
        {selected && small && (
          <div className="card queue-detail" style={{ display: 'flex', position: 'static', border: 0, minHeight: '100%' }}>
            <JobPanel id={selected} onClose={() => select(null)} />
          </div>
        )}
      </dialog>
    </>
  )
}
