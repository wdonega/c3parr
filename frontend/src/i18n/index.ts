// Idiomas da interface. O catálogo em inglês é a fonte e dá os tipos: uma chave que falta em
// outro idioma, ou um parâmetro esquecido numa chamada, não compila. O idioma é escolhido por
// navegador e fica guardado nele.
import { createElement, Fragment, useSyncExternalStore, type ReactNode } from 'react'
import { en } from './en'
import { esMX } from './es-MX'
import { ptBR } from './pt-BR'

export const LOCALES = ['en-US', 'pt-BR', 'es-MX'] as const
export type Locale = (typeof LOCALES)[number]

/** Cada idioma escrito nele mesmo: é assim que a pessoa o reconhece na lista. */
export const LOCALE_NAMES: Record<Locale, string> = {
  'en-US': 'English (US)',
  'pt-BR': 'Português (Brasil)',
  'es-MX': 'Español (México)',
}

/** Texto que muda com a quantidade `{n}`; `ordinal` escolhe pela posição (1st, 2nd, 3rd). */
export type Forms = {
  zero?: string
  one?: string
  two?: string
  few?: string
  many?: string
  other: string
  ordinal?: true
}
export type Message = string | Forms
export type Key = keyof typeof en
export type Catalog = Record<Key, Message>

const CATALOGS: Record<Locale, Catalog> = { 'en-US': en, 'pt-BR': ptBR, 'es-MX': esMX }
export const STORAGE_KEY = 'c3parr.locale'

const isLocale = (value: unknown): value is Locale => LOCALES.includes(value as Locale)

/** O primeiro idioma do navegador que a interface fala: qualquer português vira pt-BR, qualquer
    espanhol vira es-MX; sem nenhum deles, inglês. */
export function detect(languages: readonly string[]): Locale {
  for (const language of languages) {
    const base = language.toLowerCase().split(/[-_]/)[0]
    if (base === 'pt') return 'pt-BR'
    if (base === 'es') return 'es-MX'
    if (base === 'en') return 'en-US'
  }
  return 'en-US'
}

function remember(locale: Locale): void {
  try {
    localStorage.setItem(STORAGE_KEY, locale)
  } catch {
    // navegador sem armazenamento (modo privado restrito): vale só para esta visita
  }
}

/** O idioma guardado; na primeira visita, o do navegador, que já fica guardado. */
export function startLocale(): Locale {
  let saved: string | null = null
  try {
    saved = localStorage.getItem(STORAGE_KEY)
  } catch {
    saved = null
  }
  if (isLocale(saved)) return saved
  const found = detect(navigator.languages?.length ? navigator.languages : [navigator.language ?? ''])
  remember(found)
  return found
}

let current: Locale = startLocale()
document.documentElement.lang = current
const listeners = new Set<() => void>()

export const getLocale = (): Locale => current

