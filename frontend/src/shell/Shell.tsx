// A casca: menu lateral no desktop, barra de abas no celular, busca do topo e o que roda agora.
import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useRegisterSW } from 'virtual:pwa-register/react'
import { api, LIVE } from '../api/client'
import type { Shell as ShellData } from '../api/types'
import { jobName, percent } from '../lib/format'
import { Bar, Empty } from '../ui/controls'
import { Icon, type IconName } from '../ui/Icon'
import { Poster } from '../ui/media'

export function useShell() {
  return useQuery({ queryKey: ['shell'], queryFn: api.shell, refetchInterval: LIVE.fast })
}

/** Título da página: no topo e na aba do navegador. */
export function PageHead({
  title,
  sub,
  crumbs,
  children,
}: {
  title: string
  sub?: ReactNode
  crumbs?: { label: string; to: string }
  children?: ReactNode
}) {
  useEffect(() => {
    document.title = `${title} · c3parr`
  }, [title])
  return (
    <header className="page-head">
      <div className="page-title">
        {crumbs && (
          <nav className="crumbs" aria-label="Breadcrumb">
            <Link to={crumbs.to}>{crumbs.label}</Link>
            <Icon name="right" size={14} className="muted" />
            <span aria-current="page">{title}</span>
          </nav>
        )}
        {crumbs ? <h1 className="sr-only">{title}</h1> : <h1>{title}</h1>}
        {sub && <p>{sub}</p>}
      </div>
      {children}
    </header>
  )
}

function Item({
  to,
  icon,
  label,
  children,
}: {
  to: string
  icon: IconName
  label: string
  children?: ReactNode
}) {
  return (
    <NavLink to={to} className="nav-item">
      <Icon name={icon} size={18} />
      <span>{label}</span>
      {children}
    </NavLink>
  )
}

function Sidebar({ shell, onSearch }: { shell?: ShellData; onSearch: () => void }) {
  const running = shell?.running
  return (
    <nav className="side" aria-label="Main">
      <Link to="/dashboard" className="brand">
        <img src="/logo.png" alt="" />
        <span>C3PARR</span>
      </Link>
      <div className="nav-group">
        <Item to="/dashboard" icon="overview" label="Overview" />
        <button type="button" className="nav-item" onClick={onSearch} style={{ border: 0, background: 'none' }}>
          <Icon name="search" size={18} />
          <span>Search</span>
          <span className="kbd" style={{ marginLeft: 'auto' }}>
            ⌘K
          </span>
        </button>
      </div>
      <div className="nav-group">
        <span className="nav-label">Library</span>
        <Item to="/movies" icon="movies" label="Movies">
          {shell && <span className="count">{shell.movies}</span>}
        </Item>
        <Item to="/tv" icon="tv" label="TV Shows">
          {shell && <span className="count">{shell.shows}</span>}
        </Item>
      </div>
      <div className="nav-group">
        <span className="nav-label">Activity</span>
        <Item to="/queue" icon="queue" label="Queue">
          {!!shell?.pending && (
            <span className="badge" aria-label={`${shell.pending} unfinished tasks`}>
              {shell.pending}
            </span>
          )}
        </Item>
      </div>
      <div className="nav-group">
        <span className="nav-label">Admin</span>
        <Item to="/settings" icon="settings" label="Settings" />
        <Item to="/system" icon="system" label="System">
          {!!shell?.alerts_unseen && (
            <span className="badge bad" aria-label={`${shell.alerts_unseen} alerts to look at`}>
              {shell.alerts_unseen}
            </span>
          )}
        </Item>
      </div>
      {running && (
        <Link to={`/queue?job=${running.id}`} className="now">
          <span className="eyebrow live">
            <span className="dot" />
            Running now
          </span>
          <span className="truncate" style={{ fontSize: 13, fontWeight: 500 }}>
            {jobName(running).title}
          </span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ flex: 1 }}>
              <Bar value={running.progress} label={running.step} />
            </span>
            <span className="mono" style={{ fontSize: 11, color: 'var(--text-2)' }}>
              {percent(running.progress)}
            </span>
          </span>
        </Link>
      )}
      <div className="side-foot" style={running ? undefined : { marginTop: 'auto' }}>
        {shell && (
          <a className="mono" href={shell.release_url} target="_blank" rel="noopener">
            v{shell.version}
          </a>
        )}
        <span>·</span>
        {shell && (
          <a href={shell.repo_url} target="_blank" rel="noopener">
            GitHub
          </a>
        )}
      </div>
    </nav>
  )
}

function TabBar({ shell }: { shell?: ShellData }) {
  const { pathname } = useLocation()
  const more = ['/settings', '/system', '/more'].some((prefix) => pathname.startsWith(prefix))
  const tab = (to: string, icon: IconName, label: string) => (
    <NavLink to={to}>
      <Icon name={icon} size={20} />
      {label}
    </NavLink>
  )
  return (
    <nav className="tabbar" aria-label="Main">
      {tab('/dashboard', 'overview', 'Overview')}
      {tab('/movies', 'movies', 'Movies')}
      {tab('/tv', 'tv', 'TV Shows')}
      {tab('/queue', 'queue', shell?.pending ? `Queue · ${shell.pending}` : 'Queue')}
      <Link to="/more" aria-current={more ? 'page' : undefined}>
        <Icon name="more" size={20} />
        {shell?.alerts_unseen ? `More · ${shell.alerts_unseen}` : 'More'}
      </Link>
    </nav>
  )
}

