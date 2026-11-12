// Settings: as subpáginas são abas e dividem um rascunho só. A barra de salvar aparece quando
// há alteração; sair de Settings com alteração pendente pergunta antes.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { NavLink, useBlocker, useParams } from 'react-router-dom'
import { api } from '../../api/client'
import type { AppConfig, ArrConfig, SettingsView, TestResult } from '../../api/types'
import { t, useLocale } from '../../i18n'
import { BASE, cleanBase } from '../../lib/base'
import { NotFound, PageHead } from '../../shell/Shell'
import { Loading } from '../../ui/controls'
import { useFeedback } from '../../ui/feedback'
import {
  arrValue,
  changedFields,
  EMPTY,
  fieldValue,
  isEdited,
  setArrField,
  setField,
  setSecret,
  toPatch,
  type ArrKind,
  type Draft,
} from './form'
import { EnginesTab, GeneralTab, IntegrationsTab, LanguagesTab, LibraryTab, SubtitlesTab } from './tabs'

const PAGES = ['library', 'integrations', 'languages', 'engines', 'subtitles', 'general'] as const

/** O que cada aba recebe para ler e mexer no rascunho. */
export type Form = {
  view: SettingsView
  draft: Draft
  update: (next: (draft: Draft) => Draft) => void
  value: <K extends keyof AppConfig>(key: K) => AppConfig[K]
  set: <K extends keyof AppConfig>(key: K, value: AppConfig[K]) => void
  arr: <K extends keyof ArrConfig>(kind: ArrKind, key: K) => ArrConfig[K]
  setArr: <K extends keyof ArrConfig>(kind: ArrKind, key: K, value: ArrConfig[K]) => void
  secret: (name: string) => string | null | undefined
  setSecret: (name: string, value: string | null) => void
  edited: (path: string) => boolean
  test: (target: string) => void
  testing: string | null
  /** O resultado do teste feito agora, com o que está na tela. */
  tested: Record<string, TestResult>
}

export function SettingsPage() {
  useLocale()
  const params = useParams()
  const page = params.tab ? 'library' : (params.page ?? 'library')
  const client = useQueryClient()
  const { report, toast } = useFeedback()
  const settings = useQuery({ queryKey: ['settings'], queryFn: api.settings })
  const [draft, setDraft] = useState<Draft>(EMPTY)
  const [tested, setTested] = useState<Record<string, TestResult>>({})
  const fields = changedFields(draft)
  const dirty = fields.length > 0
  const leave = useRef<HTMLDialogElement>(null)

  // sair de Settings com alteração pendente pergunta; trocar de aba não (o rascunho é um só)
  const blocker = useBlocker(
    ({ nextLocation }) => dirty && !nextLocation.pathname.startsWith('/settings'),
  )
  useEffect(() => {
    if (blocker.state === 'blocked') leave.current?.showModal()
    else leave.current?.close()
  }, [blocker.state])
  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => event.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  const save = useMutation({
    mutationFn: () => api.saveSettings(toPatch(draft)),
    onSuccess: async (result) => {
      if (!report(result)) return false
      // a URL base ou o nome mudaram: a página é aberta de novo no endereço que passou a valer
      if ('url_base' in draft.changes || 'instance_name' in draft.changes) {
        const base = 'url_base' in draft.changes ? cleanBase(String(draft.changes.url_base)) : BASE
        window.location.assign(`${base}/settings/general`)
        return true
      }
      setDraft(EMPTY)
      setTested({})
      await client.invalidateQueries({ queryKey: ['settings'] })
      void client.invalidateQueries({ queryKey: ['shell'] })
      return true
    },
    onError: (error) => toast(error.message, true),
  })
  const test = useMutation({
    mutationFn: (target: string) => api.testConnection(target, toPatch(draft)),
    onSuccess: (result, target) => setTested((old) => ({ ...old, [target]: result })),
    onError: (error) => toast(error.message, true),
  })

  if (!PAGES.some((slug) => slug === page)) return <NotFound />
  const view = settings.data
  if (!view) {
    return (
      <>
        <PageHead title={t('settings.title')} />
        <Loading error={settings.error} />
      </>
    )
  }

  const update: Form['update'] = (next) => {
    setDraft(next)
    setTested({}) // mexeu na tela: o teste de antes não vale para o que está nela agora
  }
  const form: Form = {
    view,
    draft,
    update,
    value: (key) => fieldValue(draft, view.config, key),
    set: (key, value) => update((d) => setField(d, view.config, key, value)),
    arr: (kind, key) => arrValue(draft, view.config, kind, key),
    setArr: (kind, key, value) => update((d) => setArrField(d, view.config, kind, key, value)),
    secret: (name) => draft.secrets[name],
    setSecret: (name, value) => update((d) => setSecret(d, name, value)),
    edited: (path) => isEdited(draft, path),
    test: (target) => test.mutate(target),
    testing: test.isPending ? (test.variables ?? null) : null,
    tested,
  }
  const saveAndLeave = async () => {
    const result = await save.mutateAsync()
    if (result.ok) blocker.proceed?.()
    else blocker.reset?.()
  }

  return (
    <>
      <PageHead title={t('settings.title')} />
      <nav className="tabs" aria-label={t('settings.pages')}>
        {PAGES.map((slug) => (
          <NavLink key={slug} to={`/settings/${slug}`}>
            {t(`settings.tab.${slug}`)}
          </NavLink>
        ))}
      </nav>

      {page === 'library' && <LibraryTab form={form} kind={params.tab === 'sonarr' ? 'sonarr' : 'radarr'} />}
      {page === 'integrations' && <IntegrationsTab form={form} />}
      {page === 'languages' && <LanguagesTab form={form} />}
      {page === 'engines' && <EnginesTab form={form} />}
      {page === 'subtitles' && <SubtitlesTab form={form} />}
      {page === 'general' && <GeneralTab form={form} />}

      {dirty && (
        <div className="save-bar" role="region" aria-label={t('settings.unsaved')}>
          <strong>{t('settings.unsavedCount', { n: fields.length })}</strong>
          <button type="button" className="btn" onClick={() => update(() => EMPTY)}>
            {t('settings.discard')}
          </button>
          <button type="button" className="btn primary" disabled={save.isPending} onClick={() => save.mutate()}>
            {t('settings.save', { n: fields.length })}
          </button>
        </div>
      )}

      <dialog ref={leave} onCancel={() => blocker.reset?.()} aria-labelledby="leave-title">
        <h2 id="leave-title">{t('settings.unsaved')}</h2>
        <p>{t('settings.leave', { n: fields.length })}</p>
        <div className="dialog-actions">
          <button type="button" className="btn" onClick={() => blocker.reset?.()}>
            {t('settings.stay')}
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => {
              setDraft(EMPTY)
              blocker.proceed?.()
            }}
          >
            {t('settings.discard')}
          </button>
          <button type="button" className="btn primary" disabled={save.isPending} onClick={saveAndLeave}>
            {t('settings.saveAndLeave')}
          </button>
        </div>
      </dialog>
    </>
  )
}