export function setLocale(locale: Locale): void {
  current = locale
  remember(locale)
  document.documentElement.lang = locale
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** Faz o componente desenhar de novo quando o idioma muda. As páginas e a casca chamam; o que
    está dentro delas é desenhado de novo junto. */
export function useLocale(): Locale {
  return useSyncExternalStore(subscribe, getLocale)
}

// --- textos ---

type Names<S> = S extends `${string}{${infer P}}${infer R}` ? P | Names<R> : never
type TextOf<M> = M extends string ? M : M extends object ? Extract<M[keyof M], string> : never
type Counted<M> = M extends string ? never : 'n'
// um texto que muda com a quantidade sempre recebe `n`, mesmo quando não o escreve
type ParamNames<K extends Key> = Names<TextOf<(typeof en)[K]>> | Counted<(typeof en)[K]>
type Values = Record<string, string | number>
type Args<K extends Key> = [ParamNames<K>] extends [never]
  ? []
  : [params: Record<ParamNames<K>, string | number>]

const rules = new Map<string, Intl.PluralRules>()

function form(locale: Locale, forms: Forms, n: number): string {
  const type = forms.ordinal ? 'ordinal' : 'cardinal'
  const id = `${locale}:${type}`
  let rule = rules.get(id)
  if (!rule) {
    rule = new Intl.PluralRules(locale, { type })
    rules.set(id, rule)
  }
  // zero pede o plural nos três idiomas ("0 tarefas"), mesmo onde a regra do idioma diz singular
  if (n === 0 && !forms.ordinal) return forms.zero ?? forms.other
  return forms[rule.select(n)] ?? forms.other
}

const fill = (text: string, params?: Values) =>
  params ? text.replace(/\{(\w+)\}/g, (whole, name: string) => (name in params ? String(params[name]) : whole)) : text

function raw(locale: Locale, key: Key, params?: Values): string {
  const message = CATALOGS[locale][key] ?? en[key]
  return typeof message === 'string' ? message : form(locale, message, Number(params?.n ?? 0))
}

/** O texto da chave no idioma dado (os testes comparam idiomas com isto). */
export function textIn(locale: Locale, key: Key, params?: Values): string {
  return fill(raw(locale, key, params), params)
}

/** O texto da chave no idioma da interface, com os `{parâmetros}` no lugar. */
export function t<K extends Key>(key: K, ...args: Args<K>): string {
  return textIn(current, key, args[0])
}

/** Como `t`, para frases com um trecho que vira link ou destaque: no catálogo o trecho fica
    entre `<nome>` e `</nome>`, e `tags.nome` diz o que desenhar com ele. */
export function rich<K extends Key>(
  key: K,
  tags: Record<string, (text: string) => ReactNode>,
  ...args: Args<K>
): ReactNode {
  // os parâmetros entram depois de separar os trechos: um valor com "<" não vira marcação
  const parts = raw(current, key, args[0]).split(/<(\w+)>(.*?)<\/\1>/g)
  return parts.map((part, index) => {
    if (index % 3 === 0) return fill(part, args[0])
    if (index % 3 === 2) return null
    const inner = fill(parts[index + 1] ?? '', args[0])
    return createElement(Fragment, { key: index }, tags[part]?.(inner) ?? inner)
  })
}

export const hasKey = (key: string): key is Key => key in en

/** Para nomes que vêm do servidor: o texto da chave, se ela existe; senão o que o servidor
    mandou. Um nome novo no servidor aparece em inglês em vez de sumir. */
export function maybe(key: string, fallback: string, params?: Values): string {
  return hasKey(key) ? textIn(current, key, params) : fallback
}

// --- o que o servidor escreve ---

type Coded = { code: string; params: Record<string, string | number> }

/** Os valores de uma frase do servidor; `x_key` diz que `x` é um nome que a interface traduz. */
function serverParams(params: Values): Values {
  const out: Values = { ...params }
  for (const [name, value] of Object.entries(params)) {
    if (!name.endsWith('_key')) continue
    const target = name.slice(0, -'_key'.length)
    out[target] = maybe(String(value), String(params[target] ?? ''))
  }
  return out
}

/** Uma frase que o servidor mandou, no idioma da interface. Sem código (o texto veio de fora,
    como um erro do Radarr) ou com um código que a interface não conhece, vale a frase dele. */
export function said(result: Coded & { message: string }): string {
  if (!result.code) return result.message
  return maybe(`msg.${result.code}`, result.message, serverParams(result.params))
}

/** O título e o detalhe de um aviso do servidor (`setup` ou `notice`), no idioma da interface. */
export function titled(
  kind: 'setup' | 'notice',
  item: Coded & { title: string; detail: string },
): { title: string; detail: string } {
  const params = serverParams(item.params)
  return {
    title: maybe(`${kind}.${item.code}.title`, item.title, params),
    detail: maybe(`${kind}.${item.code}.detail`, item.detail, params),
  }
}

// --- o que o navegador já sabe escrever em cada idioma ---

/** `Oct 28`, `28 de out.`, `28 oct`. */
export function shortDate(date: Date): string {
  return new Intl.DateTimeFormat(current, { month: 'short', day: 'numeric' }).format(date)
}

/** `1.2`, `1,2`. */
export function decimal(value: number, digits = 1): string {
  return new Intl.NumberFormat(current, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value)
}

/** Nome de um idioma de legenda no idioma da interface. Em inglês vale o nome que o servidor
    manda; `pt` sozinho é o de Portugal, para não se confundir com o do Brasil. */
export function languageName(code: string, fallback: string): string {
  if (current === 'en-US') return fallback
  try {
    const names = new Intl.DisplayNames([current], {
      type: 'language',
      languageDisplay: 'standard',
      fallback: 'none',
    })
    const name = names.of(code === 'pt' ? 'pt-PT' : code)
    return name && name !== code ? name.charAt(0).toLocaleUpperCase(current) + name.slice(1) : fallback
  } catch {
    return fallback
  }
}
