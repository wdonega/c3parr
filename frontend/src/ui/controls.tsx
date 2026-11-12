// Controles pequenos e repetidos: barra de progresso, chips de filtro, liga/desliga, campo.
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { t } from '../i18n'
import { Icon, type IconName } from './Icon'

export function Bar({
  value,
  thick,
  raised,
  label,
}: {
  value: number
  thick?: boolean
  raised?: boolean
  label?: string
}) {
  const percent = Math.max(0, Math.min(100, value * 100))
  const classes = ['bar', thick && 'thick', raised && 'on-raised'].filter(Boolean).join(' ')
  return (
    <div
      className={classes}
      role="progressbar"
      aria-label={label ?? t('common.progress')}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(percent)}
    >
      <span style={{ width: `${percent}%` }} />
    </div>
  )
}

export type ChipOption<T extends string> = {
  value: T
  label: string
  count?: number
  tone?: 'warn' | 'bad'
}

export function Chips<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string
  options: ChipOption<T>[]
  value: T
  onChange: (value: T) => void
}) {
  return (
    <div className="chips" role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          className="chip"
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
          {option.count !== undefined && (
            <span className={`n ${option.count > 0 && option.tone ? option.tone : ''}`}>
              {option.count}
            </span>
          )}
        </button>
      ))}
    </div>
  )
}

export function Switch({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean
  onChange: (checked: boolean) => void
  label: string
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      className="switch"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
    />
  )
}

export function SwitchRow({
  name,
  help,
  checked,
  onChange,
  disabled,
  child,
}: {
  name: string
  help?: ReactNode
  checked: boolean
  onChange: (checked: boolean) => void
  disabled?: boolean
  child?: boolean
}) {
  return (
    <div className={`switch-row ${child ? 'child' : ''}`} data-off={disabled || undefined}>
      <Switch checked={checked} onChange={onChange} label={name} disabled={disabled} />
      <div>
        <span className="name">{name}</span>
        {help && <span className="hint muted">{help}</span>}
      </div>
    </div>
  )
}

export function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string
  options: { value: T; label: string }[]
  value: T
  onChange: (value: T) => void
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

/** Rótulo em cima, controle no meio, dica embaixo. O `label` envolve só o rótulo e o
    controle: a dica fica fora, para não virar parte do nome do campo. */
export function Field({
  label,
  hint,
  edited,
  optional,
  children,
}: {
  label: string
  hint?: ReactNode
  edited?: boolean
  optional?: boolean
  children: ReactNode
}) {
  return (
    <div className="field">
      <label>
        <span className="label">
          {label}
          {optional && <span className="muted">{t('common.optional')}</span>}
          {edited && <span className="edited">{t('common.edited')}</span>}
        </span>
        {children}
      </label>
      {hint && <span className="hint">{hint}</span>}
    </div>
  )
}

/** Busca que só avisa depois que a pessoa para de digitar. `hotkey` foca o campo. */
export function SearchBox({
  value,
  onChange,
  placeholder,
  hotkey,
}: {
  value: string
  onChange: (value: string) => void
  placeholder: string
  hotkey?: string
}) {
  const [text, setText] = useState(value)
  const [seen, setSeen] = useState(value)
  const input = useRef<HTMLInputElement>(null)
  const latest = useRef(onChange)

  if (seen !== value) {
    // o valor mudou por fora (voltar no navegador, "limpar filtros"): o campo acompanha
    setSeen(value)
    setText(value)
  }
  useEffect(() => {
    latest.current = onChange
  })
  useEffect(() => {
    if (text === value) return
    const timer = setTimeout(() => latest.current(text), 250)
    return () => clearTimeout(timer)
  }, [text, value])
  useEffect(() => {
    if (!hotkey) return
    const onKey = (event: KeyboardEvent) => {
      const typing = (event.target as HTMLElement | null)?.closest('input, textarea, select')
      if (event.key === hotkey && !typing && !event.metaKey && !event.ctrlKey) {
        event.preventDefault()
        input.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [hotkey])

  return (
    <label className="search">
      <Icon name="search" />
      <input
        ref={input}
        type="search"
        value={text}
        placeholder={placeholder}
        aria-label={placeholder}
        autoComplete="off"
        onChange={(event) => setText(event.target.value)}
      />
      {hotkey && <span className="kbd">{hotkey}</span>}
    </label>
  )
}

export function Pager({
  page,
  pages,
  onPage,
}: {
  page: number
  pages: number
  onPage: (page: number) => void
}) {
  if (pages <= 1) return null
  return (
    <nav className="toolbar" aria-label={t('common.pages')} style={{ justifyContent: 'center' }}>
      <button
        type="button"
        className="icon-btn outlined"
        aria-label={t('common.previousPage')}
        disabled={page <= 1}
        onClick={() => onPage(page - 1)}
      >
        <Icon name="left" />
      </button>
      <span className="muted num">
        {t('common.pageOf', { page, pages })}
      </span>
      <button
        type="button"
        className="icon-btn outlined"
        aria-label={t('common.nextPage')}
        disabled={page >= pages}
        onClick={() => onPage(page + 1)}
      >
        <Icon name="right" />
      </button>
    </nav>
  )
}

export function Empty({
  icon,
  title,
  children,
}: {
  icon: IconName
  title: string
  children?: ReactNode
}) {
  return (
    <div className="empty">
      <Icon name={icon} size={30} />
      <strong>{title}</strong>
      {children}
    </div>
  )
}

/** Enquanto os dados não chegam, ou quando a API não respondeu. */
export function Loading({ error }: { error?: unknown }) {
  if (error) {
    return (
      <Empty icon="alert" title={t('common.loadFailed')}>
        <p>{error instanceof Error ? error.message : t('common.noAnswer')}</p>
      </Empty>
    )
  }
  return (
    <div className="empty" role="status">
      <Icon name="spinner" size={24} className="spin" />
      <span className="sr-only">{t('common.loading')}</span>
    </div>
  )
}
