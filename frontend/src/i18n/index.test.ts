// Testes dos idiomas da interface: qual idioma abre, o que fica guardado, e os catálogos.
import { isValidElement } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { en } from './en'
import { esMX } from './es-MX'
import {
  decimal,
  detect,
  getLocale,
  languageName,
  LOCALES,
  maybe,
  rich,
  said,
  setLocale,
  shortDate,
  startLocale,
  STORAGE_KEY,
  t,
  textIn,
  titled,
  type Catalog,
  type Message,
} from './index'
import { ptBR } from './pt-BR'

const browser = (...languages: string[]) =>
  vi.spyOn(navigator, 'languages', 'get').mockReturnValue(languages)

beforeEach(() => {
  localStorage.clear()
  setLocale('en-US')
  localStorage.clear()
})
afterEach(() => vi.restoreAllMocks())

describe('which language opens', () => {
  it('any Portuguese opens in pt-BR', () => {
    expect(detect(['pt-BR'])).toBe('pt-BR')
    expect(detect(['pt-PT'])).toBe('pt-BR')
    expect(detect(['pt'])).toBe('pt-BR')
  })

  it('any Spanish opens in es-MX', () => {
    expect(detect(['es-MX'])).toBe('es-MX')
    expect(detect(['es-ES'])).toBe('es-MX')
    expect(detect(['es-419'])).toBe('es-MX')
  })

  it('everything else opens in English', () => {
    expect(detect(['fr-FR'])).toBe('en-US')
    expect(detect(['en-GB'])).toBe('en-US')
    expect(detect([])).toBe('en-US')
    expect(detect([''])).toBe('en-US')
  })

  it('follows the order of the browser list', () => {
    expect(detect(['en-US', 'pt-BR'])).toBe('en-US')
    expect(detect(['fr-FR', 'es-AR', 'pt-BR'])).toBe('es-MX')
  })
})

describe('what stays in the browser', () => {
  it('the first visit saves the detected language', () => {
    browser('pt-PT', 'en')
    expect(startLocale()).toBe('pt-BR')
    expect(localStorage.getItem(STORAGE_KEY)).toBe('pt-BR')
  })

  it('the saved language wins over the browser one', () => {
    localStorage.setItem(STORAGE_KEY, 'es-MX')
    browser('pt-BR')
    expect(startLocale()).toBe('es-MX')
  })

  it('a saved value that is not a language is replaced by the detected one', () => {
    localStorage.setItem(STORAGE_KEY, 'klingon')
    browser('es-ES')
    expect(startLocale()).toBe('es-MX')
    expect(localStorage.getItem(STORAGE_KEY)).toBe('es-MX')
  })

  it('picking a language saves it and tells the page', () => {
    setLocale('pt-BR')
    expect(getLocale()).toBe('pt-BR')
    expect(localStorage.getItem(STORAGE_KEY)).toBe('pt-BR')
    expect(document.documentElement.lang).toBe('pt-BR')
    expect(t('nav.queue')).toBe('Fila')
  })

  it('works when the browser has no storage', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('denied')
    })
    browser('pt-BR')
    expect(startLocale()).toBe('pt-BR')
    expect(() => setLocale('es-MX')).not.toThrow()
    expect(getLocale()).toBe('es-MX')
  })
})

