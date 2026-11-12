import { useMemo, useState } from 'react'
import { ArrowLeft, Merge, Pencil, Plus, Power, Trash2 } from 'lucide-react'
import { del, get, patch, post, put } from '../lib/api'
import { useAuth } from '../lib/auth'
import { Badge, Empty, Field, Modal, Spinner, errMsg, useLoad, useToast } from '../components/ui'
import { CatBadge, CategoryPicker, FOOD, UNCAT, saveCategory } from './categories'

const KIND_COLOR: Record<string, string> = { customer: 'blue', warehouse: 'purple', supplier: 'green', accounting: 'yellow', other: 'gray' }
const SHORT: Record<string, string> = { customer: '客户', warehouse: '外库', supplier: '供应商', accounting: 'QB', other: '旧称' }

type Alias = { id: string; source: string; party_kind: string; kind_label: string; party: string; party_ref: string; code: string; name: string
  pack_size: string; unit: string; factor: number; last_price: number | null; note: string; sku: { id: string; code: string; name: string; base_unit: string } | null }

/** 商品主档 + 对照（一物多码）：库存只记我们的商品，各方的编码 / 品名挂在下面；可新建、编辑、停用、合并。 */
export function AliasManager({ skuId, onClose }: { skuId?: string; onClose: () => void }) {
  const { can } = useAuth()
  const master = useLoad(() => get('/api/inv/master?include_inactive=1'), [])
  const [showOff, setShowOff] = useState(false)
  const [creating, setCreating] = useState(false)
  const all = useLoad<Alias[]>(() => get('/api/aliases'), [])
  const [q, setQ] = useState('')
  const [sel, setSel] = useState<string | null>(skuId || null)
  const bySku = useMemo(() => {
    const m: Record<string, Alias[]> = {}
    for (const a of all.data || []) if (a.sku) (m[a.sku.id] ||= []).push(a)
    return m
  }, [all.data])
  if (!master.data || !all.data) return <Modal open onClose={onClose} title="商品"><Spinner /></Modal>
  const norm = (s: string) => (s || '').toLowerCase().replace(/[^0-9a-z一-鿿]/g, '')
  const n = norm(q)
  const off = master.data.skus.filter((s: any) => !s.active).length
  const skus = master.data.skus.filter((s: any) => (showOff || s.active) && (!n || norm(s.code).includes(n) || norm(s.name).includes(n)
    || norm(s.tnt_item_no).includes(n) || (bySku[s.id] || []).some((a) => norm(a.code).includes(n) || norm(a.name).includes(n))))
  const reload = () => { master.reload(); all.reload() }
  const back = <button className="-ml-2 flex min-h-11 items-center gap-1 rounded px-2 hover:bg-slate-50" onClick={() => { setSel(null); setCreating(false) }} aria-label="返回列表"><ArrowLeft size={18} />返回</button>
  if (creating) return <Modal open onClose={onClose} wide title={back}><SkuForm onDone={(id) => { setCreating(false); reload(); if (id) setSel(id) }} /></Modal>
  const sku = sel && master.data.skus.find((s: any) => s.id === sel)
  return (
    <Modal open onClose={onClose} wide title={sku ? back : '商品与对照'}>
      {sku ? <SkuAliases sku={sku} skus={master.data.skus} rows={bySku[sku.id] || []} onChanged={all.reload} onSkuChanged={reload} onGone={() => { setSel(null); reload() }} /> : (
        <div className="space-y-3">
          {can('inventory.master') && <button className="btn-primary w-full sm:w-auto" onClick={() => setCreating(true)}><Plus size={16} />新建商品</button>}
          <p className="text-sm text-slate-600">商品以<b>我们的品名</b>为主，大统华 Item# 作补充编号；库存只记这一个商品。大统华 Item#、外库（Supreme）代码、供应商编码、QuickBooks 品名都挂在对应 SKU 下面；单据进来时自动认出，确认页里选过一次的也会记住。</p>
          <input className="input" type="search" placeholder="搜品名，或任何一方的编码（538949、Vatee-001…）" value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="space-y-2">
            {skus.map((s: any) => {
              const list = bySku[s.id] || []
              return (
                <button key={s.id} className="w-full rounded-lg border border-slate-200 p-3 text-left hover:bg-slate-50" onClick={() => setSel(s.id)}>
                  <div className="flex items-baseline gap-2"><span className={`min-w-0 flex-1 truncate font-semibold ${s.active ? '' : 'text-slate-400'}`}>{s.name}</span>{!s.active && <Badge color="gray">{s.merged_into ? '已合并' : '已停用'}</Badge>}<CatBadge c={s.category} />{s.tnt_item_no && <span className="font-mono text-sm">T&amp;T {s.tnt_item_no}</span>}<span className="text-xs text-slate-400">{s.base_unit}</span></div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {list.map((a) => <Badge key={a.id} color={KIND_COLOR[a.party_kind]}>{SHORT[a.party_kind]} {a.party_kind === 'accounting' ? a.name || a.code : a.code || a.name}</Badge>)}
                    {!list.length && <span className="text-xs text-slate-400">还没有对照</span>}
                  </div>
                </button>
              )
            })}
            {!skus.length && <Empty>没找到。国内叫法、外库代码对不上的话，先找到我们的商品再加对照；真没有就「新建商品」。</Empty>}
            {off > 0 && <button className="btn-ghost btn-sm" onClick={() => setShowOff(!showOff)}>{showOff ? '隐藏' : '显示'}已停用 / 已合并的 {off} 个</button>}
          </div>
        </div>
      )}
    </Modal>
  )
}

