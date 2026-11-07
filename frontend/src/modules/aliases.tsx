import { useMemo, useState } from 'react'
import { ArrowLeft, Pencil, Plus, Trash2 } from 'lucide-react'
import { del, get, post, put } from '../lib/api'
import { useAuth } from '../lib/auth'
import { Badge, Empty, Field, Modal, Spinner, errMsg, useLoad, useToast } from '../components/ui'

const KIND_COLOR: Record<string, string> = { customer: 'blue', warehouse: 'purple', supplier: 'green', accounting: 'yellow', other: 'gray' }
const SHORT: Record<string, string> = { customer: '客户', warehouse: '外库', supplier: '供应商', accounting: 'QB', other: '旧称' }

type Alias = { id: string; source: string; party_kind: string; kind_label: string; party: string; party_ref: string; code: string; name: string
  pack_size: string; unit: string; factor: number; last_price: number | null; note: string; sku: { id: string; code: string; name: string; base_unit: string } | null }

/** 商品对照（一物多码）：库存只记我们的 SKU，各方的编码 / 品名挂在下面。 */
export function AliasManager({ skuId, onClose }: { skuId?: string; onClose: () => void }) {
  const master = useLoad(() => get('/api/inv/master'), [])
  const all = useLoad<Alias[]>(() => get('/api/aliases'), [])
  const [q, setQ] = useState('')
  const [sel, setSel] = useState<string | null>(skuId || null)
  const bySku = useMemo(() => {
    const m: Record<string, Alias[]> = {}
    for (const a of all.data || []) if (a.sku) (m[a.sku.id] ||= []).push(a)
    return m
  }, [all.data])
  if (!master.data || !all.data) return <Modal open onClose={onClose} title="商品对照"><Spinner /></Modal>
  const norm = (s: string) => (s || '').toLowerCase().replace(/[^0-9a-z一-鿿]/g, '')
  const n = norm(q)
  const skus = master.data.skus.filter((s: any) => !n || norm(s.code).includes(n) || norm(s.name).includes(n)
    || (bySku[s.id] || []).some((a) => norm(a.code).includes(n) || norm(a.name).includes(n)))
  const sku = sel && master.data.skus.find((s: any) => s.id === sel)
  return (
    <Modal open onClose={onClose} wide title={sku ? <button className="-ml-2 flex min-h-11 items-center gap-1 rounded px-2 hover:bg-slate-50" onClick={() => setSel(null)} aria-label="返回列表"><ArrowLeft size={18} />返回</button> : '商品对照（一物多码）'}>
      {sku ? <SkuAliases sku={sku} rows={bySku[sku.id] || []} onChanged={all.reload} /> : (
        <div className="space-y-3">
          <p className="text-sm text-slate-600">商品以<b>我们的品名</b>为主，大统华 Item# 作补充编号；库存只记这一个商品。大统华 Item#、外库（Supreme）代码、供应商编码、QuickBooks 品名都挂在对应 SKU 下面；单据进来时自动认出，确认页里选过一次的也会记住。</p>
          <input className="input" type="search" placeholder="搜品名，或任何一方的编码（538949、Vatee-001…）" value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="space-y-2">
            {skus.map((s: any) => {
              const list = bySku[s.id] || []
              return (
                <button key={s.id} className="w-full rounded-lg border border-slate-200 p-3 text-left hover:bg-slate-50" onClick={() => setSel(s.id)}>
                  <div className="flex items-baseline gap-2"><span className="min-w-0 flex-1 truncate font-semibold">{s.name}</span>{s.tnt_item_no && <span className="font-mono text-sm">T&amp;T {s.tnt_item_no}</span>}<span className="text-xs text-slate-400">{s.base_unit}</span></div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {list.map((a) => <Badge key={a.id} color={KIND_COLOR[a.party_kind]}>{SHORT[a.party_kind]} {a.party_kind === 'accounting' ? a.name || a.code : a.code || a.name}</Badge>)}
                    {!list.length && <span className="text-xs text-slate-400">还没有对照</span>}
                  </div>
                </button>
              )
            })}
            {!skus.length && <Empty>没找到。对方编码还没对上的话，先找到我们的 SKU 再加对照。</Empty>}
          </div>
        </div>
      )}
    </Modal>
  )
}

const EMPTY = { party_kind: 'warehouse', party: '', code: '', name: '', pack_size: '', unit: '', factor: '' }

