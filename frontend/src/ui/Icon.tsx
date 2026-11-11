// Ícones de traço, desenhados aqui: nada vem de fonte de ícones nem de CDN.
const PATHS = {
  overview: 'M3 3h7v9H3zM14 3h7v5h-7zM14 12h7v9h-7zM3 16h7v5H3z',
  movies:
    'M4 3h16a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zM7 3v18M17 3v18M3 8h4M3 12h18M3 16h4M17 8h4M17 16h4',
  tv: 'M3 7h18v13H3zM8 3l4 4 4-4',
  queue: 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
  settings: 'M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1M15 4v4M9 10v4M17 16v4',
  system: 'M3 12h4l3-8 4 16 3-8h4',
  more: 'M5 12h.01M12 12h.01M19 12h.01',
  search: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-4.3-4.3',
  check: 'M5 12l5 5L20 7',
  close: 'M6 6l12 12M18 6L6 18',
  plus: 'M12 5v14M5 12h14',
  play: 'M7 4l13 8-13 8z',
  refresh: 'M21 12a9 9 0 1 1-3-6.7M21 4v5h-5',
  alert: 'M12 3l10 18H2zM12 10v5M12 18h.01',
  info: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 11v6M12 7.5h.01',
  bookmark: 'M6 3h12v18l-6-4-6 4z',
  right: 'M9 6l6 6-6 6',
  down: 'M6 9l6 6 6-6',
  left: 'M15 6l-6 6 6 6',
  up: 'M12 19V6M6 12l6-6 6 6',
  copy: 'M9 9h11v11H9zM5 15H4V4h11v1',
  grid: 'M4 4h6v7H4zM14 4h6v7h-6zM4 14h6v6H4zM14 14h6v6h-6z',
  list: 'M4 6h16M4 12h16M4 18h16',
  spinner: 'M12 3a9 9 0 1 1-9 9',
  trash: 'M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13',
  download: 'M12 4v11M7 11l5 5 5-5M5 20h14',
  pause: 'M8 5v14M16 5v14',
} as const

export type IconName = keyof typeof PATHS

const FILLED: ReadonlySet<IconName> = new Set(['play', 'bookmark'])

export function Icon({
  name,
  size = 16,
  filled,
  className,
}: {
  name: IconName
  size?: number
  filled?: boolean
  className?: string
}) {
  const solid = filled ?? FILLED.has(name)
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={solid ? 'currentColor' : 'none'}
      stroke={solid ? 'none' : 'currentColor'}
      strokeWidth={name === 'check' ? 2.6 : 1.9}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={{ flex: 'none' }}
    >
      <path d={PATHS[name]} />
    </svg>
  )
}