const EMPTY = { party_kind: 'warehouse', party: '', code: '', name: '', pack_size: '', unit: '', factor: '' }

function SkuAliases({ sku, skus, rows, onChanged, onSkuChanged, onGone }: { sku: any; skus: any[]; rows: Alias[]; onChanged: () => void; onSkuChanged: () => void; onGone: () => void }) {
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
  const setCat = async (c: string) => { try { await saveCategory(sku.id, c); toast(`已归为${c}`); onSkuChanged() } catch (e) { toast(errMsg(e), 'err') } }
  const [mode, setMode] = useState<'' | 'edit' | 'merge'>('')
  const master = can('inventory.master')
  const toggle = async () => {
    let reason = ''
    if (sku.active) {
      const us = await get(`/api/inv/skus/${sku.id}/usage`)
      if (us.has_stock || us.reservations) {
        reason = prompt(`「${sku.name}」还有库存，一般应先盘点为 0 或合并到别的商品。确要停用请写原因：`) || ''
        if (!reason.trim()) return
      } else if (!confirm(`停用「${sku.name}」？停用后不出现在选择列表里，历史单据照常可查，可以再启用。`)) return
    }
    try { await patch(`/api/inv/skus/${sku.id}`, { active: !sku.active, reason }); toast(sku.active ? '已停用' : '已启用'); onSkuChanged() } catch (e) { toast(errMsg(e), 'err') }
  }
  if (mode === 'edit') return <SkuForm sku={sku} onDone={() => { setMode(''); onSkuChanged() }} />
  if (mode === 'merge') return <MergeForm sku={sku} skus={skus} onCancel={() => setMode('')} onDone={onGone} />
  return (
    <div className="space-y-3">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2"><span className="text-lg font-semibold">{sku.name}</span>{!sku.active && <Badge color="gray">{sku.merged_into ? '已合并' : '已停用'}</Badge>}{sku.usage && <Badge color={sku.usage === 'production' ? 'purple' : 'blue'}>{sku.usage === 'production' ? '自用' : '转卖'}</Badge>}</div>
          {sku.twin_id && <div className="text-xs text-slate-500">{sku.usage === 'production' ? '对应转卖商品' : '对应自用商品'}：{skus.find((x: any) => x.id === sku.twin_id)?.name || '—'}</div>}
          <div className="text-xs text-slate-500">{sku.tnt_item_no ? `大统华 Item# ${sku.tnt_item_no} · ` : '还没有大统华 Item#（看到 PO 会自动补上）· '}基本单位 {sku.base_unit} · 内部编号 {sku.code}
            {sku.purchase_pack ? ` · 采购 ${sku.purchase_pack}/箱` : ''}{sku.ship_pack ? ` · 出货 ${sku.ship_pack}/箱` : ''}</div>
        </div>
        {master && sku.active && <button className="btn-secondary btn-sm shrink-0" onClick={() => setMode('edit')}><Pencil size={14} />编辑</button>}
      </div>
      {sku.merged_into && <div className="rounded bg-slate-50 p-2 text-sm text-slate-600">已并入「{skus.find((x: any) => x.id === sku.merged_into)?.name || '另一个商品'}」，库存和对照都在那边。</div>}
      {sku.active && <div><div className="mb-1 flex items-center gap-2 text-sm font-medium">分类 <CatBadge c={sku.category} /></div>{master && <CategoryPicker value={sku.category} onPick={setCat} />}</div>}
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
      {master && !sku.merged_into && (
        <div className="flex flex-wrap gap-2 border-t pt-3">
          {sku.active && <button className="btn-secondary" onClick={() => setMode('merge')}><Merge size={16} />和另一个商品是同一个东西？合并</button>}
          <button className="btn-ghost" onClick={toggle}><Power size={16} />{sku.active ? '停用' : '重新启用'}</button>
        </div>
      )}
    </div>
  )
}

