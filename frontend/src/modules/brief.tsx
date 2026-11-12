import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronDown, ChevronRight, Sun } from 'lucide-react'
import { get } from '../lib/api'
import { ago } from '../lib/fmt'
import { Spinner, useLoad } from '../components/ui'
import type { ModProps } from './basic'

const TONE: Record<string, string> = { bad: 'text-red-700 bg-red-50 ring-red-200', warn: 'text-amber-800 bg-amber-50 ring-amber-200', info: 'text-sky-800 bg-sky-50 ring-sky-200' }

function line(key: string, it: any): string {
  switch (key) {
    case 'orders_due': case 'orders_late': return `${it.required_date || ''} · ${it.po}${it.site ? ` · ${it.site}` : ''} · ${it.status}`
    case 'batches': return `${it.code} · ${it.source || ''}（${it.rows} 行）`
    case 'low_stock': return `${it.sku} @ ${it.warehouse}：可用 ${it.available} ${it.unit}（安全 ${it.safety}）`
    case 'expiring': return `${it.sku} · Lot ${it.lot} · ${it.expired ? '已过期' : '到期'} ${it.expiry} · ${it.qty}`
    case 'po_late': return `${it.code} ${it.supplier} · 预计 ${it.expected_date}`
    case 'missing_proof': return `${it.what}：${it.ref}${it.note ? `（${it.note}）` : ''}`
    case 'my_tasks': return it.title
    default: return JSON.stringify(it)
  }
}

/** 今日摘要：每天早上一眼看完要处理的事（ChatGPT 的 get_daily_brief 和早上 7 点的通知是同一份）。 */
export function BriefCard({ bare }: { bare?: boolean }) {
  const { data, error } = useLoad(() => get('/api/brief'), [])
  const [open, setOpen] = useState<Record<string, boolean>>({})
  if (error) return null
  if (!data) return <Spinner />
  const shown = data.sections.filter((s: any) => s.count)
  return (
    <div className={bare ? '' : 'card p-4'} id="brief">
      {bare ? <div className="mb-1 text-xs text-slate-400">{data.date} · {ago(data.generated_at)}</div>
        : <div className="mb-1 flex items-center gap-2 font-semibold"><Sun size={18} className="text-amber-500" />今日摘要 <span className="text-xs font-normal text-slate-400">{data.date} · {ago(data.generated_at)}</span></div>}
      <p className="mb-3 text-sm text-slate-600">{data.headline}</p>
      <div className="space-y-2">
        {shown.map((s: any) => (
          <div key={s.key} className={`rounded-lg ring-1 ${TONE[s.tone] || TONE.info}`}>
            <button className="flex min-h-11 w-full items-center gap-2 px-3 text-left text-sm" aria-expanded={!!open[s.key]} onClick={() => setOpen({ ...open, [s.key]: !open[s.key] })}>
              {open[s.key] ? <ChevronDown size={16} /> : <ChevronRight size={16} />}<span className="flex-1 font-medium">{s.title}</span><b>{s.count}</b>
            </button>
            {open[s.key] && (
              <div className="space-y-1 border-t border-current/10 bg-white/60 px-3 py-2">
                {s.items.slice(0, 20).map((it: any, i: number) => (
                  <Link key={i} to={it.link || '#'} className="block truncate rounded py-1 text-sm text-slate-700 hover:underline">{line(s.key, it)}</Link>
                ))}
                {s.count > 20 && <div className="text-xs text-slate-400">还有 {s.count - 20} 条</div>}
              </div>
            )}
          </div>
        ))}
        {!shown.length && <div className="text-sm text-emerald-700">都处理完了 👍</div>}
      </div>
    </div>
  )
}

export function BriefModule(_: ModProps) {
  return <BriefCard bare />
}
