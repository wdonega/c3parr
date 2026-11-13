// Como a interface escreve datas, durações, episódios e o nome de uma tarefa.
// As palavras saem do idioma da interface (../i18n); unidades (s, min, h) são as mesmas em todos.
import type { JobItem } from '../api/types'
import { maybe, shortDate, t } from '../i18n'

const two = (n: number) => String(n).padStart(2, '0')

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
  const time = clock(iso)
  if (days === 0) return t('time.today', { time })
  if (days === 1) return t('time.yesterday', { time })
  return t('time.date', { date: shortDate(d), time })
}

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
}

/** Quanto tempo faz: `just now`, `3 min ago`, `2 h ago`; depois disso, a data. */
export function timeAgo(iso: string | null, now = new Date()): string {
  if (!iso) return t('time.never')
  const seconds = Math.round((now.getTime() - new Date(iso).getTime()) / 1000)
  if (seconds < 45) return t('time.justNow')
  if (seconds < 3600) return t('time.minAgo', { n: Math.max(1, Math.round(seconds / 60)) })
  if (seconds < 86_400) return t('time.hAgo', { n: Math.round(seconds / 3600) })
  return dayTime(iso, now)
}

/** Quanto falta: `in 12 min`, `in 3 h`; passado ou agora, `soon`. */
export function timeUntil(iso: string | null, now = new Date()): string {
  if (!iso) return ''
  const seconds = Math.round((new Date(iso).getTime() - now.getTime()) / 1000)
  if (seconds < 45) return t('time.soon')
  if (seconds < 3600) return t('time.inMin', { n: Math.round(seconds / 60) })
  if (seconds < 86_400) return t('time.inH', { n: Math.round(seconds / 3600) })
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
  return position === 1 ? t('place.next') : t('place.nth', { n: position })
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
  return action ? maybe(`done.${action}`, step, { lang: language ?? '' }) : step
}

/** Nome de um passo: `Translate → pt-BR`. O servidor diz qual é; as palavras são do idioma. */
export function stepLabel(action: string | null, language: string | null, fallback: string): string {
  const label = action ? maybe(`action.${action}`, '') : ''
  if (!label) return fallback
  return language ? `${label} → ${language}` : label
}

const PROGRESS: [RegExp, (found: string[]) => string][] = [
  [/^block (\d+) of (\d+)$/, ([n, total]) => t('progress.block', { n, total })],
  [/^preparing the audio$/, () => t('progress.preparingAudio')],
  [/^transcribing on the server$/, () => t('progress.onServer')],
  [/^preparing the model$/, () => t('progress.preparingModel')],
  [/^detecting the language at several points$/, () => t('progress.detecting')],
  [/^checking spans in another language$/, () => t('progress.checking')],
  [/^transcribing \((.+)\)$/, ([backend]) => t('progress.transcribing', { backend })],
  [/^(\d+) lines in (\S+) \((.+)\)$/, ([n, lang, where]) => t('progress.linesWhere', { n, lang, where })],
  [/^(\d+) lines in (\S+)$/, ([n, lang]) => t('progress.lines', { n, lang })],
  // legenda de show
  [/^preparing the voice separation model$/, () => t('progress.voiceModel')],
  [/^separating the voice \((.+)\)$/, ([where]) => t('progress.separating', { where })],
  [/^finding where there is voice$/, () => t('progress.findingVoice')],
  [/^voice separated \((.+)\)$/, ([where]) => t('progress.voiceDone', { where })],
  [/^listening for the lyrics$/, () => t('progress.listening')],
  [/^finding the artist$/, () => t('progress.artist')],
  [/^no artist found: no official lyrics$/, () => t('progress.noArtist')],
  [/^artist: (.+)$/, ([names]) => t('progress.artistFound', { names })],
  [/^lyrics: (.+)$/, ([album]) => t('progress.album', { album })],
  [/^(\d+) songs with lyrics$/, ([n]) => t('progress.songs', { n })],
  [/^lyrics in (.+): not fitted$/, ([lang]) => t('progress.notFitted', { lang })],
  [/^fitting the lyrics \((\d+) of (\d+)\)$/, ([n, total]) => t('progress.fitting', { n, total })],
  [/^hearing again \((\d+) of (\d+)\)$/, ([n, total]) => t('progress.again', { n, total })],
  [/^hearing the speech \((\d+) of (\d+)\)$/, ([n, total]) => t('progress.speech', { n, total })],
]

/** O andamento que o motor escreveu (`block 3 of 12`), no idioma da interface. O que a
    interface não reconhece fica como veio. */
export function progressText(message: string | null): string {
  if (!message) return ''
  for (const [pattern, write] of PROGRESS) {
    const found = pattern.exec(message)
    if (found) return write(found.slice(1))
  }
  return message
}
