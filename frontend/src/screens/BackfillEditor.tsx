import { useEffect, useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import { get, post } from '../lib/api'
import { num } from '../lib/fmt'
import { Badge, Field, Segmented, errMsg, useToast } from '../components/ui'

export const KIND_HINT: Record<string, string> = {
  purchase_receipt: '超市 / 批发店买了直接拿回来：一张小票里可以混着入库的货和不管库存的东西。',
  purchase_order: '只是向供应商订货（含装柜、运输资料），不能增加库存；货到了再补「收货」或正常收货。',
  receiving: '货到了、收进仓库：库存没记过才选「补记库存」。',
  transfer: '多伦多 / 温哥华 / 外库调来的货。已经导入过库存的柜（期初、盘点、调入单）只补单据。',
  production_use: '生产领了多少原料（扣原料库存）；产出的成品一般已在发货里体现，默认只补单据。',
  sales_delivery: '已经送给客户的货。关联了订单 / 送货单且发货时已扣库存的，不能再扣。',
  expense: '费用（停车、维修人工等），只记金额，不管库存。',
}
const ITEM_TYPES = ['食品原料', '调料辅料', '包材', '生产用品', '维修材料', '设备', '成品', '费用']
const NO_STOCK = new Set(['purchase_order', 'expense'])

export const blankLine = () => ({ name: '', item_type: '', sku_id: '', qty: '', unit: '', factor: '', amount: '', lot_code: '', inventory: 'doc_only', direction: '' })

/** 每行的库存影响：变动前 → 变动后；有问题的行红色，疑似已记过的黄色要人逐行勾。 */
export function PlanView({ plan, ack, setAck }: { plan: any; ack?: Set<string>; setAck?: (s: Set<string>) => void }) {
  if (!plan) return null
  return (
    <div className="space-y-2" aria-label="库存影响预览">
      {plan.dups?.length > 0 && <div className="rounded bg-amber-50 p-2 text-xs text-amber-800"><b>疑似重复 / 已有相关记录：</b>{plan.dups.join('；')}</div>}
      {[...(plan.blockers || []), ...(plan.warnings || [])].map((w: string, i: number) => <div key={i} className="rounded bg-amber-50 p-2 text-xs text-amber-800">{w}</div>)}
      {plan.lines.map((r: any) => (
        <div key={r.line_id} className={`rounded-lg border p-2 text-sm ${r.mode === 'post' && r.blockers.length ? 'border-red-300 bg-red-50/40' : r.suspects.length && r.deltas.length ? 'border-amber-300 bg-amber-50/40' : 'border-slate-200'}`}>
          <div className="flex flex-wrap items-center gap-2">
            <b className="min-w-0 break-words">{r.name}</b>
            <Badge color={r.mode === 'post' ? 'blue' : 'gray'}>{r.mode === 'post' ? '补记库存' : '只补单据'}</Badge>
            {(r.item_type || r.category) && <span className="text-xs text-slate-500">{[r.item_type, r.category].filter((x, i, a) => x && a.indexOf(x) === i).join(' · ')}</span>}
          </div>
          <div className="text-xs text-slate-500">{r.sku ? `商品：${r.sku}` : r.sku_note || '未对应商品'}{r.sku && r.sku_note ? ` · ${r.sku_note}` : ''}{r.convert ? ` · ${r.convert}` : ''}{r.amount != null ? ` · $${num(r.amount)}` : ''}</div>
          {r.deltas.map((d: any, i: number) => (
            <div key={i} className="text-xs">{d.warehouse}{d.lot_code ? ` · Lot ${d.lot_code}${d.new_lot ? '（新）' : ''}` : ''}：<b className={d.qty > 0 ? 'text-emerald-700' : 'text-red-700'}>{d.qty > 0 ? '+' : ''}{num(d.qty)}</b> {r.base_unit} · {num(d.before)} → <b>{num(d.after)}</b></div>
          ))}
          {r.mode === 'doc_only' && !r.deltas.length && <div className="text-xs text-slate-400">不动库存</div>}
          {r.mode === 'post' && r.blockers.map((b: string, i: number) => <div key={i} className="text-xs text-red-700">✕ {b}</div>)}
          {r.suspects.length > 0 && r.deltas.length > 0 && (
            <div className="mt-1 rounded bg-amber-100/60 p-1.5 text-xs text-amber-900">
              <div className="font-semibold">疑似已经记过：</div>
              {r.suspects.map((s: string, i: number) => <div key={i}>· {s}</div>)}
              {setAck && <label className="mt-1 flex items-center gap-2 font-semibold"><input type="checkbox" checked={!!ack?.has(r.line_id)} onChange={() => { const n = new Set(ack); n.has(r.line_id) ? n.delete(r.line_id) : n.add(r.line_id); setAck(n) }} />我核对过，这笔确实没记过</label>}
            </div>
          )}
          {r.info.map((s: string, i: number) => <div key={i} className="text-xs text-slate-500">{s}</div>)}
        </div>
      ))}
    </div>
  )
}

/** 新建 / 修改补录单。已确认的单要写修改原因，库存只按差额记。 */
export function BackfillEditor({ initial, confirmed, onSave, onCancel }: { initial?: any; confirmed?: boolean; onSave: (body: any, reason: string) => Promise<void>; onCancel: () => void }) {
  const toast = useToast()
  const [meta, setMeta] = useState<any>(null)
  const [master, setMaster] = useState<any>(null)
  const [h, setH] = useState<any>(() => initial ? { ...initial } : { kind: 'purchase_receipt', business_date: '', supplier: '', doc_no: '', container_no: '', goods_status: 'arrived', warehouse_id: '', from_warehouse_id: '', payment_method: '', paid_by: '', total_amount: '', reason: '', note: '' })
  const [lines, setLines] = useState<any[]>(() => initial?.lines?.length ? initial.lines.map((l: any) => ({ ...blankLine(), ...l, sku_id: l.sku_id || (l.new_sku ? 'new' : ''), new_unit: l.new_sku?.base_unit || '' })) : [blankLine()])
  const [links, setLinks] = useState<any[]>(initial?.links || [])
  const [link, setLink] = useState({ type: 'order', id: '' })
  const [plan, setPlan] = useState<any>(null)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { get('/api/backfill/meta').then(setMeta).catch(() => {}); get('/api/inv/master').then(setMaster).catch(() => {}) }, [])
  const set = (k: string, v: any) => { setH({ ...h, [k]: v }); setPlan(null) }
  const setL = (i: number, k: string, v: any) => { const n = [...lines]; n[i] = { ...n[i], [k]: v }; setLines(n); setPlan(null) }
  const skus = (master?.skus || []).filter((s: any) => s.active !== false)
  const whs = master?.warehouses || []
  const body = () => ({
    ...h, total_amount: h.total_amount === '' ? null : Number(h.total_amount), warehouse_id: h.warehouse_id || null, from_warehouse_id: h.from_warehouse_id || null,
    links: links.map((x) => ({ type: x.type, id: x.id })),
    lines: lines.map((l) => {
      const o: any = { name: l.name, item_type: l.item_type || undefined, inventory: l.inventory, unit: l.unit || undefined, lot_code: l.lot_code || undefined, direction: l.direction || undefined, note: l.note || undefined }
      if (l.id) o.id = l.id
      for (const k of ['qty', 'factor', 'amount']) if (l[k] !== '' && l[k] != null) o[k] = Number(l[k])
      if (l.sku_id === 'new') o.new_sku = { name: l.name, base_unit: l.new_unit || l.unit || '箱', category: l.category || undefined }
      else if (l.sku_id) o.sku_id = l.sku_id
      return o
    }),
  })
  const preview = async () => {
    try { setBusy(true); setPlan((await post('/api/backfill/preview', body())).plan) } catch (e) { toast(errMsg(e), 'err') } finally { setBusy(false) }
  }
  const save = async () => {
    try { setBusy(true); await onSave(body(), reason) } catch (e) { toast(errMsg(e), 'err') } finally { setBusy(false) }
  }
  const noStock = NO_STOCK.has(h.kind)
  return (
    <div className="space-y-3">
      <Field label="补录类型">
        <select className="input" value={h.kind} disabled={confirmed} onChange={(e) => set('kind', e.target.value)}>
          {Object.entries(meta?.kinds || {}).map(([k, v]: any) => <option key={k} value={k}>{v}</option>)}
        </select>
      </Field>
      <p className="text-xs text-slate-500">{KIND_HINT[h.kind]}</p>
      <div className="grid gap-2 sm:grid-cols-2">
        <Field label="业务日期 *"><input type="date" className="input" value={h.business_date} onChange={(e) => set('business_date', e.target.value)} /></Field>
        <Field label="供应商 / 客户 / 来源"><input className="input" value={h.supplier} onChange={(e) => set('supplier', e.target.value)} /></Field>
        <Field label="单号（小票号 / 采购单号 / BOL / 送货单号）"><input className="input" value={h.doc_no} onChange={(e) => set('doc_no', e.target.value)} /></Field>
        <Field label="柜号"><input className="input" placeholder="如 2602" value={h.container_no} onChange={(e) => set('container_no', e.target.value)} /></Field>
        {h.kind === 'transfer' && <Field label="从哪里来 *"><select className="input" value={h.from_warehouse_id} onChange={(e) => set('from_warehouse_id', e.target.value)}><option value="">选择</option>{whs.map((w: any) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></Field>}
        <Field label={h.kind === 'transfer' ? '调到哪个仓库' : '仓库'}><select className="input" value={h.warehouse_id} onChange={(e) => set('warehouse_id', e.target.value)}><option value="">597 主场地（默认）</option>{whs.map((w: any) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></Field>
        <Field label="到货状态"><Segmented items={[{ key: 'arrived', label: '已到货' }, { key: 'in_transit', label: '在途 / 未到货' }]} value={h.goods_status} onChange={(k) => set('goods_status', k)} /></Field>
        <Field label="单据总额"><input className="input" inputMode="decimal" value={h.total_amount ?? ''} onChange={(e) => set('total_amount', e.target.value)} /></Field>
        <Field label="付款方式"><select className="input" value={h.payment_method} onChange={(e) => set('payment_method', e.target.value)}>{Object.entries(meta?.pay || { '': '' }).map(([k, v]: any) => <option key={k} value={k}>{v || '（不填）'}</option>)}</select></Field>
        {h.payment_method === 'personal' && <Field label="谁垫付的"><input className="input" value={h.paid_by} onChange={(e) => set('paid_by', e.target.value)} /></Field>}
      </div>
      <Field label="补录原因 *"><input className="input" placeholder="如：上周猪肉丁原料采购没录" value={h.reason} onChange={(e) => set('reason', e.target.value)} /></Field>

      <div className="rounded-lg border border-slate-200 p-2">
        <div className="mb-1 text-sm font-semibold">关联已有记录</div>
        <div className="flex flex-wrap gap-1">{links.map((x, i) => <span key={i} className="chip bg-slate-100">{x.label || `${x.type} ${x.id}`}<button className="ml-1 text-slate-400" aria-label="移除关联" onClick={() => { setLinks(links.filter((_, j) => j !== i)); setPlan(null) }}>×</button></span>)}</div>
        <div className="mt-1 flex flex-wrap gap-1">
          <select className="input w-auto" value={link.type} onChange={(e) => setLink({ ...link, type: e.target.value })}>{Object.entries(meta?.link_types || {}).map(([k, v]: any) => <option key={k} value={k}>{v}</option>)}</select>
          <input className="input min-w-0 flex-1" placeholder="单号 / PO 号 / 送货单号" value={link.id} onChange={(e) => setLink({ ...link, id: e.target.value })} />
          <button className="btn-secondary" disabled={!link.id.trim()} onClick={() => { setLinks([...links, { ...link, id: link.id.trim() }]); setLink({ ...link, id: '' }); setPlan(null) }}>加</button>
        </div>
      </div>

      <div className="space-y-2">
        <div className="text-sm font-semibold">明细</div>
        {lines.map((l, i) => {
          const sku = skus.find((s: any) => s.id === l.sku_id)
          const base = sku?.base_unit || (l.sku_id === 'new' ? l.new_unit || l.unit : '')
          return (
            <div key={i} className="space-y-2 rounded-lg border border-slate-200 p-2">
              <div className="flex gap-2">
                <input className="input min-w-0 flex-1" aria-label="品名" placeholder="品名" value={l.name} onChange={(e) => setL(i, 'name', e.target.value)} />
                <button className="btn-ghost" aria-label="删除这一行" onClick={() => { setLines(lines.filter((_, j) => j !== i)); setPlan(null) }}><Trash2 size={16} /></button>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <select className="input" aria-label="物品类型" value={l.item_type} onChange={(e) => setL(i, 'item_type', e.target.value)}><option value="">物品类型</option>{ITEM_TYPES.map((t) => <option key={t}>{t}</option>)}</select>
                <select className="input" aria-label="对应商品" value={l.sku_id} onChange={(e) => setL(i, 'sku_id', e.target.value)}>
                  <option value="">不对应商品（只记金额）</option><option value="new">＋ 新建商品（同名会复用）</option>
                  {skus.map((s: any) => <option key={s.id} value={s.id}>{s.name}（{s.base_unit}）</option>)}
                </select>
                <input className="input" aria-label="数量" inputMode="decimal" placeholder="数量" value={l.qty} onChange={(e) => setL(i, 'qty', e.target.value)} />
                <input className="input" aria-label="单位" placeholder={base ? `单位（默认 ${base}）` : '单位'} value={l.unit} onChange={(e) => setL(i, 'unit', e.target.value)} />
                {l.sku_id === 'new' && <input className="input" aria-label="新商品基本单位" placeholder="新商品基本单位（如 箱 / 磅）" value={l.new_unit} onChange={(e) => setL(i, 'new_unit', e.target.value)} />}
                {base && l.unit && l.unit !== base && <input className="input" aria-label="换算" inputMode="decimal" placeholder={`1 ${l.unit} = ? ${base}`} value={l.factor} onChange={(e) => setL(i, 'factor', e.target.value)} />}
                <input className="input" aria-label="金额" inputMode="decimal" placeholder="金额" value={l.amount} onChange={(e) => setL(i, 'amount', e.target.value)} />
                <input className="input" aria-label="Lot" placeholder="Lot（食品不填自动编）" value={l.lot_code} onChange={(e) => setL(i, 'lot_code', e.target.value)} />
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Segmented items={[{ key: 'doc_only', label: '只补单据' }, { key: 'post', label: '补记库存' }]} value={noStock ? 'doc_only' : l.inventory} onChange={(k) => !noStock && setL(i, 'inventory', k)} />
                {h.kind === 'production_use' && <Segmented items={[{ key: '', label: '领料耗用' }, { key: 'output', label: '产出' }]} value={l.direction} onChange={(k) => setL(i, 'direction', k)} />}
              </div>
            </div>
          )
        })}
        <button className="btn-secondary" onClick={() => setLines([...lines, blankLine()])}><Plus size={16} />加一行</button>
      </div>

      <button className="btn-secondary w-full" disabled={busy} onClick={preview}>预览每行库存影响</button>
      <PlanView plan={plan} />
      {confirmed && <Field label="修改原因 *（已确认的单只按差额记库存，旧流水保留）"><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>}
      <div className="flex gap-2">
        <button className="btn-primary flex-1" disabled={busy || !h.business_date || !h.reason.trim() || (confirmed && !reason.trim())} onClick={save}>{confirmed ? '保存修改（记差额）' : initial ? '保存草稿' : '存为草稿'}</button>
        <button className="btn-secondary" onClick={onCancel}>取消</button>
      </div>
    </div>
  )
}
