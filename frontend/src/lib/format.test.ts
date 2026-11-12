import { afterEach, describe, expect, it } from 'vitest'
import { setLocale } from '../i18n'
import {
  dayTime,
  doneLabel,
  duration,
  episodeCode,
  jobName,
  percent,
  place,
  posterHue,
  progressText,
  span,
  stepLabel,
  timeAgo,
  timeUntil,
} from './format'

const now = new Date(2026, 10, 11, 20, 30, 0)
const at = (hours: number, minutes: number, day = 11) => new Date(2026, 10, day, hours, minutes).toISOString()

describe('datas e durações', () => {
  it('diz há quanto tempo, e depois de um dia mostra a data', () => {
    expect(timeAgo(null, now)).toBe('never')
    expect(timeAgo(at(20, 29), now)).toBe('1 min ago')
    expect(timeAgo(new Date(now.getTime() - 20_000).toISOString(), now)).toBe('just now')
    expect(timeAgo(at(17, 30), now)).toBe('3 h ago')
    expect(timeAgo(at(9, 5, 9), now)).toBe('Nov 9, 09:05')
  })

  it('diz quanto falta', () => {
    expect(timeUntil(at(20, 42), now)).toBe('in 12 min')
    expect(timeUntil(at(23, 30), now)).toBe('in 3 h')
    expect(timeUntil(at(20, 0), now)).toBe('soon')
    expect(timeUntil(null, now)).toBe('')
  })

  it('escreve o dia como hoje, ontem ou a data', () => {
    expect(dayTime(at(20, 28), now)).toBe('Today, 20:28')
    expect(dayTime(at(8, 0, 10), now)).toBe('Yesterday, 08:00')
    expect(dayTime(at(8, 0, 2), now)).toBe('Nov 2, 08:00')
    expect(dayTime(null, now)).toBe('')
  })

  it('escreve durações em segundos, minutos e horas', () => {
    expect(span(4)).toBe('4 s')
    expect(span(420)).toBe('7 min')
    expect(span(3900)).toBe('1 h 05 min')
    expect(duration(at(20, 0), at(20, 7), now)).toBe('7 min')
    expect(duration(at(20, 24), null, now)).toBe('6 min') // ainda rodando: até agora
    expect(duration(null, null, now)).toBe('')
  })
})

describe('nomes e rótulos', () => {
  it('escreve o episódio e o lugar na fila', () => {
    expect(episodeCode(2, 1)).toBe('S02E01')
    expect(episodeCode(null, 1)).toBe('')
    expect([1, 2, 3, 4, 11, 12, 13, 21, 22].map(place)).toEqual([
      'next', '2nd', '3rd', '4th', '11th', '12th', '13th', '21st', '22nd',
    ])
    expect(percent(0.468)).toBe('47%')
  })

  it('dá nome à tarefa pelo filme ou pelo episódio', () => {
    const item = { year: 2005, show_id: 1, season: 2, episode: 1 }
    expect(
      jobName({ name: 'x.mkv', item: { ...item, kind: 'episode', id: 3, title: 'Supernatural', episode_title: 'In My Time of Dying' } }),
    ).toEqual({ title: 'In My Time of Dying', sub: 'Supernatural · S02E01' })
    // o Sonarr não informou o título do episódio: série e código
    expect(
      jobName({ name: 'x.mkv', item: { ...item, kind: 'episode', id: 3, title: 'Supernatural', episode_title: null } }),
    ).toEqual({ title: 'Supernatural S02E01', sub: '' })
    expect(
      jobName({ name: 'x.mkv', item: { kind: 'movie', id: 1, title: 'Oldboy', year: 2003, show_id: null, season: null, episode: null, episode_title: null } }),
    ).toEqual({ title: 'Oldboy', sub: '2003' })
    // vídeo fora da biblioteca: o nome do arquivo
    expect(jobName({ name: 'loose.mkv', item: null })).toEqual({ title: 'loose.mkv', sub: '' })
  })

  it('diz o que uma tarefa pronta fez', () => {
    expect(doneLabel('traduzir', 'pt-BR', 'Translate → pt-BR')).toBe('Translated to pt-BR')
    expect(doneLabel('extrair', 'en', 'x')).toBe('Extracted en from the video')
    expect(doneLabel(null, null, 'Subtitle pt-BR')).toBe('Subtitle pt-BR') // tarefa antiga
    expect(doneLabel('invented', null, 'Some step')).toBe('Some step')
  })

  it('dá sempre a mesma cor ao mesmo título', () => {
    expect(posterHue('Incendies')).toBe(posterHue('Incendies'))
    expect(posterHue('Incendies')).not.toBe(posterHue('Oldboy'))
    expect(posterHue('')).toBe(0)
  })
})

describe('em outro idioma', () => {
  afterEach(() => setLocale('en-US'))

  it('escreve datas e tempos no idioma da interface', () => {
    setLocale('pt-BR')
    expect(timeAgo(at(20, 29), now)).toBe('há 1 min')
    expect(timeAgo(null, now)).toBe('nunca')
    expect(timeUntil(at(23, 30), now)).toBe('em 3 h')
    expect(dayTime(at(20, 28), now)).toBe('Hoje, 20:28')
    expect(dayTime(at(8, 0, 2), now)).toBe('2 de nov., 08:00')
    expect(place(1)).toBe('a próxima')
    expect(place(3)).toBe('3ª')
    setLocale('es-MX')
    expect(timeAgo(at(17, 30), now)).toBe('hace 3 h')
    expect(dayTime(at(8, 0, 10), now)).toBe('Ayer, 08:00')
    expect(dayTime(at(8, 0, 2), now)).toBe('2 nov, 08:00')
  })

  it('dá nome ao passo pelo identificador, e ao que não conhece pelo que o servidor mandou', () => {
    expect(stepLabel('traduzir', 'pt-BR', 'x')).toBe('Translate → pt-BR')
    expect(stepLabel('metadados', null, 'x')).toBe('Metadata')
    expect(stepLabel('novo_passo', 'pt-BR', 'New step → pt-BR')).toBe('New step → pt-BR')
    expect(stepLabel(null, null, 'Subtitle pt-BR')).toBe('Subtitle pt-BR')
    setLocale('pt-BR')
    expect(stepLabel('traduzir', 'pt-BR', 'x')).toBe('Traduzir → pt-BR')
    expect(doneLabel('traduzir', 'pt-BR', 'x')).toBe('Traduzida para pt-BR')
    expect(doneLabel('novo_passo', 'pt-BR', 'New step')).toBe('New step')
  })

  it('reescreve o andamento que reconhece e deixa o resto como veio', () => {
    expect(progressText('block 3 of 12')).toBe('block 3 of 12')
    expect(progressText(null)).toBe('')
    setLocale('pt-BR')
    expect(progressText('block 3 of 12')).toBe('bloco 3 de 12')
    expect(progressText('preparing the model')).toBe('preparando o modelo')
    expect(progressText('transcribing (Metal)')).toBe('transcrevendo (Metal)')
    expect(progressText('412 lines in en (CPU)')).toBe('412 falas em en (CPU)')
    expect(progressText('412 lines in en')).toBe('412 falas em en')
    expect(progressText('something an engine made up')).toBe('something an engine made up')
    setLocale('es-MX')
    expect(progressText('checking spans in another language')).toBe('revisando tramos en otro idioma')
  })
})