/** Busca do topo (⌘K): acha filmes e séries pelo título ou ano e leva até eles. */
function Palette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [text, setText] = useState('')
  const navigate = useNavigate()
  const found = useQuery({
    queryKey: ['search', text],
    queryFn: () => api.search(text),
    enabled: open && text.trim().length > 0,
  })

  useEffect(() => {
    if (open) dialog.current?.showModal()
    else dialog.current?.close()
  }, [open])

  const close = () => {
    setText('')
    onClose()
  }
  const go = (to: string) => {
    close()
    navigate(to)
  }
  const movies = found.data?.movies ?? []
  const shows = found.data?.shows ?? []
  const hit = (key: string, title: string, year: number | null, poster: string | null, to: string) => (
    <button key={key} type="button" className="row row-btn" onClick={() => go(to)}>
      <Poster title={title} src={poster} size="thumb" />
      <span className="grow truncate">{title}</span>
      <span className="muted num">{year}</span>
    </button>
  )

  return (
    <dialog ref={dialog} className="palette" onClose={close} aria-label="Search movies and shows">
      <label className="search">
        <Icon name="search" />
        <input
          type="search"
          value={text}
          placeholder="Search movies and shows"
          aria-label="Search movies and shows"
          autoComplete="off"
          onChange={(event) => setText(event.target.value)}
        />
        <span className="kbd">esc</span>
      </label>
      <div className="rows">
        {movies.map((m) => hit(`m${m.id}`, m.title, m.year, m.poster, `/movies?q=${encodeURIComponent(m.title)}`))}
        {shows.map((s) => hit(`s${s.id}`, s.title, s.year, s.poster, `/tv/${s.id}`))}
        {text.trim() && found.isSuccess && movies.length + shows.length === 0 && (
          <Empty icon="search" title="Nothing found">
            <p>No movie or show matches “{text.trim()}”.</p>
          </Empty>
        )}
      </div>
    </dialog>
  )
}

/** Saiu versão nova do c3parr e a aba ainda está na antiga: oferece recarregar. */
function UpdateNotice() {
  const {
    needRefresh: [ready, setReady],
    updateServiceWorker,
  } = useRegisterSW()
  if (!ready) return null
  return (
    <div className="notice" role="status" style={{ position: 'sticky', top: 12, zIndex: 10 }}>
      <Icon name="refresh" size={18} className="accent" />
      <div className="grow">
        <strong>A new version of c3parr is ready</strong>
        <span>Reload to use it. Nothing you saved is lost.</span>
      </div>
      <button type="button" className="btn" onClick={() => setReady(false)}>
        Later
      </button>
      <button type="button" className="btn primary" onClick={() => void updateServiceWorker(true)}>
        Reload
      </button>
    </div>
  )
}

export function Shell() {
  const shell = useShell()
  const [searching, setSearching] = useState(false)

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setSearching(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <div className="shell">
      <Sidebar shell={shell.data} onSearch={() => setSearching(true)} />
      <main className="main">
        <UpdateNotice />
        <Outlet />
      </main>
      <TabBar shell={shell.data} />
      <Palette open={searching} onClose={() => setSearching(false)} />
    </div>
  )
}

/** No celular, o que não cabe na barra de abas. */
export function More() {
  const shell = useShell()
  const [searching, setSearching] = useState(false)
  const link = (to: string, icon: IconName, label: string, note?: ReactNode) => (
    <Link to={to} className="row" style={{ color: 'var(--text)' }}>
      <Icon name={icon} size={18} className="accent" />
      <span className="grow">{label}</span>
      {note}
      <Icon name="right" className="muted" />
    </Link>
  )
  return (
    <>
      <PageHead title="More" />
      <section className="card rows">
        <button type="button" className="row row-btn" style={{ borderTop: 0 }} onClick={() => setSearching(true)}>
          <Icon name="search" size={18} className="accent" />
          <span className="grow">Search movies and shows</span>
          <Icon name="right" className="muted" />
        </button>
        {link('/settings', 'settings', 'Settings')}
        {link(
          '/system',
          'system',
          'System',
          shell.data?.alerts_unseen ? <span className="badge bad">{shell.data.alerts_unseen}</span> : undefined,
        )}
      </section>
      <Palette open={searching} onClose={() => setSearching(false)} />
    </>
  )
}

export function NotFound() {
  return (
    <>
      <PageHead title="Page not found" />
      <section className="card">
        <Empty icon="alert" title="There is nothing at this address">
          <Link to="/dashboard" className="btn">
            Go to the overview
          </Link>
        </Empty>
      </section>
    </>
  )
}
