// Movies e TV Shows: pôsteres ou tabela, busca, filtros com contagem, ordenação e seleção.
import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, LIVE, type ItemKind, type ListParams } from '../api/client'
import type { MovieRow, ShowRow } from '../api/types'
import { t, useLocale } from '../i18n'
import { timeAgo } from '../lib/format'
import { PageHead } from '../shell/Shell'
import { Bar, Chips, Empty, Loading, Pager, SearchBox, type ChipOption } from '../ui/controls'
import { useFeedback } from '../ui/feedback'
import { Icon } from '../ui/Icon'
import { ItemJob, Poster, TrackLegend, Tracks } from '../ui/media'

type Filter = '' | 'missing' | 'complete' | 'queued'
type View = 'posters' | 'table'
const VIEW_KEY = 'c3parr.library.view'

/** Busca, filtro, ordem e página ficam no endereço: recarregar ou voltar mantém a lista. */
export function useListParams() {
  const [params, setParams] = useSearchParams()
  const values: Required<ListParams> = {
    q: params.get('q') ?? '',
    show: params.get('show') ?? '',
    sort: params.get('sort') ?? 'title',
    page: Number(params.get('page')) || 1,
  }
  const set = (changes: Partial<Record<keyof ListParams, string | number>>) => {
    const next = new URLSearchParams(params)
    if (!('page' in changes)) next.delete('page') // mudou o filtro: volta para a primeira página
    for (const [key, value] of Object.entries(changes)) {
      const empty = value === '' || (key === 'sort' && value === 'title') || (key === 'page' && value === 1)
      if (empty) next.delete(key)
      else next.set(key, String(value))
    }
    setParams(next, { replace: true })
  }
  return { ...values, set }
}

export function refreshLibrary(client: QueryClient) {
  for (const key of ['movies', 'shows', 'show', 'shell', 'queue', 'dashboard']) {
    void client.invalidateQueries({ queryKey: [key] })
  }
}

/** Pedir legendas para itens e marcar/desmarcar monitorado. */
export function useLibraryActions() {
  const client = useQueryClient()
  const { report, toast } = useFeedback()
  const add = useMutation({
    mutationFn: (items: string[]) => api.addToQueue(items),
    onSuccess: (result) => {
      report(result)
      refreshLibrary(client)
    },
    onError: (error) => toast(error.message, true),
  })
  const monitor = useMutation({
    mutationFn: (v: { kind: ItemKind; id: number; monitored: boolean }) =>
      api.monitor(v.kind, v.id, v.monitored),
    onSuccess: () => refreshLibrary(client),
    onError: (error) => toast(error.message, true),
  })
  const sync = useMutation({
    mutationFn: (arr: 'radarr' | 'sonarr') => api.runTask(`sync_${arr}`),
    onSuccess: (result) => report(result),
    onError: (error) => toast(error.message, true),
  })
  return { add, monitor, sync }
}

function useView(): [View, (view: View) => void] {
  const [view, setView] = useState<View>(() =>
    localStorage.getItem(VIEW_KEY) === 'table' ? 'table' : 'posters',
  )
  return [
    view,
    (next) => {
      localStorage.setItem(VIEW_KEY, next)
      setView(next)
    },
  ]
}

export function MonitorButton({
  monitored,
  onToggle,
  title,
}: {
  monitored: boolean
  onToggle: () => void
  title: string
}) {
  return (
    <button
      type="button"
      className="icon-btn"
      aria-pressed={monitored}
      aria-label={t('library.monitoredItem', {
        state: monitored ? t('library.monitored') : t('library.notMonitored'),
        title,
      })}
      title={monitored ? t('library.monitoredHint') : t('library.notMonitored')}
      onClick={onToggle}
    >
      <Icon name="bookmark" size={14} filled={monitored} />
    </button>
  )
}

type Common = {
  kind: ItemKind
  arr: 'radarr' | 'sonarr'
  counts?: Record<string, number>
  total: number
  page: number
  pages: number
  loading: boolean
  error: unknown
  keys: string[]
  posters: (selection: Selection) => ReactNode
  table: (selection: Selection) => ReactNode
}
type Selection = { on: boolean; has: (key: string) => boolean; toggle: (key: string) => void }

