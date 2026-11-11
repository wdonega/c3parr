// A página de uma série: temporadas que abrem e fecham, episódios pelo título, seleção.
import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, ApiError, LIVE } from '../api/client'
import type { EpisodeRow, Season } from '../api/types'
import { plural } from '../lib/format'
import { NotFound, PageHead } from '../shell/Shell'
import { Bar, Loading, Switch } from '../ui/controls'
import { Icon } from '../ui/Icon'
import { ItemJob, Poster, Tracks } from '../ui/media'
import { useLibraryActions } from './Library'

const seasonKey = (season: Season) => String(season.season ?? 'other')
const episodeKey = (episode: EpisodeRow) => `episode:${episode.id}`
const complete = (episode: EpisodeRow) => episode.missing.length === 0
/** Falta legenda e ninguém está cuidando: é o que "Add missing" manda para a fila. */
const waiting = (episode: EpisodeRow) => !complete(episode) && !episode.job

function SeasonBlock({
  season,
  open,
  onToggle,
  picked,
  onPick,
  onAdd,
  busy,
}: {
  season: Season
  open: boolean
  onToggle: () => void
  picked: Set<string>
  onPick: (keys: string[], on: boolean) => void
  onAdd: (keys: string[]) => void
  busy: boolean
}) {
  const episodes = season.episodes
  const done = episodes.filter(complete).length
  const queued = episodes.filter((e) => e.job).length
  const missing = episodes.filter(waiting)
  const keys = episodes.map(episodeKey)
  const allPicked = keys.length > 0 && keys.every((key) => picked.has(key))
  const name = season.season === null ? 'Other files' : `Season ${season.season}`

  return (
    <div>
      <div className="season-head" style={{ padding: 0 }}>
        <button
          type="button"
          className="season-head"
          style={{ flex: 1, width: 'auto' }}
          aria-expanded={open}
          onClick={onToggle}
        >
          <Icon name={open ? 'down' : 'right'} className={open ? 'accent' : 'muted'} />
          <span style={{ fontWeight: 600 }}>{name}</span>
          <span className="muted" style={{ fontSize: 13 }}>
            {done === episodes.length ? plural(episodes.length, 'episode') : `${done} of ${episodes.length} complete`}
          </span>
          {done < episodes.length && (
            <span style={{ flex: '0 1 140px' }}>
              <Bar value={episodes.length ? done / episodes.length : 0} label={`${name} complete`} />
            </span>
          )}
          <span className="spacer" />
          {queued > 0 && (
            <span className="accent" style={{ fontSize: 13 }}>
              {queued} in the queue
            </span>
          )}
          {done === episodes.length && (
            <span className="ok" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
              <Icon name="check" size={14} />
              Complete
            </span>
          )}
        </button>
        {missing.length > 0 && (
          <button
            type="button"
            className="btn primary small"
            style={{ marginRight: 18 }}
            disabled={busy}
            onClick={() => onAdd(missing.map(episodeKey))}
          >
            Add {missing.length} missing
          </button>
        )}
      </div>
      {open && (
        <div className="season-body rows">
          <label className="row" style={{ minHeight: 36, fontSize: 13, color: 'var(--muted)' }}>
            <input
              type="checkbox"
              className="check"
              checked={allPicked}
              onChange={(event) => onPick(keys, event.target.checked)}
            />
            Select every episode of this season
          </label>
          {episodes.map((episode) => (
            <div key={episode.id} className="row">
              <input
                type="checkbox"
                className="check"
                aria-label={`Select ${episode.title ?? episode.name}`}
                checked={picked.has(episodeKey(episode))}
                onChange={(event) => onPick([episodeKey(episode)], event.target.checked)}
              />
              <span className="ep-n mono muted">
                {episode.episode === null ? '' : String(episode.episode).padStart(2, '0')}
              </span>
              <span className="grow truncate" title={episode.name}>
                {episode.title ?? episode.name}
              </span>
              <Tracks subs={episode.subs} missing={episode.missing} job={episode.job} />
              <span className="ep-state">
                {episode.job ? (
                  <ItemJob job={episode.job} bar />
                ) : complete(episode) ? null : (
                  <button
                    type="button"
                    className="btn small"
                    disabled={busy}
                    onClick={() => onAdd([episodeKey(episode)])}
                  >
                    Make {episode.missing.join(', ')}
                  </button>
                )}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export function Show() {
  const id = Number(useParams().id)
  const { add, monitor, sync } = useLibraryActions()
  const show = useQuery({
    queryKey: ['show', id],
    queryFn: () => api.show(id),
    refetchInterval: LIVE.normal,
    retry: false,
  })
  const [toggled, setToggled] = useState<Set<string> | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const data = show.data
  // até a pessoa mexer, ficam abertas as temporadas onde há o que fazer; as completas, fechadas
  const open = useMemo(() => {
    if (toggled || !data) return toggled
    const busy = data.seasons.filter((s) => s.episodes.some((e) => !complete(e) || e.job))
    return new Set((busy.length ? busy : data.seasons.slice(0, 1)).map(seasonKey))
  }, [toggled, data])

  if (show.error instanceof ApiError && show.error.status === 404) return <NotFound />
  if (!data) {
    return (
      <>
        <PageHead title="TV show" crumbs={{ label: 'TV Shows', to: '/tv' }} />
        <Loading error={show.error} />
      </>
    )
  }

  const episodes = data.seasons.flatMap((season) => season.episodes)
  const done = episodes.filter(complete).length
  const queued = episodes.filter((e) => e.job).length
  const missing = episodes.filter(waiting).length
  const pick = (keys: string[], on: boolean) =>
    setPicked((old) => {
      const next = new Set(old)
      for (const key of keys) {
        if (on) next.add(key)
        else next.delete(key)
      }
      return next
    })
  const toggleSeason = (key: string) => {
    const next = new Set(open)
    if (!next.delete(key)) next.add(key)
    setToggled(next)
  }

  return (
    <>
      <PageHead title={data.title} crumbs={{ label: 'TV Shows', to: '/tv' }}>
        <button type="button" className="btn" disabled={sync.isPending} onClick={() => sync.mutate('sonarr')}>
          <Icon name="refresh" size={15} />
          Sync with Sonarr
        </button>
      </PageHead>

      <section className="card pad" aria-label="Show" style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 20 }}>
        <Poster title={data.title} src={data.poster} size="small" />
        <div style={{ flex: '1 1 320px', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', gap: 10 }}>
            <span style={{ fontSize: 26, fontWeight: 600, letterSpacing: '-0.02em' }}>{data.title}</span>
            <span className="muted num" style={{ fontSize: 15 }}>
              {data.year}
            </span>
          </div>
          <span className="mono muted" style={{ overflowWrap: 'anywhere' }}>
            {data.folder}
          </span>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 520 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, fontSize: 13 }}>
              <span style={{ flex: 1, color: 'var(--text-2)' }}>Episodes with every subtitle</span>
              <span className="num" style={{ fontWeight: 600 }}>
                {done} <span className="muted" style={{ fontWeight: 400 }}>/ {episodes.length}</span>
              </span>
            </div>
            <Bar value={episodes.length ? done / episodes.length : 0} thick label="Episodes with every subtitle" />
            <span className="muted" style={{ fontSize: 12 }}>
              {queued > 0 ? `${queued} in the queue` : missing > 0 ? `${missing} missing` : 'Nothing to do'}
            </span>
          </div>
        </div>
        <div style={{ flex: '0 1 260px', display: 'flex', flexDirection: 'column', gap: 12, alignItems: 'flex-start' }}>
          <div className="switch-row">
            <Switch
              checked={data.monitored}
              label="Monitored"
              onChange={(monitored) => monitor.mutate({ kind: 'series', id: data.id, monitored })}
            />
            <div>
              <span className="name">Monitored</span>
              <span className="hint muted">New episodes get their subtitles in the routine</span>
            </div>
          </div>
          {missing > 0 && (
            <button
              type="button"
              className="btn primary"
              disabled={add.isPending}
              onClick={() => add.mutate([`series:${data.id}`])}
            >
              <Icon name="plus" size={14} />
              Add {missing} missing to the queue
            </button>
          )}
        </div>
      </section>

      <section className="card rows" aria-label="Seasons" style={{ overflow: 'hidden' }}>
        {data.seasons.map((season) => (
          <SeasonBlock
            key={seasonKey(season)}
            season={season}
            open={open?.has(seasonKey(season)) ?? false}
            onToggle={() => toggleSeason(seasonKey(season))}
            picked={picked}
            onPick={pick}
            onAdd={(keys) => add.mutate(keys)}
            busy={add.isPending}
          />
        ))}
      </section>

      {picked.size > 0 && (
        <div className="bulk" role="region" aria-label="Selection">
          <strong>{plural(picked.size, 'episode')} selected</strong>
          <button type="button" className="btn small" onClick={() => setPicked(new Set())}>
            Clear
          </button>
          <button
            type="button"
            className="btn primary small"
            disabled={add.isPending}
            onClick={() => add.mutate([...picked], { onSuccess: () => setPicked(new Set()) })}
          >
            Add to the queue
          </button>
        </div>
      )}
    </>
  )
}
