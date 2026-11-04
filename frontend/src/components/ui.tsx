import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { X } from 'lucide-react'
import { t } from '../lib/i18n'

// ---------- toast ----------
type Toast = { id: number; msg: string; kind: 'ok' | 'err' }
const ToastCtx = createContext<(msg: string, kind?: 'ok' | 'err') => void>(() => {})
export const useToast = () => useContext(ToastCtx)
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([])
  const push = useCallback((msg: string, kind: 'ok' | 'err' = 'ok') => {
    const id = Date.now() + Math.random()
    setItems((s) => [...s, { id, msg, kind }])
    setTimeout(() => setItems((s) => s.filter((x) => x.id !== id)), kind === 'err' ? 6000 : 3000)
  }, [])
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="fixed bottom-[calc(76px+env(safe-area-inset-bottom))] left-1/2 z-[100] flex w-[min(92vw,420px)] -translate-x-1/2 flex-col gap-2 lg:bottom-4">
        {items.map((x) => (
          <div key={x.id} className={`rounded-lg px-4 py-3 text-sm shadow-lg ${x.kind === 'err' ? 'bg-red-600 text-white' : 'bg-slate-900 text-white'}`}>{x.msg}</div>
        ))}
      </div>
    </ToastCtx.Provider>
  )
}

// ---------- modal / drawer ----------
export function Modal({ open, onClose, title, children, wide, footer }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; wide?: boolean; footer?: ReactNode }) {
  useEffect(() => {
    if (!open) return
    const h = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-900/40 sm:items-center sm:p-4" onMouseDown={onClose}>
      <div className={`flex w-full flex-col rounded-t-2xl bg-white shadow-xl sm:max-h-[92vh] sm:rounded-2xl ${wide ? 'h-[100dvh] rounded-none sm:h-auto sm:max-w-4xl' : 'max-h-[92dvh] sm:max-w-lg'}`} onMouseDown={(e) => e.stopPropagation()} role="dialog" aria-modal>
        <div className={`flex items-center justify-between gap-2 border-b px-4 py-2 ${wide ? 'pt-[max(0.5rem,env(safe-area-inset-top))]' : ''}`}>
          <div className="min-w-0 font-semibold">{title}</div>
          <button className="btn-ghost shrink-0" onClick={onClose} aria-label={t('common.close')}><X size={20} /></button>
        </div>
        <div className="flex-1 overflow-y-auto overscroll-contain p-4">{children}</div>
        {footer && <div className="flex justify-end gap-2 border-t px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] [&>button]:flex-1 sm:[&>button]:flex-none">{footer}</div>}
      </div>
    </div>
  )
}

// ---------- responsive ----------
const MQ = '(max-width: 639px)'
export function useIsMobile() {
  const [m, setM] = useState(() => typeof window !== 'undefined' && window.matchMedia(MQ).matches)
  useEffect(() => {
    const mq = window.matchMedia(MQ)
    const h = () => setM(mq.matches)
    mq.addEventListener('change', h)
    return () => mq.removeEventListener('change', h)
  }, [])
  return m
}

export function Segmented({ items, value, onChange }: { items: { key: string; label: ReactNode }[]; value: string; onChange: (k: string) => void }) {
  return (
    <div className="seg" role="group">
      {items.map((x) => <button key={x.key} type="button" aria-pressed={value === x.key} onClick={() => onChange(x.key)}>{x.label}</button>)}
    </div>
  )
}

// ---------- small pieces ----------
const tone: Record<string, string> = {
  gray: 'bg-slate-100 text-slate-600', green: 'bg-emerald-100 text-emerald-700', yellow: 'bg-amber-100 text-amber-800',
  red: 'bg-red-100 text-red-700', blue: 'bg-sky-100 text-sky-700', purple: 'bg-violet-100 text-violet-700',
}
export const Badge = ({ children, color = 'gray' }: { children: ReactNode; color?: string }) => <span className={`chip ${tone[color] || tone.gray}`}>{children}</span>

export const TASK_COLOR: Record<string, string> = { todo: 'gray', doing: 'blue', blocked: 'red', review: 'yellow', done: 'green', cancelled: 'gray' }
export const EV_COLOR: Record<string, string> = { unverified: 'yellow', approved: 'green', rejected: 'red', superseded: 'gray' }
export const LOT_COLOR: Record<string, string> = { released: 'green', quarantine: 'red', pending: 'yellow' }
export const LOT_LABEL: Record<string, string> = { released: '已放行', quarantine: '已隔离', pending: '待放行' }

export const Empty = ({ children }: { children?: ReactNode }) => <div className="py-8 text-center text-sm text-slate-400">{children || t('common.empty')}</div>
export const Spinner = () => <div className="py-8 text-center text-sm text-slate-400">{t('common.loading')}</div>

export function Field({ label, children, hint }: { label: ReactNode; children: ReactNode; hint?: ReactNode }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-400">{hint}</span>}
    </label>
  )
}

export function Tabs({ tabs, value, onChange }: { tabs: { key: string; label: ReactNode }[]; value: string; onChange: (k: string) => void }) {
  return (
    <div className="flex gap-1 overflow-x-auto border-b">
      {tabs.map((x) => (
        <button key={x.key} onClick={() => onChange(x.key)} className={`whitespace-nowrap border-b-2 px-3 py-2 text-sm ${value === x.key ? 'border-brand-600 font-semibold text-brand-700' : 'border-transparent text-slate-500 hover:text-slate-700'}`}>{x.label}</button>
      ))}
    </div>
  )
}

export function errMsg(e: any) {
  const d = e?.detail
  if (d && typeof d === 'object') return d.msg || JSON.stringify(d)
  return e?.message || String(e)
}

// Data loader hook with reload
export function useLoad<T>(fn: () => Promise<T>, deps: any[] = []): { data: T | undefined; error: any; loading: boolean; reload: () => void; setData: (d: T) => void } {
  const [data, setData] = useState<T>()
  const [error, setError] = useState<any>()
  const [loading, setLoading] = useState(true)
  const [n, setN] = useState(0)
  useEffect(() => {
    let alive = true
    setLoading(true)
    fn().then((d) => { if (alive) { setData(d); setError(undefined) } }).catch((e) => alive && setError(e)).finally(() => alive && setLoading(false))
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, n])
  return { data, error, loading, reload: () => setN((x) => x + 1), setData }
}

export function ErrorBox({ error, onRetry }: { error: any; onRetry?: () => void }) {
  const forbidden = error?.status === 403
  return (
    <div className={`rounded-lg p-4 text-sm ${forbidden ? 'bg-slate-100 text-slate-600' : 'bg-red-50 text-red-700'}`}>
      {forbidden ? '权限不足：' : '出错了：'}{errMsg(error)}
      {onRetry && !forbidden && <button className="btn-secondary btn-sm ml-3" onClick={onRetry}>{t('common.retry')}</button>}
    </div>
  )
}