function ListPage(props: Common) {
  const list = useListParams()
  const [view, setView] = useView()
  const [selecting, setSelecting] = useState(false)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const { add, sync } = useLibraryActions()
  const counts = props.counts ?? {}
  const filters: ChipOption<Filter>[] = [
    { value: '', label: t('common.all'), count: counts.all },
    { value: 'missing', label: t('library.filter.missing'), count: counts.missing, tone: 'warn' },
    { value: 'complete', label: t('common.complete'), count: counts.complete },
    { value: 'queued', label: t('common.inQueue'), count: counts.queued },
  ]
  const selection: Selection = {
    on: selecting,
    has: (key) => picked.has(key),
    toggle: (key) =>
      setPicked((old) => {
        const next = new Set(old)
        if (!next.delete(key)) next.add(key)
        return next
      }),
  }
  const stopSelecting = () => {
    setSelecting(false)
    setPicked(new Set())
  }
  const filtered = list.q !== '' || list.show !== ''
  const arrName = props.arr === 'radarr' ? 'Radarr' : 'Sonarr'
  const what = props.kind === 'movie' ? 'movies' : 'shows'
  const count = t(`library.count.${what}`, { n: counts.all ?? 0, done: counts.complete ?? 0 })

  return (
    <>
      <PageHead
        title={t(`nav.${what}`)}
        sub={props.counts ? count + (list.q ? t('library.matching', { q: list.q }) : '') : undefined}
      >
        <SearchBox
          value={list.q}
          onChange={(q) => list.set({ q })}
          placeholder={t(`library.search.${what}`)}
          hotkey="/"
        />
        <button type="button" className="btn" disabled={sync.isPending} onClick={() => sync.mutate(props.arr)}>
          <Icon name="refresh" size={15} />
          {t('library.sync', { arr: arrName })}
        </button>
      </PageHead>

      <div className="toolbar">
        <Chips label={t('common.filter')} options={filters} value={list.show as Filter} onChange={(show) => list.set({ show })} />
        <span className="spacer" />
        <select
          className="select"
          aria-label={t('library.sortBy')}
          value={list.sort}
          onChange={(event) => list.set({ sort: event.target.value })}
        >
          <option value="title">{t('library.sort.title')}</option>
          <option value="added">{t('library.sort.added')}</option>
          <option value="missing">{t('library.sort.missing')}</option>
        </select>
        <div className="toggle-group" role="group" aria-label={t('library.view')}>
          <button type="button" aria-label={t('library.posters')} aria-pressed={view === 'posters'} onClick={() => setView('posters')}>
            <Icon name="grid" size={15} />
          </button>
          <button type="button" aria-label={t('library.table')} aria-pressed={view === 'table'} onClick={() => setView('table')}>
            <Icon name="list" size={15} />
          </button>
        </div>
        <button
          type="button"
          className="btn small"
          aria-pressed={selecting}
          onClick={() => (selecting ? stopSelecting() : setSelecting(true))}
        >
          {selecting ? t('library.doneSelecting') : t('library.select')}
        </button>
      </div>

      <TrackLegend />

      {props.loading ? (
        <Loading error={props.error} />
      ) : props.total === 0 ? (
        <section className="card">
          {filtered ? (
            <Empty icon="search" title={t('library.noMatch')}>
              <button type="button" className="btn" onClick={() => list.set({ q: '', show: '' })}>
                {t('common.clearFilters')}
              </button>
            </Empty>
          ) : (
            <Empty icon="movies" title={t(`library.empty.${what}`)}>
              <p>{t('library.emptyBody', { arr: arrName })}</p>
              <Link to={`/settings/library/${props.arr}`} className="btn">
                {t('library.connect', { arr: arrName })}
              </Link>
            </Empty>
          )}
        </section>
      ) : view === 'posters' ? (
        props.posters(selection)
      ) : (
        <section className="card table-wrap">{props.table(selection)}</section>
      )}

      <Pager page={props.page} pages={props.pages} onPage={(page) => list.set({ page })} />

      {selecting && (
        <div className="bulk" role="region" aria-label={t('common.selection')}>
          <strong>{picked.size ? t('library.selected', { n: picked.size }) : t('library.pick')}</strong>
          <button type="button" className="btn small" onClick={() => setPicked(new Set(props.keys))}>
            {t('library.selectPage')}
          </button>
          <button type="button" className="btn small" disabled={!picked.size} onClick={() => setPicked(new Set())}>
            {t('common.clear')}
          </button>
          <button
            type="button"
            className="btn primary small"
            disabled={!picked.size || add.isPending}
            onClick={() => add.mutate([...picked], { onSuccess: stopSelecting })}
          >
            {t('common.addToQueue')}
          </button>
        </div>
      )}
    </>
  )
}

