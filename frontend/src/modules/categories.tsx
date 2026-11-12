import { useState } from 'react'
import { patch } from '../lib/api'
import { Empty, Modal, Segmented, errMsg, useToast } from '../components/ui'

/** 库存分类（老板定的七类）；“未分类”只是兜底，点一下就能补。和后端 app/categories.py 保持一致。 */
export const CATEGORIES = ['干货', '冻货', '原料肉', '原料菜', '生产用品', '公司用品', '生产机器']
export const UNCAT = '未分类'
export const FOOD = new Set(['干货', '冻货', '原料肉', '原料菜'])

const CAT_CLS: Record<string, string> = {
  干货: 'bg-amber-100 text-amber-800', 冻货: 'bg-sky-100 text-sky-700', 原料肉: 'bg-rose-100 text-rose-700', 原料菜: 'bg-emerald-100 text-emerald-700',
  生产用品: 'bg-violet-100 text-violet-700', 公司用品: 'bg-slate-200 text-slate-700', 生产机器: 'bg-zinc-700 text-white', [UNCAT]: 'border border-dashed border-slate-300 text-slate-500',
}

export function CatBadge({ c }: { c?: string }) {
  const v = c && CAT_CLS[c] ? c : UNCAT
  return <span className={`inline-flex shrink-0 items-center rounded px-1.5 py-0.5 text-[11px] font-medium leading-none ${CAT_CLS[v]}`}>{v}</span>
}

/** 分类筛选：一行可横向滑动的按钮，带数量；数量为 0 的分类不显示（未分类除外不显示 0）。 */
export function CategoryChips({ counts, value, onChange }: { counts: Record<string, number>; value: string; onChange: (c: string) => void }) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0)
  const items = [{ key: '', label: '全部', n: total }, ...[...CATEGORIES, UNCAT].filter((c) => counts[c]).map((c) => ({ key: c, label: c, n: counts[c] }))]
  return (
    <div className="-mx-4 mb-3 flex gap-1.5 overflow-x-auto px-4 pb-1" role="group" aria-label="按分类筛选">
      {items.map((x) => (
        <button key={x.key || 'all'} type="button" aria-pressed={value === x.key} onClick={() => onChange(x.key)}
          className={`flex min-h-9 shrink-0 items-center gap-1 whitespace-nowrap rounded-full border px-3 text-sm ${value === x.key ? 'border-brand-600 bg-brand-600 text-white' : 'border-slate-200 bg-white text-slate-700'}`}>
          {x.label}<span className={value === x.key ? 'text-white/80' : 'text-slate-400'}>{x.n}</span>
        </button>
      ))}
    </div>
  )
}

/** 七个分类按钮（手机上 4 列，点一下就保存）。 */
export function CategoryPicker({ value, onPick, disabled }: { value?: string; onPick: (c: string) => void; disabled?: boolean }) {
  return (
    <div className="grid grid-cols-4 gap-1.5 sm:grid-cols-7">
      {CATEGORIES.map((c) => (
        <button key={c} type="button" disabled={disabled} aria-pressed={value === c} onClick={() => onPick(c)}
          className={`min-h-11 rounded-lg border px-1 text-sm ${value === c ? 'border-brand-600 bg-brand-600 font-semibold text-white' : 'border-slate-200 bg-white text-slate-700 active:bg-slate-50'}`}>{c}</button>
      ))}
    </div>
  )
}

export async function saveCategory(skuId: string, category: string) {
  return patch(`/api/inv/skus/${skuId}`, { category })
}

/** 整理分类：默认只列未分类的；也可以看全部，改掉自动猜错的。 */
export function ClassifyDialog({ skus, onClose, onDone }: { skus: any[]; onClose: () => void; onDone: () => void }) {
  const toast = useToast()
  const [mode, setMode] = useState(skus.some((s) => !s.category || s.category === UNCAT) ? 'todo' : 'all')
  const [cats, setCats] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState('')
  const [shown] = useState(() => new Set(skus.filter((s) => !s.category || s.category === UNCAT).map((s) => s.id)))
  const catOf = (s: any) => cats[s.id] ?? s.category ?? UNCAT
  const list = mode === 'todo' ? skus.filter((s) => shown.has(s.id)) : skus
  const left = skus.filter((s) => catOf(s) === UNCAT).length
  const pick = async (s: any, c: string) => {
    setBusy(s.id)
    try { await saveCategory(s.id, c); setCats((m) => ({ ...m, [s.id]: c })) } catch (e) { toast(errMsg(e), 'err') } finally { setBusy('') }
  }
  const close = () => { if (Object.keys(cats).length) onDone(); onClose() }
  return (
    <Modal open onClose={close} wide title="整理分类" footer={<button className="btn-primary" onClick={close}>完成{left ? `（还有 ${left} 个未分类）` : ''}</button>}>
      <div className="space-y-3">
        <Segmented items={[{ key: 'todo', label: `未分类 ${shown.size}` }, { key: 'all', label: `全部 ${skus.length}` }]} value={mode} onChange={setMode} />
        <p className="text-xs text-slate-500">点一下就保存。旧数据是按品名自动归的类，看全部可以改掉归错的。食品类（干货、冻货、原料肉、原料菜）收货时要填 Lot。</p>
        {list.map((s) => (
          <div key={s.id} role="group" aria-label={s.name} className="rounded-lg border border-slate-200 p-3">
            <div className="mb-2 flex items-center gap-2"><span className="min-w-0 flex-1 truncate font-medium">{s.name}</span><CatBadge c={catOf(s)} /></div>
            <CategoryPicker value={catOf(s)} disabled={busy === s.id} onPick={(c) => pick(s, c)} />
          </div>
        ))}
        {!list.length && <Empty>都分好了</Empty>}
      </div>
    </Modal>
  )
}
