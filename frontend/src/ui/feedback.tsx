// Avisos (toasts) e perguntas de confirmação. Erro fica na tela até ser fechado.
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { Icon } from './Icon'

type Toast = { id: number; message: string; error: boolean }
type Question = {
  title: string
  body: ReactNode
  confirm: string
  danger?: boolean
}
type Feedback = {
  toast: (message: string, error?: boolean) => void
  /** Mostra o resultado de uma ação da API. Devolve se deu certo. */
  report: (result: { ok: boolean; message: string }) => boolean
  confirm: (question: Question) => Promise<boolean>
}

const Context = createContext<Feedback | null>(null)
const SUCCESS_MS = 4000

export function useFeedback(): Feedback {
  const feedback = useContext(Context)
  if (!feedback) throw new Error('useFeedback needs <FeedbackProvider>')
  return feedback
}

export function FeedbackProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const [question, setQuestion] = useState<Question | null>(null)
  const answer = useRef<(value: boolean) => void>(() => {})
  const dialog = useRef<HTMLDialogElement>(null)
  const nextId = useRef(1)

  const dismiss = useCallback((id: number) => {
    setToasts((list) => list.filter((t) => t.id !== id))
  }, [])

  const toast = useCallback(
    (message: string, error = false) => {
      if (!message) return
      const id = nextId.current++
      setToasts((list) => [...list.slice(-3), { id, message, error }])
      if (!error) setTimeout(() => dismiss(id), SUCCESS_MS)
    },
    [dismiss],
  )

  const feedback = useMemo<Feedback>(
    () => ({
      toast,
      report: (result) => {
        toast(result.message, !result.ok)
        return result.ok
      },
      confirm: (asked) =>
        new Promise<boolean>((resolve) => {
          answer.current = resolve
          setQuestion(asked)
        }),
    }),
    [toast],
  )

  useEffect(() => {
    if (question) dialog.current?.showModal()
  }, [question])

  const close = (value: boolean) => {
    dialog.current?.close()
    setQuestion(null)
    answer.current(value)
  }

  return (
    <Context.Provider value={feedback}>
      {children}
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.error ? 'bad' : ''}`} role={t.error ? 'alert' : 'status'}>
            <p>{t.message}</p>
            <button
              type="button"
              className="icon-btn"
              aria-label="Dismiss"
              onClick={() => dismiss(t.id)}
            >
              <Icon name="close" size={14} />
            </button>
          </div>
        ))}
      </div>
      {question && (
        <dialog ref={dialog} onCancel={() => close(false)} aria-labelledby="confirm-title">
          <h2 id="confirm-title">{question.title}</h2>
          <p>{question.body}</p>
          <div className="dialog-actions">
            <button type="button" className="btn" onClick={() => close(false)}>
              Cancel
            </button>
            <button
              type="button"
              className={`btn ${question.danger ? 'danger solid' : 'primary'}`}
              onClick={() => close(true)}
            >
              {question.confirm}
            </button>
          </div>
        </dialog>
      )}
    </Context.Provider>
  )
}