function SkuAliases({ sku, rows, onChanged }: { sku: any; rows: Alias[]; onChanged: () => void }) {
  const { can } = useAuth()
  const toast = useToast()
  const parties = useLoad(() => get('/api/aliases/parties'), [])
  const [f, setF] = useState<any>(null)
  const canEdit = (kind: string) => can(kind === 'customer' ? 'order.manage' : 'inventory.master')
  const save = async () => {
    const body = { ...f, sku_id: sku.id, factor: f.factor ? Number(f.factor) : null, party: f.party_kind === 'accounting' ? 'QuickBooks' : f.party }
    try { await (f.id ? put(`/api/aliases/${f.id}`, body) : post('/api/aliases', body)); toast('已保存'); setF(null); onChanged() } catch (e) { toast(errMsg(e), 'err') }
  }
  const remove = async (a: Alias) => {
    if (!confirm(`删除对照 ${a.party} ${a.code || a.name}？`)) return
    try { await del(`/api/aliases/${a.id}`); onChanged() } catch (e) { toast(errMsg(e), 'err') }
  }
  const opts = parties.data?.[f?.party_kind] || []
  return (
    <div className="space-y-3">
      <div><div className="text-lg font-semibold">{sku.name}</div><div className="text-xs text-slate-500">{sku.tnt_item_no ? `大统华 Item# ${sku.tnt_item_no} · ` : '还没有大统华 Item#（看到 PO 会自动补上）· '}基本单位 {sku.base_unit} · 内部编号 {sku.code}</div></div>
      <div className="space-y-2">
        {rows.map((a) => (
          <div key={a.id} className="flex items-start gap-2 rounded-lg border border-slate-200 p-3 text-sm">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-1.5"><Badge color={KIND_COLOR[a.party_kind]}>{a.kind_label}</Badge><span className="font-medium">{a.party}</span></div>
              <div className="mt-1">{a.code && <span className="font-mono font-semibold">{a.code}</span>} {a.name}</div>
              <div className="text-xs text-slate-500">{a.pack_size && `${a.pack_size} · `}{a.unit ? `1 ${a.unit} = ${a.factor} ${sku.base_unit}` : a.factor !== 1 ? `换算 ×${a.factor}` : '单位相同'}{a.last_price != null && ` · 上次单价 ${a.last_price}`}</div>
            </div>
            {canEdit(a.party_kind) && <>
              <button className="btn-ghost" aria-label="修改" onClick={() => setF({ id: a.id, party_kind: a.party_kind, party: a.party_ref, code: a.code, name: a.name, pack_size: a.pack_size, unit: a.unit, factor: String(a.factor ?? '') })}><Pencil size={16} /></button>
              <button className="btn-ghost text-red-600" aria-label="删除" onClick={() => remove(a)}><Trash2 size={16} /></button>
            </>}
          </div>
        ))}
        {!rows.length && <Empty>还没有对照</Empty>}
      </div>
      {!f && (can('inventory.master') || can('order.manage')) && <button className="btn-secondary w-full sm:w-auto" onClick={() => setF({ ...EMPTY })}><Plus size={16} />加一个对照</button>}
      {f && (
        <div className="space-y-2 rounded-lg border border-brand-200 bg-brand-50/30 p-3">
          <Field label="哪一方">
            <select className="input" value={f.party_kind} disabled={!!f.id && f.party_kind === 'customer'} onChange={(e) => setF({ ...f, party_kind: e.target.value, party: '' })}>
              {(parties.data?.kinds || []).filter((k: any) => canEdit(k.key)).map((k: any) => <option key={k.key} value={k.key}>{k.label}</option>)}
            </select>
          </Field>
          {f.party_kind !== 'accounting' && (
            <Field label={f.party_kind === 'warehouse' ? '外库 / 来源地' : f.party_kind === 'customer' ? '客户' : f.party_kind === 'supplier' ? '供应商名' : '说明'}>
              {['warehouse', 'customer'].includes(f.party_kind)
                ? <select className="input" value={f.party} onChange={(e) => setF({ ...f, party: e.target.value })}><option value="">请选择</option>{opts.map((o: any) => <option key={o.value} value={o.value}>{o.label}</option>)}</select>
                : <><input className="input" list="alias-parties" value={f.party} onChange={(e) => setF({ ...f, party: e.target.value })} /><datalist id="alias-parties">{opts.map((o: any) => <option key={o.value} value={o.value} />)}</datalist></>}
            </Field>
          )}
          <div className="grid grid-cols-2 gap-2">
            <Field label={f.party_kind === 'customer' ? 'Item#' : f.party_kind === 'accounting' ? 'QuickBooks 编码（可空）' : '对方编码'}><input className="input" value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} /></Field>
            <Field label={f.party_kind === 'accounting' ? 'QuickBooks 品名' : '对方品名'}><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
            {f.party_kind !== 'accounting' && <>
            <Field label="规格"><input className="input" placeholder="120g×4×16/CS" value={f.pack_size} onChange={(e) => setF({ ...f, pack_size: e.target.value })} /></Field>
            <Field label="对方单位"><input className="input" placeholder="箱 / 袋 / 磅" value={f.unit} onChange={(e) => setF({ ...f, unit: e.target.value })} /></Field>
            </>}
          </div>
          {f.party_kind !== 'accounting' && <Field label={`换算：对方 1 ${f.unit || '个单位'} = 多少 ${sku.base_unit}`}><input className="input" type="number" inputMode="decimal" placeholder="单位相同填 1" value={f.factor} onChange={(e) => setF({ ...f, factor: e.target.value })} /></Field>}
          <div className="flex gap-2"><button className="btn-primary flex-1 sm:flex-none" disabled={!(f.code || f.name) || (f.party_kind !== 'accounting' && !f.party)} onClick={save}>保存</button><button className="btn-ghost" onClick={() => setF(null)}>取消</button></div>
        </div>
      )}
    </div>
  )
}
