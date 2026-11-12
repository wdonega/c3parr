// A casca: menu lateral no desktop, barra de abas no celular, busca do topo e o que roda agora.
import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useRegisterSW } from 'virtual:pwa-register/react'
import { api, LIVE } from '../api/client'
import type { Shell as ShellData } from '../api/types'
import { getLocale, LOCALE_NAMES, LOCALES, setLocale, t, useLocale, type Locale } from '../i18n'
import { INSTANCE, withBase } from '../lib/base'
import { jobName, percent, stepLabel } from '../lib/format'
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
    document.title = `${title} · ${INSTANCE}`
  }, [title])
  return (
    <header className="page-head">
      <div className="page-title">
        {crumbs && (
          <nav className="crumbs" aria-label={t('nav.breadcrumb')}>
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

/** Troca o idioma da interface. A escolha vale para este navegador e fica guardada nele. */
export function LanguagePicker({ compact }: { compact?: boolean }) {
  return (
    <label className={compact ? 'language compact' : 'row language'}>
      <Icon name="globe" size={compact ? 14 : 18} className={compact ? undefined : 'accent'} />
      <span className={compact ? 'sr-only' : 'grow'}>{t('common.language')}</span>
      <select
        aria-label={compact ? t('common.language') : undefined}
        value={getLocale()}
        onChange={(event) => setLocale(event.target.value as Locale)}
      >
        {LOCALES.map((locale) => (
          <option key={locale} value={locale} lang={locale}>
            {LOCALE_NAMES[locale]}
          </option>
        ))}
      </select>
    </label>
  )
}

function Sidebar({ shell, onSearch }: { shell?: ShellData; onSearch: () => void }) {
  const running = shell?.running
  return (
    <nav className="side" aria-label={t('nav.main')}>
      <Link to="/dashboard" className="brand">
        <img src={withBase('/logo.png')} alt="" />
        <span>C3PARR</span>
      </Link>
      <div className="nav-group">
        <Item to="/dashboard" icon="overview" label={t('nav.overview')} />
        <button type="button" className="nav-item" onClick={onSearch} style={{ border: 0, background: 'none' }}>
          <Icon name="search" size={18} />
          <span>{t('nav.search')}</span>
          <span className="kbd" style={{ marginLeft: 'auto' }}>
            ⌘K
          </span>
        </button>
      </div>
      <div className="nav-group">
        <span className="nav-label">{t('nav.library')}</span>
        <Item to="/movies" icon="movies" label={t('nav.movies')}>
          {shell && <span className="count">{shell.movies}</span>}
        </Item>
        <Item to="/tv" icon="tv" label={t('nav.shows')}>
          {shell && <span className="count">{shell.shows}</span>}
        </Item>
      </div>
      <div className="nav-group">
        <span className="nav-label">{t('nav.activity')}</span>
        <Item to="/queue" icon="queue" label={t('nav.queue')}>
          {!!shell?.pending && (
            <span className="badge" aria-label={t('nav.pending', { n: shell.pending })}>
              {shell.pending}
            </span>
          )}
        </Item>
      </div>
      <div className="nav-group">
        <span className="nav-label">{t('nav.admin')}</span>
        <Item to="/settings" icon="settings" label={t('nav.settings')} />
        <Item to="/system" icon="system" label={t('nav.system')}>
          {!!shell?.alerts_unseen && (
            <span className="badge bad" aria-label={t('nav.alerts', { n: shell.alerts_unseen })}>
              {shell.alerts_unseen}
            </span>
          )}
        </Item>
      </div>
      {running && (
        <Link to={`/queue?job=${running.id}`} className="now">
          <span className="eyebrow live">
            <span className="dot" />
            {t('common.runningNow')}
          </span>
          <span className="truncate" style={{ fontSize: 13, fontWeight: 500 }}>
            {jobName(running).title}
          </span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ flex: 1 }}>
              <Bar value={running.progress} label={stepLabel(running.action, running.language, running.step)} />
            </span>
            <span className="mono" style={{ fontSize: 11, color: 'var(--text-2)' }}>
              {percent(running.progress)}
            </span>
          </span>
        </Link>
      )}
      <div className="side-foot" style={running ? undefined : { marginTop: 'auto' }}>
        <LanguagePicker compact />
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
    <nav className="tabbar" aria-label={t('nav.main')}>
      {tab('/dashboard', 'overview', t('nav.overview'))}
      {tab('/movies', 'movies', t('nav.movies'))}
      {tab('/tv', 'tv', t('nav.shows'))}
      {tab('/queue', 'queue', shell?.pending ? t('nav.queueCount', { n: shell.pending }) : t('nav.queue'))}
      <Link to="/more" aria-current={more ? 'page' : undefined}>
        <Icon name="more" size={20} />
        {shell?.alerts_unseen ? t('nav.moreCount', { n: shell.alerts_unseen }) : t('nav.more')}
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
    <dialog ref={dialog} className="palette" onClose={close} aria-label={t('search.title')}>
      <label className="search">
        <Icon name="search" />
        <input
          type="search"
          value={text}
          placeholder={t('search.title')}
          aria-label={t('search.title')}
          autoComplete="off"
          onChange={(event) => setText(event.target.value)}
        />
        <span className="kbd">esc</span>
      </label>
      <div className="rows">
        {movies.map((m) => hit(`m${m.id}`, m.title, m.year, m.poster, `/movies?q=${encodeURIComponent(m.title)}`))}
        {shows.map((s) => hit(`s${s.id}`, s.title, s.year, s.poster, `/tv/${s.id}`))}
        {text.trim() && found.isSuccess && movies.length + shows.length === 0 && (
          <Empty icon="search" title={t('search.none')}>
            <p>{t('search.noMatch', { text: text.trim() })}</p>
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
  // na primeira visita o service worker acabou de ser instalado: não há versão anterior a trocar
  const [updating] = useState(() => !!navigator.serviceWorker?.controller)
  if (!ready || !updating) return null
  return (
    <div className="notice" role="status" style={{ position: 'sticky', top: 12, zIndex: 10 }}>
      <Icon name="refresh" size={18} className="accent" />
      <div className="grow">
        <strong>{t('update.title')}</strong>
        <span>{t('update.body')}</span>
      </div>
      <button type="button" className="btn" onClick={() => setReady(false)}>
        {t('update.later')}
      </button>
      <button type="button" className="btn primary" onClick={() => void updateServiceWorker(true)}>
        {t('update.reload')}
      </button>
    </div>
  )
}

export function Shell() {
  useLocale()
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
  useLocale()
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
      <PageHead title={t('nav.more')} />
      <section className="card rows">
        <button type="button" className="row row-btn" style={{ borderTop: 0 }} onClick={() => setSearching(true)}>
          <Icon name="search" size={18} className="accent" />
          <span className="grow">{t('search.title')}</span>
          <Icon name="right" className="muted" />
        </button>
        {link('/settings', 'settings', t('nav.settings'))}
        {link(
          '/system',
          'system',
          t('nav.system'),
          shell.data?.alerts_unseen ? <span className="badge bad">{shell.data.alerts_unseen}</span> : undefined,
        )}
      </section>
      <section className="card">
        <LanguagePicker />
      </section>
      <Palette open={searching} onClose={() => setSearching(false)} />
    </>
  )
}

export function NotFound() {
  useLocale()
  return (
    <>
      <PageHead title={t('notFound.title')} />
      <section className="card">
        <Empty icon="alert" title={t('notFound.body')}>
          <Link to="/dashboard" className="btn">
            {t('notFound.back')}
          </Link>
        </Empty>
      </section>
    </>
  )
}
