// O que é próprio de legenda e biblioteca: pôster, legendas por origem, tarefa do item.
import { useState, type ReactNode } from 'react'
import type { JobRef, Sub } from '../api/types'
import { t } from '../i18n'
import { withBase } from '../lib/base'
import { percent, place, posterHue, stepLabel } from '../lib/format'
import { Bar } from './controls'

/** O pôster; sem imagem (ou se ela não carregar), um bloco de cor com o título. */
export function Poster({
  title,
  src,
  size,
  children,
}: {
  title: string
  src: string | null
  size?: 'small' | 'thumb'
  children?: ReactNode
}) {
  const [broken, setBroken] = useState(false)
  const image = src && !broken
  return (
    <div
      className={`poster ${size ?? ''}`}
      style={{ background: `oklch(0.38 0.085 ${posterHue(title)})` }}
    >
      {image && <img src={withBase(src)} alt="" loading="lazy" onError={() => setBroken(true)} />}
      {children}
      {!image && size !== 'thumb' && <span className="name">{title}</span>}
    </div>
  )
}

/** Legendas do item por origem (a do idioma original e as dos alvos); as que faltam
    tracejadas. A que a tarefa do item está fazendo aparece em andamento (tarefa sem idioma,
    como ler metadados, vale para todas). As dos outros idiomas só são contadas: `+31` no
    cartão, `31 more subtitles` por extenso na tabela (`wide`). */
export function Tracks({
  subs,
  others,
  missing,
  job,
  wide,
}: {
  subs: Sub[]
  others: number
  missing: string[]
  job?: JobRef | null
  wide?: boolean
}) {
  const making = (lang: string) => !!job && (job.language === null || job.language === lang)
  const more = others > 0 ? t('sub.more', { n: others }) : ''
  return (
    <span className="pills">
      {more && (
        <span className="more" title={wide ? undefined : more} aria-label={wide ? undefined : more}>
          {wide ? more : `+${others}`}
        </span>
      )}
      {subs.map((sub) => (
        <span
          key={`${sub.lang}-${sub.kind}`}
          className={`pill ${sub.kind}`}
          title={t('sub.tip', { lang: sub.lang, what: t(`sub.${sub.kind}`) })}
        >
          {sub.lang}
        </span>
      ))}
      {missing.map((lang) => (
        <span
          key={`missing-${lang}`}
          className={`pill ${making(lang) ? 'working' : 'missing'}`}
          title={t('sub.tip', { lang, what: making(lang) ? t('sub.making') : t('sub.missing') })}
        >
          {lang}
        </span>
      ))}
    </span>
  )
}

/** O que cada cor de legenda quer dizer: uma amostra sem texto, para não parecer um item. */
export function TrackLegend() {
  return (
    <div className="legend track-legend" role="group" aria-label={t('sub.legend')}>
      {(['embedded', 'external', 'ours', 'missing'] as const).map((kind) => (
        <span key={kind}>
          <span className={`pill ${kind}`} aria-hidden="true" />
          {t(`sub.${kind}`)}
        </span>
      ))}
    </div>
  )
}

/** A tarefa ativa de um item: `Translate → pt-BR · 47%` ou `Queued · 3rd`. */
export function ItemJob({ job, bar }: { job: JobRef; bar?: boolean }) {
  const label = stepLabel(job.action, job.language, job.label)
  if (job.status === 'running') {
    return (
      <>
        {bar && (
          <span style={{ flex: '1 1 60px', minWidth: 40 }}>
            <Bar value={job.progress} label={label} />
          </span>
        )}
        <span className="accent" style={{ fontWeight: 500 }}>
          {label} · {percent(job.progress)}
        </span>
      </>
    )
  }
  return (
    <span className="muted">
      {t('job.queuedAt', { place: job.position ? place(job.position) : t('job.waiting') })}
    </span>
  )
}