function SelectBox({ selection, itemKey, title }: { selection: Selection; itemKey: string; title: string }) {
  if (!selection.on) return null
  return (
    <input
      type="checkbox"
      className="check"
      aria-label={t('common.selectItem', { title })}
      checked={selection.has(itemKey)}
      onChange={() => selection.toggle(itemKey)}
    />
  )
}

// --- filmes ---

function MovieAction({ movie, onAdd, busy }: { movie: MovieRow; onAdd: () => void; busy: boolean }) {
  if (movie.job) return <ItemJob job={movie.job} />
  if (movie.missing.length === 0) return null
  return (
    <button type="button" className="btn primary small" disabled={busy} onClick={onAdd}>
      <Icon name="plus" size={13} />
      {t('library.make', { langs: movie.missing.join(', ') })}
    </button>
  )
}

export function Movies() {
  useLocale()
  const list = useListParams()
  const { add, monitor } = useLibraryActions()
  const page = useQuery({
    queryKey: ['movies', list.q, list.show, list.sort, list.page],
    queryFn: () => api.movies(list),
    refetchInterval: LIVE.normal,
    placeholderData: (previous) => previous,
  })
  const rows = page.data?.rows ?? []
  const key = (movie: MovieRow) => `movie:${movie.id}`
  const toggle = (movie: MovieRow) =>
    monitor.mutate({ kind: 'movie', id: movie.id, monitored: !movie.monitored })

  return (
    <ListPage
      kind="movie"
      arr="radarr"
      counts={page.data?.counts}
      total={page.data?.total ?? 0}
      page={page.data?.page ?? 1}
      pages={page.data?.pages ?? 1}
      loading={!page.data}
      error={page.error}
      keys={rows.map(key)}
      posters={(selection) => (
        <div className="poster-grid">
          {rows.map((movie) => (
            <article key={movie.id} className="media">
              <Poster title={movie.title} src={movie.poster}>
                <div className="poster-top">
                  <SelectBox selection={selection} itemKey={key(movie)} title={movie.title} />
                  <span className="spacer" />
                  <MonitorButton monitored={movie.monitored} title={movie.title} onToggle={() => toggle(movie)} />
                </div>
              </Poster>
              <div className="title">
                <span title={movie.title}>{movie.title}</span>
                <span className="year num">{movie.year}</span>
              </div>
              <Tracks subs={movie.subs} others={movie.others} missing={movie.missing} job={movie.job} />
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
                <MovieAction movie={movie} busy={add.isPending} onAdd={() => add.mutate([key(movie)])} />
              </div>
            </article>
          ))}
        </div>
      )}
      table={(selection) => (
        <div className="rows" style={{ minWidth: 640 }}>
          {rows.map((movie) => (
            <div key={movie.id} className="row" style={{ borderTop: movie === rows[0] ? 0 : undefined }}>
              <SelectBox selection={selection} itemKey={key(movie)} title={movie.title} />
              <MonitorButton monitored={movie.monitored} title={movie.title} onToggle={() => toggle(movie)} />
              <div className="grow">
                <div className="truncate" style={{ fontWeight: 500 }}>
                  {movie.title} <span className="muted num">{movie.year}</span>
                </div>
                <div className="sub">{t('library.added', { when: timeAgo(movie.added_at) })}</div>
              </div>
              <Tracks subs={movie.subs} others={movie.others} missing={movie.missing} job={movie.job} wide />
              <span className="ep-state">
                <MovieAction movie={movie} busy={add.isPending} onAdd={() => add.mutate([key(movie)])} />
              </span>
            </div>
          ))}
        </div>
      )}
    />
  )
}