const UNITS = ['箱', '磅', '包', '袋', '盒', '个', '桶', '瓶', '台']

/** 新建 / 编辑商品（品名为主；内部编号可空 = 自动编号）。 */
function SkuForm({ sku, onDone }: { sku?: any; onDone: (id?: string) => void }) {
  const toast = useToast()
  const [f, setF] = useState<any>(() => ({ name: sku?.name || '', category: sku?.category || UNCAT, tnt_item_no: sku?.tnt_item_no || '', base_unit: sku?.base_unit || '箱',
    purchase_pack: sku?.purchase_pack ?? '', ship_pack: sku?.ship_pack ?? '', code: sku?.code || '', barcode: sku?.barcode || '', usage: sku?.usage ?? '' }))
  const [busy, setBusy] = useState(false)
  const save = async () => {
    setBusy(true)
    const body: any = { ...f, purchase_pack: f.purchase_pack === '' ? (sku ? 0 : null) : Number(f.purchase_pack), ship_pack: f.ship_pack === '' ? (sku ? 0 : null) : Number(f.ship_pack) }
    if (sku) { if (body.base_unit === sku.base_unit) delete body.base_unit; if (!body.tnt_item_no) delete body.tnt_item_no }
    try {
      const r = sku ? await patch(`/api/inv/skus/${sku.id}`, body) : await post('/api/inv/skus', body)
      toast(sku ? '已保存' : `已新建（${r.code}）`); onDone(sku ? undefined : r.id)
    } catch (e) { toast(errMsg(e), 'err') } finally { setBusy(false) }
  }
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value })
  return (
    <div className="space-y-3">
      <div className="text-lg font-semibold">{sku ? `编辑「${sku.name}」` : '新建商品'}</div>
      <Field label="品名（我们自己的叫法，不能重名）"><input className="input" value={f.name} onChange={set('name')} placeholder="如 剁椒" /></Field>
      <div><div className="label">分类</div><CategoryPicker value={f.category} onPick={(c) => setF({ ...f, category: c, usage: f.usage || (['原料肉', '原料菜'].includes(c) ? 'production' : FOOD.has(c) ? 'resale' : '') })} /></div>
      {FOOD.has(f.category) && (
        <div><div className="label">用途</div>
          <div className="grid grid-cols-2 gap-1.5">
            {[['resale', '转卖（卖给大统华等）'], ['production', '自用（腌制 / 切配用料）']].map(([k, l]) => (
              <button key={k} type="button" aria-pressed={(f.usage || 'resale') === k} onClick={() => setF({ ...f, usage: k })}
                className={`min-h-11 rounded-lg border px-2 text-sm ${(f.usage || 'resale') === k ? 'border-brand-600 bg-brand-600 font-semibold text-white' : 'border-slate-200 bg-white'}`}>{l}</button>
            ))}
          </div>
          <p className="mt-1 text-xs text-slate-500">同一种货两种用途都有（如五香粉），建成两个商品：「五香粉」转卖、「五香粉（自用）」自用。录小票时可拆，库存里也能「转用途」。</p>
        </div>
      )}
      <div className="grid grid-cols-2 gap-2">
        <Field label="大统华 Item#（可空）"><input className="input" inputMode="numeric" value={f.tnt_item_no} onChange={set('tnt_item_no')} placeholder="以后看到 PO 会自动补" /></Field>
        <Field label="基本单位"><select className="input" value={f.base_unit} onChange={set('base_unit')}>{[...new Set([...UNITS, f.base_unit])].map((x) => <option key={x}>{x}</option>)}</select></Field>
        <Field label="采购：每箱多少（可空）"><input className="input" type="number" inputMode="decimal" value={f.purchase_pack} onChange={set('purchase_pack')} /></Field>
        <Field label="出货：每箱多少（可空）"><input className="input" type="number" inputMode="decimal" value={f.ship_pack} onChange={set('ship_pack')} /></Field>
        <Field label="内部编号（可空 = 自动）"><input className="input" value={f.code} onChange={set('code')} /></Field>
        <Field label="条码（可空）"><input className="input" value={f.barcode} onChange={set('barcode')} /></Field>
      </div>
      {f.base_unit === '磅' && <p className="text-xs text-slate-500">按磅记的是称重品（肉、切粒），每箱实重不同，出货逐箱称重。</p>}
      {sku && <p className="text-xs text-slate-500">已有库存的商品不能改基本单位；国内叫法、外库代码请在下面「加一个对照」，不要改品名。</p>}
      <div className="flex gap-2"><button className="btn-primary flex-1 sm:flex-none" disabled={!f.name.trim() || busy} onClick={save}>保存</button><button className="btn-ghost" onClick={() => onDone()}>取消</button></div>
    </div>
  )
}