const names = (text: string) => [...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort()
const tags = (text: string) => [...text.matchAll(/<(\w+)>.*?<\/\1>/g)].map((m) => m[1]).sort()
const texts = (message: Message): string[] =>
  typeof message === 'string' ? [message] : Object.values(message).filter((v) => typeof v === 'string')
const uniq = (list: string[]) => [...new Set(list)].sort()

describe.each([
  ['pt-BR', ptBR],
  ['es-MX', esMX],
] as [string, Catalog][])('the %s catalog', (_name, catalog) => {
  const keys = Object.keys(en) as (keyof typeof en)[]

  it('has exactly the keys of the English one', () => {
    expect(Object.keys(catalog).sort()).toEqual([...keys].sort())
  })

  it('uses the same parameters in every text', () => {
    for (const key of keys) {
      const wanted = uniq(texts(en[key]).flatMap(names))
      const got = uniq(texts(catalog[key]).flatMap(names))
      expect(got, key).toEqual(wanted)
    }
  })

  it('keeps the marked stretches (links, highlights)', () => {
    for (const key of keys) {
      expect(uniq(texts(catalog[key]).flatMap(tags)), key).toEqual(uniq(texts(en[key]).flatMap(tags)))
    }
  })

  it('counts where English counts', () => {
    for (const key of keys) {
      expect(typeof catalog[key] === 'string', key).toBe(typeof en[key] === 'string')
    }
  })

  it('leaves no text empty', () => {
    for (const key of keys) {
      for (const text of texts(catalog[key])) expect(text.trim(), key).not.toBe('')
    }
  })
})

describe('texts', () => {
  it('counts in English', () => {
    expect(t('show.episodes', { n: 1 })).toBe('1 episode')
    expect(t('show.episodes', { n: 2 })).toBe('2 episodes')
    expect(t('show.episodes', { n: 0 })).toBe('0 episodes')
    expect(t('system.times', { n: 1 })).toBe('once')
    expect(t('system.times', { n: 4 })).toBe('4 times')
  })

  it('zero is plural in Portuguese and Spanish too', () => {
    expect(textIn('pt-BR', 'show.episodes', { n: 0 })).toBe('0 episódios')
    expect(textIn('pt-BR', 'show.episodes', { n: 1 })).toBe('1 episódio')
    expect(textIn('es-MX', 'show.episodes', { n: 0 })).toBe('0 episodios')
    expect(textIn('es-MX', 'show.episodes', { n: 1 })).toBe('1 episodio')
  })

  it('writes the place in the queue', () => {
    expect([1, 2, 3, 4, 11, 12, 13, 21, 22, 101].map((n) => t('place.nth', { n }))).toEqual(
      ['1st', '2nd', '3rd', '4th', '11th', '12th', '13th', '21st', '22nd', '101st'],
    )
    expect(textIn('pt-BR', 'place.nth', { n: 3 })).toBe('3ª')
    expect(textIn('es-MX', 'place.nth', { n: 3 })).toBe('3.ª')
  })

  it('a name from the server falls back to what the server sent', () => {
    expect(maybe('nav.queue', 'fallback')).toBe('Queue')
    expect(maybe('step.brand_new', 'Brand new step')).toBe('Brand new step')
  })

  it('writes what the server said by its code, and keeps what came from outside', () => {
    const cancelled = { ok: true, message: '2 task(s) cancelled.', code: 'queue.cancelled', params: { n: 2 } }
    expect(said(cancelled)).toBe('2 tasks cancelled.')
    expect(said({ message: 'radarr.test: connection refused', code: '', params: {} })).toBe(
      'radarr.test: connection refused',
    )
    expect(said({ message: 'Brand new sentence.', code: 'brand.new', params: {} })).toBe('Brand new sentence.')
    const starting = {
      message: 'Sync with Radarr: starting.',
      code: 'task.starting',
      params: { task: 'Sync with Radarr', task_key: 'routine.sync_radarr' },
    }
    setLocale('pt-BR')
    expect(said(cancelled)).toBe('2 tarefas canceladas.')
    expect(said({ ...cancelled, params: { n: 1 } })).toBe('1 tarefa cancelada.')
    // um nome dentro da frase também é traduzido; um que a interface não conhece fica como veio
    expect(said(starting)).toBe('Sincronizar com o Radarr: começando.')
    expect(said({ ...starting, params: { task: 'Nova rotina', task_key: 'routine.nova' } })).toBe(
      'Nova rotina: começando.',
    )
  })

  it('writes the title and the detail of a notice of the server', () => {
    const item = {
      title: 'Sonarr needs its address and API key',
      detail: 'Sonarr → Settings → General → API Key.',
      code: 'arr_incomplete',
      params: { name: 'Sonarr' },
    }
    expect(titled('setup', item).title).toBe('Sonarr needs its address and API key')
    setLocale('es-MX')
    expect(titled('setup', item)).toEqual({
      title: 'Sonarr necesita su dirección y su clave de API',
      detail: 'Sonarr → Settings → General → API Key.',
    })
    expect(titled('notice', { title: 'New', detail: 'Thing', code: 'unknown', params: {} })).toEqual({
      title: 'New',
      detail: 'Thing',
    })
  })

  it('marks a stretch of a sentence, and a value with "<" is not markup', () => {
    const parts = rich('gen.urlBaseHint', { mono: (text) => `[${text}]` }, {
      where: 'http://<x>y</x>/',
      from: '',
    }) as unknown[]
    const flat = parts
      .map((part) => (isValidElement<{ children: string }>(part) ? part.props.children : part))
      .join('')
    expect(flat).toContain('the address is [http://<x>y</x>/].')
  })

  it('writes dates, decimals and language names in the language of the interface', () => {
    const day = new Date(2026, 9, 28)
    expect(shortDate(day)).toBe('Oct 28')
    expect(decimal(1.25)).toBe('1.3')
    expect(languageName('pt-BR', 'Portuguese (Brazil)')).toBe('Portuguese (Brazil)')
    setLocale('pt-BR')
    expect(shortDate(day)).toBe('28 de out.')
    expect(decimal(1.25)).toBe('1,3')
    expect(languageName('pt-BR', 'Portuguese (Brazil)')).toBe('Português (Brasil)')
    expect(languageName('pt', 'Portuguese (Portugal)')).toBe('Português (Portugal)')
    expect(languageName('en', 'English')).toBe('Inglês')
    setLocale('es-MX')
    expect(languageName('en', 'English')).toBe('Inglés')
    expect(languageName('xx-nope', 'Fallback')).toBe('Fallback')
  })

  it('offers the three languages', () => {
    expect([...LOCALES]).toEqual(['en-US', 'pt-BR', 'es-MX'])
  })
})