// --- séries ---

function ShowState({ show, onAdd, busy }: { show: ShowRow; onAdd: () => void; busy: boolean }) {
  if (show.missing > 0) {
    return (
      <button type="button" className="btn primary small" disabled={busy} onClick={onAdd}>
        <Icon name="plus" size={13} />
        {t('library.addMissing', { n: show.missing })}
      </button>
    )
  }
  if (show.queued) return <span className="accent">{t('common.inQueue')}</span>
  if (show.total > 0 && show.done === show.total) {
    return (
      <span className="ok" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        <Icon name="check" size={14} />
        {t('common.complete')}
      </span>
    )
  }
  return null
}

export function Shows() {
  useLocale()
  const list = useListParams()
  const { add, monitor } = useLibraryActions()
  const page = useQuery({
    queryKey: ['shows', list.q, list.show, list.sort, list.page],
    queryFn: () => api.shows(list),
    refetchInterval: LIVE.normal,
    placeholderData: (previous) => previous,
  })
  const rows = page.data?.rows ?? []
  const key = (show: ShowRow) => `series:${show.id}`
  const toggle = (show: ShowRow) =>
    monitor.mutate({ kind: 'series', id: show.id, monitored: !show.monitored })
  const progress = (show: ShowRow) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{ flex: 1 }}>
        <Bar value={show.total ? show.done / show.total : 0} label={t('overview.episodesAll')} />
      </span>
      <span className="muted num" style={{ fontSize: 12 }}>
        {t('library.doneOf', { done: show.done, total: show.total })}
      </span>
    </div>
  )

  return (
    <ListPage
      kind="series"
      arr="sonarr"
      counts={page.data?.counts}
      total={page.data?.total ?? 0}
      page={page.data?.page ?? 1}
      pages={page.data?.pages ?? 1}
      loading={!page.data}
      error={page.error}
      keys={rows.map(key)}
      posters={(selection) => (
        <div className="poster-grid">
          {rows.map((show) => (
            <article key={show.id} className="media">
              <Poster title={show.title} src={show.poster}>
                <div className="poster-top">
                  <SelectBox selection={selection} itemKey={key(show)} title={show.title} />
                  <span className="spacer" />
                  <MonitorButton monitored={show.monitored} title={show.title} onToggle={() => toggle(show)} />
                </div>
              </Poster>
              <div className="title">
                <Link to={`/tv/${show.id}`} title={show.title}>
                  {show.title}
                </Link>
                <span className="year num">{show.year}</span>
              </div>
              {progress(show)}
              <div style={{ fontSize: 13 }}>
                <ShowState show={show} busy={add.isPending} onAdd={() => add.mutate([key(show)])} />
              </div>
            </article>
          ))}
        </div>
      )}
      table={(selection) => (
        <div className="rows" style={{ minWidth: 640 }}>
          {rows.map((show) => (
            <div key={show.id} className="row" style={{ borderTop: show === rows[0] ? 0 : undefined }}>
              <SelectBox selection={selection} itemKey={key(show)} title={show.title} />
              <MonitorButton monitored={show.monitored} title={show.title} onToggle={() => toggle(show)} />
              <div className="grow">
                <Link to={`/tv/${show.id}`} className="truncate" style={{ display: 'block', fontWeight: 500 }}>
                  {show.title} <span className="muted num">{show.year}</span>
                </Link>
              </div>
              <div style={{ flex: '0 1 220px' }}>{progress(show)}</div>
              <span className="ep-state">
                <ShowState show={show} busy={add.isPending} onAdd={() => add.mutate([key(show)])} />
              </span>
            </div>
          ))}
        </div>
      )}
    />
  )
}