/** 合并：把这个商品并入另一个（同一个东西被建了两次）。 */
function MergeForm({ sku, skus, onCancel, onDone }: { sku: any; skus: any[]; onCancel: () => void; onDone: () => void }) {
  const toast = useToast()
  const usage = useLoad(() => get(`/api/inv/skus/${sku.id}/usage`), [sku.id])
  const [into, setInto] = useState('')
  const [q, setQ] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const n = q.trim().toLowerCase()
  const cands = skus.filter((x: any) => x.active && x.id !== sku.id && (!n || x.name.toLowerCase().includes(n) || (x.tnt_item_no || '').includes(n) || x.code.toLowerCase().includes(n)))
  const dst = skus.find((x: any) => x.id === into)
  const u = usage.data
  const go = async () => {
    setBusy(true)
    try { const r = await post(`/api/inv/skus/${sku.id}/merge`, { into_id: into, reason }); toast(`已并入「${r.into.name}」`); onDone() } catch (e) { toast(errMsg(e), 'err') } finally { setBusy(false) }
  }
  return (
    <div className="space-y-3">
      <div className="text-lg font-semibold">合并「{sku.name}」</div>
      <p className="text-sm text-slate-600">同一个东西被建成了两个商品时用（如国内采购叫「三樱辣椒段」、卖给大统华叫「剁椒」）。只是叫法不同、另一个名字没建成商品的话，不用合并，在商品里「加一个对照」就行。</p>
      <Field label="并入哪个商品（保留它）"><input className="input" type="search" placeholder="搜品名 / 大统华 Item#" value={q} onChange={(e) => setQ(e.target.value)} /></Field>
      <div className="max-h-56 space-y-1 overflow-y-auto">
        {cands.slice(0, 30).map((x: any) => (
          <button key={x.id} aria-pressed={into === x.id} onClick={() => setInto(x.id)}
            className={`flex min-h-11 w-full items-center gap-2 rounded-lg border px-3 text-left text-sm ${into === x.id ? 'border-brand-600 bg-brand-50' : 'border-slate-200'}`}>
            <span className="min-w-0 flex-1 truncate">{x.name}</span>{x.tnt_item_no && <span className="font-mono text-xs">T&amp;T {x.tnt_item_no}</span>}<CatBadge c={x.category} />
          </button>
        ))}
        {!cands.length && <Empty>没有可选的商品</Empty>}
      </div>
      {dst && u && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          <div className="font-medium">合并后：</div>
          <ul className="ml-4 list-disc text-xs">
            <li>「{sku.name}」的库存{u.has_stock ? `（${Object.entries(u.stock).map(([k, v]) => `${({ on_hand: '实物', in_transit: '在途', unverified: '未核数' } as any)[k] || k} ${v}`).join('、')}）` : ''}转到「{dst.name}」，Lot 跟过去，旧流水保留可追溯</li>
            {(u.order_lines + u.po_lines + u.transfer_lines + u.aliases) > 0 && <li>订单行 {u.order_lines}、采购行 {u.po_lines}、调拨行 {u.transfer_lines}、对照 {u.aliases} 改指向「{dst.name}」</li>}
            <li>「{sku.name}」停用；这个名字记成「{dst.name}」的旧称，以后国内单据上出现照样认得出</li>
            {sku.base_unit !== dst.base_unit && <li className="text-red-700">两边单位不同（{sku.base_unit} / {dst.base_unit}）{u.has_stock ? '，有库存时不能直接合并' : ''}</li>}
          </ul>
          <p className="mt-1 text-xs">合并不能撤销，确认是同一个东西再做。</p>
        </div>
      )}
      <Field label="原因"><input className="input" placeholder="如：老板确认国内采购名和大统华品名是同一个东西" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
      <div className="flex gap-2"><button className="btn-primary flex-1 sm:flex-none" disabled={!into || !reason.trim() || busy} onClick={go}><Merge size={16} />确认合并</button><button className="btn-ghost" onClick={onCancel}>取消</button></div>
    </div>
  )
}
