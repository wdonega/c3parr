// O que é próprio de legenda e biblioteca: pôster, legendas por origem, tarefa do item.
import { useState, type ReactNode } from 'react'
import type { JobRef, Sub } from '../api/types'
import { percent, place, posterHue } from '../lib/format'
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
      {image && <img src={src} alt="" loading="lazy" onError={() => setBroken(true)} />}
      {children}
      {!image && size !== 'thumb' && <span className="name">{title}</span>}
    </div>
  )
}

const KIND_LABEL: Record<Sub['kind'], string> = {
  embedded: 'inside the video',
  external: 'from another source',
  ours: 'made by c3parr',
}

/** Legendas do item por origem; as que faltam tracejadas. A que a tarefa do item está
    fazendo aparece em andamento (tarefa sem idioma, como ler metadados, vale para todas). */
export function Tracks({ subs, missing, job }: { subs: Sub[]; missing: string[]; job?: JobRef | null }) {
  const making = (lang: string) => !!job && (job.language === null || job.language === lang)
  return (
    <span className="pills">
      {subs.map((sub) => (
        <span
          key={`${sub.lang}-${sub.kind}`}
          className={`pill ${sub.kind}`}
          title={`${sub.lang}: ${KIND_LABEL[sub.kind]}`}
        >
          {sub.lang}
        </span>
      ))}
      {missing.map((lang) => (
        <span
          key={`missing-${lang}`}
          className={`pill ${making(lang) ? 'working' : 'missing'}`}
          title={making(lang) ? `${lang}: being made` : `${lang}: missing`}
        >
          {lang}
        </span>
      ))}
    </span>
  )
}

export function TrackLegend() {
  return (
    <div className="legend" aria-label="Subtitle legend" style={{ fontSize: 12 }}>
      <span>
        <span className="pill embedded">en</span>inside the video
      </span>
      <span>
        <span className="pill external">en</span>from another source
      </span>
      <span>
        <span className="pill ours">en</span>made by c3parr
      </span>
      <span>
        <span className="pill missing">en</span>missing
      </span>
    </div>
  )
}

/** A tarefa ativa de um item: `Translate → pt-BR · 47%` ou `Queued · 3rd`. */
export function ItemJob({ job, bar }: { job: JobRef; bar?: boolean }) {
  if (job.status === 'running') {
    return (
      <>
        {bar && (
          <span style={{ flex: '1 1 60px', minWidth: 40 }}>
            <Bar value={job.progress} label={job.label} />
          </span>
        )}
        <span className="accent" style={{ fontWeight: 500 }}>
          {job.label} · {percent(job.progress)}
        </span>
      </>
    )
  }
  return <span className="muted">Queued · {job.position ? place(job.position) : 'waiting'}</span>
}
