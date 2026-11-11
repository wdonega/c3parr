// Como a interface escreve datas, durações, episódios e o nome de uma tarefa.
import type { JobItem } from '../api/types'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const two = (n: number) => String(n).padStart(2, '0')

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`
}

/** `20:28`, na hora local. */
export function clock(iso: string): string {
  const d = new Date(iso)
  return `${two(d.getHours())}:${two(d.getMinutes())}`
}

/** `Today, 20:28`, `Yesterday, 20:28` ou `Oct 28, 20:28`. */
export function dayTime(iso: string | null, now = new Date()): string {
  if (!iso) return ''
  const d = new Date(iso)
  const days = Math.round((startOfDay(now) - startOfDay(d)) / 86_400_000)
  const day = days === 0 ? 'Today' : days === 1 ? 'Yesterday' : `${MONTHS[d.getMonth()]} ${d.getDate()}`
  return `${day}, ${clock(iso)}`
}

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
}

/** Quanto tempo faz: `just now`, `3 min ago`, `2 h ago`; depois disso, a data. */
export function timeAgo(iso: string | null, now = new Date()): string {
  if (!iso) return 'never'
  const seconds = Math.round((now.getTime() - new Date(iso).getTime()) / 1000)
  if (seconds < 45) return 'just now'
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} min ago`
  if (seconds < 86_400) return `${Math.round(seconds / 3600)} h ago`
  return dayTime(iso, now)
}

/** Quanto falta: `in 12 min`, `in 3 h`; passado ou agora, `soon`. */
export function timeUntil(iso: string | null, now = new Date()): string {
  if (!iso) return ''
  const seconds = Math.round((new Date(iso).getTime() - now.getTime()) / 1000)
  if (seconds < 45) return 'soon'
  if (seconds < 3600) return `in ${Math.round(seconds / 60)} min`
  if (seconds < 86_400) return `in ${Math.round(seconds / 3600)} h`
  return dayTime(iso, now)
}

/** `4 s`, `7 min`, `1 h 05 min`. */
export function span(seconds: number): string {
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))} s`
  const minutes = Math.round(seconds / 60)
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${two(minutes % 60)} min`
}

export function duration(start: string | null, end: string | null, now = new Date()): string {
  if (!start) return ''
  const until = end ? new Date(end) : now
  return span((until.getTime() - new Date(start).getTime()) / 1000)
}

export const percent = (progress: number) => `${Math.round(progress * 100)}%`

/** `S02E01`; sem temporada ou número, vazio. */
export function episodeCode(season: number | null, episode: number | null): string {
  if (season === null || episode === null) return ''
  return `S${two(season)}E${two(episode)}`
}

/** Lugar na fila em palavras: `next`, `2nd`, `3rd`, `11th`. */
export function place(position: number): string {
  if (position === 1) return 'next'
  const tens = position % 100
  const suffix = tens >= 11 && tens <= 13 ? 'th' : (['th', 'st', 'nd', 'rd'][position % 10] ?? 'th')
  return `${position}${suffix}`
}

/** Nome de uma tarefa: o título do filme ou do episódio, e de onde ele é. */
export function jobName(job: { name: string; item: JobItem | null }): { title: string; sub: string } {
  const item = job.item
  if (!item) return { title: job.name, sub: '' }
  if (item.kind === 'movie') return { title: item.title, sub: item.year ? String(item.year) : '' }
  const code = episodeCode(item.season, item.episode)
  if (item.episode_title) return { title: item.episode_title, sub: [item.title, code].filter(Boolean).join(' · ') }
  // o Sonarr não informou o título: a série e o código já dizem qual é
  return code ? { title: `${item.title} ${code}`, sub: '' } : { title: job.name, sub: item.title }
}

/** Cor do pôster de mentira: sempre a mesma para o mesmo título. */
export function posterHue(title: string): number {
  let hash = 0
  for (const char of title) hash = (hash * 31 + char.charCodeAt(0)) >>> 0
  return Math.round((hash * 137.508) % 360) // o ângulo áureo espalha títulos parecidos
}

/** O que foi feito, em palavras: `Translated to pt-BR`, `Extracted en from the video`. */
export function doneLabel(action: string | null, language: string | null, step: string): string {
  const lang = language ?? ''
  const done: Record<string, string> = {
    traduzir: `Translated to ${lang}`,
    extrair: `Extracted ${lang} from the video`,
    asr: 'Transcribed the audio',
    sincronizar: 'Synced the subtitle',
    gerar: `Subtitle made from the audio`,
    publicar: `Published ${lang}`,
    metadados: 'Read the metadata',
    remover: `Removed our ${lang} subtitle`,
  }
  return (action && done[action]) || step
}
