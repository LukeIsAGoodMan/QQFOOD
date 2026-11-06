import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import QRCode from 'qrcode'
import { AlertTriangle, Plus, Printer, Scale, Send, Trash2, Truck, Users } from 'lucide-react'
import { get, patch, post, uuid } from '../lib/api'
import { useAuth } from '../lib/auth'
import { dt, num } from '../lib/fmt'
import { Badge, Empty, Field, Modal, Segmented, Spinner, errMsg, useLoad, useToast } from '../components/ui'
import { EvidencePanel } from '../components/Evidence'
import { ScanButton } from '../components/Scan'
import { Timeline, type ModProps } from './basic'

const money = (n: any) => (n === null || n === undefined ? '—' : `$${Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`)
const ST_COLOR: Record<string, string> = { confirmed: 'blue', partial: 'yellow', shipped: 'purple', delivered: 'green', closed: 'gray', cancelled: 'gray' }
const unitLabel = (u: string) => (u === '磅' ? 'lb' : u)

// ---------------- labels (QR) ----------------
function QR({ text, size = 96 }: { text: string; size?: number }) {
  const [src, setSrc] = useState('')
  useEffect(() => { QRCode.toDataURL(text, { margin: 0, width: size * 2 }).then(setSrc) }, [text, size])
  return src ? <img src={src} style={{ width: size, height: size }} alt={text} /> : null
}

export function BoxLabels({ boxes, onClose }: { boxes: any[]; onClose: () => void }) {
  return (
    <Modal open onClose={onClose} wide title={`箱标签（${boxes.length}）`} footer={<button className="btn-primary" onClick={() => window.print()}><Printer size={16} />打印</button>}>
      <div className="print-area grid gap-3 sm:grid-cols-2">
        {boxes.map((b) => (
          <div key={b.serial} className="flex gap-3 rounded border-2 border-black p-3 text-black" style={{ breakInside: 'avoid' }}>
            <QR text={b.serial} />
            <div className="min-w-0 text-sm leading-5">
              <div className="text-base font-bold">{b.sku_name}</div>
              <div>{b.sku_code} · LOT <b>{b.lot_code || '—'}</b></div>
              <div className="text-2xl font-extrabold">{num(b.weight)} lb</div>
              <div>PACKED {dt(b.packed_at, { dateOnly: true })}{b.expiry && ` · EXP ${b.expiry}`}</div>
              <div className="font-mono text-xs">{b.serial}</div>
            </div>
          </div>
        ))}
      </div>
    </Modal>
  )
}

// ---------------- weigh & label ----------------
export function BoxesModule({ preview }: ModProps) {
  const { can } = useAuth()
  const toast = useToast()
  const master = useLoad(() => (preview ? Promise.resolve(null) : get('/api/inv/master')), [preview])
  const boxes = useLoad(() => (preview ? Promise.resolve([]) : get('/api/orders/boxes/list')), [preview])
  const [f, setF] = useState<any>({ sku_id: '', lot_code: '', expiry: '', post_stock: true })
  const [weights, setWeights] = useState<number[]>([])
  const [w, setW] = useState('')
  const [labels, setLabels] = useState<any[] | null>(null)
  const [key, setKey] = useState(uuid())
  const input = useRef<HTMLInputElement>(null)
  if (preview) return <div className="text-sm text-slate-400">称重打标签（预览不加载数据）</div>
  if (!master.data) return <Spinner />
  const cw = master.data.skus.filter((s: any) => ['磅', 'lb'].includes(s.base_unit))
  const addW = () => { const v = Number(w); if (v > 0 && v <= 200) { setWeights([...weights, v]); setW(''); input.current?.focus() } }
  const save = async () => {
    try {
      const r = await post('/api/orders/boxes', { ...f, expiry: f.expiry || null, lot_code: f.lot_code || null, weights, idempotency_key: key })
      setLabels(r.boxes); setWeights([]); setKey(uuid()); boxes.reload()
      toast(`已生成 ${r.boxes.length} 箱，共 ${num(r.total)} lb`)
    } catch (e) { toast(errMsg(e), 'err') }
  }
  const total = weights.reduce((a, b) => a + b, 0)
  const groups: Record<string, any[]> = {}
  for (const b of boxes.data || []) (groups[b.sku_code] ||= []).push(b)
  return (
    <div className="space-y-4">
      {can('inventory.post') && (
        <div className="space-y-3 rounded-lg border border-slate-200 p-3">
          {!cw.length && <div className="rounded bg-amber-50 p-2 text-xs text-amber-800">还没有称重品。称重品的 SKU 基本单位需为「磅」（可让 ChatGPT 用 propose_skus 设置，或在 SKU 里改）。</div>}
          <div className="grid gap-2 sm:grid-cols-3">
            <Field label="品项（称重品）"><select className="input" value={f.sku_id} onChange={(e) => setF({ ...f, sku_id: e.target.value })}><option value="">选择…</option>{cw.map((s: any) => <option key={s.id} value={s.id}>{s.code} {s.name}</option>)}</select></Field>
            <Field label="Lot（生产/切配批次）"><input className="input" value={f.lot_code} onChange={(e) => setF({ ...f, lot_code: e.target.value })} placeholder="如 CK1106" /></Field>
            <Field label="到期日"><input className="input" type="date" value={f.expiry} onChange={(e) => setF({ ...f, expiry: e.target.value })} /></Field>
          </div>
          <Field label="每箱重量（lb），回车加一箱">
            <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); addW() }}>
              <input ref={input} className="input text-2xl" type="number" inputMode="decimal" step="0.01" placeholder="50.2" value={w} onChange={(e) => setW(e.target.value)} />
              <button className="btn-secondary shrink-0" type="submit"><Plus size={16} />加一箱</button>
            </form>
          </Field>
          {weights.length > 0 && (
            <div className="flex flex-wrap gap-1.5">{weights.map((x, i) => <button key={i} className="chip min-h-[38px] bg-slate-100 px-3 text-sm text-slate-700" onClick={() => setWeights(weights.filter((_, j) => j !== i))}>#{i + 1} {x} lb ×</button>)}</div>
          )}
          <div className="flex flex-wrap gap-3 text-sm">
            <label className="flex min-h-[36px] items-center gap-1.5"><input type="radio" checked={f.post_stock} onChange={() => setF({ ...f, post_stock: true })} />称重入库（这批货还没记过库存）</label>
            <label className="flex min-h-[36px] items-center gap-1.5"><input type="radio" checked={!f.post_stock} onChange={() => setF({ ...f, post_stock: false })} />只打标签（已在库存里）</label>
          </div>
          <button className="btn-primary w-full" disabled={!f.sku_id || !weights.length} onClick={save}><Scale size={16} />生成 {weights.length} 箱 · {num(total)} lb · 打印标签</button>
        </div>
      )}
      <div>
        <div className="mb-2 text-sm font-semibold">在库称重箱</div>
        {Object.entries(groups).map(([code, bs]) => (
          <div key={code} className="mb-2 rounded-lg border border-slate-200 p-2 text-sm">
            <div className="flex items-center gap-2"><b>{code}</b><span className="text-xs text-slate-500">{bs[0].sku_name}</span>
              <span className="ml-auto">{bs.length} 箱 · {num(bs.reduce((a: number, b: any) => a + b.weight, 0))} lb</span></div>
            <div className="mt-1 flex flex-wrap gap-1">{bs.slice(0, 30).map((b: any) => <button key={b.id} onClick={() => setLabels([b])} className={`chip min-h-[38px] px-3 ${b.quality_status !== 'released' ? 'bg-red-100 text-red-700' : 'bg-slate-100 text-slate-600'}`}>{b.serial.slice(-5)} · {b.weight}</button>)}</div>
          </div>
        ))}
        {boxes.data && !boxes.data.length && <Empty>没有在库的称重箱</Empty>}
      </div>
      {labels && <BoxLabels boxes={labels} onClose={() => setLabels(null)} />}
    </div>
  )
}

// ---------------- packing slip ----------------
function PackingSlip({ so, shipment, onClose }: { so: any; shipment?: any; onClose: () => void }) {
  const rows = so.lines.map((l: any) => {
    // one shipment → that shipment's lines; whole order → merge every shipment of this line
    const parts = (shipment ? [shipment] : so.shipments).flatMap((sh: any) => sh.lines.filter((x: any) => x.line_id === l.id))
    return { l, qty: shipment ? parts.reduce((a: number, x: any) => a + (x.qty || 0), 0) : l.qty_shipped,
             boxes: parts.flatMap((x: any) => x.box_serials || []), lots: [...new Set(parts.flatMap((x: any) => x.lots || []))] }
  })
  return (
    <Modal open onClose={onClose} wide title="发货单（Packing Slip）" footer={<button className="btn-primary" onClick={() => window.print()}><Printer size={16} />打印</button>}>
      <div className="print-area mx-auto max-w-3xl space-y-3 bg-white p-4 text-sm text-black">
        <div className="flex justify-between">
          <div><div className="text-lg font-bold">Q&Q FOODS USA INC.</div><div>597 Monterey Pass Rd, Monterey Park, CA</div>{so.vendor_no && <div>Vendor # {so.vendor_no}</div>}</div>
          <div className="text-right"><div className="text-lg font-bold">PACKING SLIP</div><div>PO No.: <b>{so.po_no}</b></div><div>{shipment ? shipment.code : so.code}</div><div>Date: {dt(shipment?.shipped_at || new Date().toISOString(), { dateOnly: true })}</div></div>
        </div>
        <div><b>Ship to:</b> {so.site_detail?.name || so.site}{so.site_detail?.address && `, ${so.site_detail.address}`}{so.site_detail?.dept && ` · ${so.site_detail.dept}`}</div>
        <table className="w-full border-collapse text-xs">
          <thead><tr className="border-b-2 border-black">{['Item#', 'Description', 'Pack', 'Ordered', 'Shipped', 'Lot / Boxes'].map((h) => <th key={h} className="p-1 text-left">{h}</th>)}</tr></thead>
          <tbody>{rows.map(({ l, qty, boxes, lots }: any) => (
            <tr key={l.id} className="border-b align-top">
              <td className="p-1">{l.item_no}</td><td className="p-1">{l.description}</td><td className="p-1">{l.pack_size}</td>
              <td className="p-1">{num(l.qty_ordered)} {unitLabel(l.order_unit)}</td>
              <td className="p-1 font-bold">{num(qty)} {unitLabel(l.order_unit)}{boxes.length > 0 && ` / ${boxes.length} cs`}</td>
              <td className="p-1">{lots.join(', ')}{boxes.length > 0 && <div className="font-mono text-[10px]">{boxes.join(' ')}</div>}</td>
            </tr>))}</tbody>
        </table>
        <div className="text-xs">PO number must be presented on delivery. Received by: ____________________ Date: __________</div>
      </div>
    </Modal>
  )
}

// ---------------- ship dialog ----------------
function ShipDialog({ so, onClose, onDone }: { so: any; onClose: () => void; onDone: () => void }) {
  const toast = useToast()
  const [key] = useState(uuid())
  const open = so.lines.filter((l: any) => l.remaining > 1e-9 && l.sku_id)
  const [qty, setQty] = useState<Record<string, string>>(Object.fromEntries(open.filter((l: any) => !l.catch_weight).map((l: any) => [l.id, String(Math.min(l.remaining, l.available_base / (l.factor || 1)) > 0 ? Math.min(l.remaining, Math.floor(l.available_base / (l.factor || 1))) : '')])))
  const [boxSel, setBoxSel] = useState<Record<string, Set<string>>>({})
  const [over, setOver] = useState('')
  const [note, setNote] = useState('')
  const toggle = (lid: string, bid: string) => { const s = new Set(boxSel[lid] || []); s.has(bid) ? s.delete(bid) : s.add(bid); setBoxSel({ ...boxSel, [lid]: s }) }
  const scan = (code: string) => {
    for (const l of open.filter((x: any) => x.catch_weight)) {
      const b = l.boxes_in_stock.find((x: any) => x.serial === code.toUpperCase())
      if (b) { if (b.blocked) toast(`箱 ${b.serial} 所在批次未放行`, 'err'); else toggle(l.id, b.id); return }
    }
    toast(`箱 ${code} 不在这张订单可发的箱里`, 'err')
  }
  const submit = async () => {
    try {
      const lines = open.map((l: any) => (l.catch_weight ? { line_id: l.id, box_ids: [...(boxSel[l.id] || [])] } : { line_id: l.id, qty: qty[l.id] ? Number(qty[l.id]) : null }))
      const r = await post(`/api/orders/${so.id}/ship`, { idempotency_key: key, lines, note, over_reason: over })
      toast(r.duplicate ? '重复提交已拦截' : `已发货 ${r.shipment_code}`); onDone(); onClose()
    } catch (e: any) { toast(errMsg(e), 'err') }
  }
  return (
    <Modal open onClose={onClose} wide title={`发货：PO ${so.po_no}`} footer={<button className="btn-primary" onClick={submit}><Truck size={16} />确认发货</button>}>
      <div className="space-y-3">
        {open.some((l: any) => l.catch_weight) && <ScanButton label="扫箱标签" onResult={scan} />}
        {open.map((l: any) => {
          const sel = boxSel[l.id] || new Set()
          const w = (l.boxes_in_stock || []).filter((b: any) => sel.has(b.id)).reduce((a: number, b: any) => a + b.weight, 0)
          return (
            <div key={l.id} className="rounded-lg border border-slate-200 p-3 text-sm">
              <div className="font-medium">{l.description} <span className="text-xs font-normal text-slate-500">{l.item_no} · {l.sku_code}</span></div>
              <div className="text-xs text-slate-500">剩余 {num(l.remaining)} {unitLabel(l.order_unit)} · 可用 {num(l.available_base)} {l.base_unit}{l.short && <span className="ml-1 text-red-600">库存不够</span>}</div>
              {l.catch_weight ? (
                <div className="mt-2">
                  <div className="mb-1 text-xs">已选 {sel.size} 箱 · <b>{num(w)} lb</b> / 订 {num(l.remaining)} lb</div>
                  <div className="flex flex-wrap gap-1.5">{l.boxes_in_stock.map((b: any) => (
                    <button key={b.id} type="button" disabled={b.blocked} onClick={() => toggle(l.id, b.id)}
                      className={`chip min-h-[40px] px-3 ${b.blocked ? 'bg-red-50 text-red-400 line-through' : sel.has(b.id) ? 'bg-brand-600 text-white' : 'bg-slate-100 text-slate-700'}`}>{b.serial.slice(-5)} · {b.weight}</button>
                  ))}{!l.boxes_in_stock.length && <span className="text-xs text-amber-700">没有在库的称重箱，请先在「称重打标签」里称重</span>}</div>
                </div>
              ) : (
                <div className="mt-2 grid grid-cols-[1fr_auto] items-end gap-2">
                  <label><span className="label">本次发（{unitLabel(l.order_unit)}）</span><input className="input text-lg" type="number" inputMode="decimal" value={qty[l.id] ?? ''} onChange={(e) => setQty({ ...qty, [l.id]: e.target.value })} /></label>
                  <div className="pb-2 text-xs text-slate-500">先到期先出：{(l.lots || []).slice(0, 2).map((x: any) => `${x.lot_code}${x.expiry ? `(${x.expiry})` : ''}`).join('、') || '无可用批次'}</div>
                </div>
              )}
            </div>
          )
        })}
        {so.lines.some((l: any) => !l.sku_id) && <div className="rounded bg-amber-50 p-2 text-xs text-amber-800">有未配对 SKU 的行不能发货，请先在订单里配对。</div>}
        <Field label="超出订量的原因（大统华可能拒收；没有超量可不填）"><input className="input" value={over} onChange={(e) => setOver(e.target.value)} /></Field>
        <Field label="备注（车牌、司机等）"><input className="input" value={note} onChange={(e) => setNote(e.target.value)} /></Field>
      </div>
    </Modal>
  )
}

function DeliverDialog({ so, shipment, onClose, onDone }: any) {
  const toast = useToast()
  const [rej, setRej] = useState<Record<string, { qty: string; reason: string }>>({})
  const [exc, setExc] = useState('')
  const lines = so.lines.filter((l: any) => shipment.lines.some((x: any) => x.line_id === l.id))
  const go = async () => {
    try {
      await post(`/api/orders/shipments/${shipment.id}/deliver`, { exception_reason: exc,
        rejected: Object.entries(rej).filter(([, v]) => Number(v.qty) > 0).map(([line_id, v]) => ({ line_id, qty: Number(v.qty), reason: v.reason })) })
      onDone(); onClose()
    } catch (e) { toast(errMsg(e), 'err') }
  }
  return (
    <Modal open onClose={onClose} title={`登记送达：${shipment.code}`} footer={<button className="btn-primary" onClick={go}>确认送达</button>}>
      <div className="space-y-3">
        <EvidencePanel targetType="shipment" targetId={shipment.id} purposes={['签收单', 'POD', '送货照片']} compact />
        {!shipment.pod && <Field label="没有签收单？写原因（会留例外标签）"><input className="input" value={exc} onChange={(e) => setExc(e.target.value)} /></Field>}
        <div className="text-sm font-semibold">有拒收或短少吗？</div>
        {lines.map((l: any) => (
          <div key={l.id} className="grid grid-cols-[1fr_90px] gap-2 text-sm">
            <span className="self-center">{l.description}</span>
            <input className="input" type="number" inputMode="decimal" placeholder={`拒收 ${unitLabel(l.order_unit)}`} value={rej[l.id]?.qty || ''} onChange={(e) => setRej({ ...rej, [l.id]: { ...(rej[l.id] || { reason: '' }), qty: e.target.value } })} />
            {Number(rej[l.id]?.qty) > 0 && <input className="input col-span-2" placeholder="拒收原因 *" value={rej[l.id]?.reason || ''} onChange={(e) => setRej({ ...rej, [l.id]: { ...rej[l.id], reason: e.target.value } })} />}
          </div>
        ))}
      </div>
    </Modal>
  )
}

// ---------------- order detail ----------------
function SkuPicker({ onPick }: { onPick: (id: string, factor?: number) => void }) {
  const master = useLoad(() => get('/api/inv/master'), [])
  const [id, setId] = useState('')
  const [factor, setFactor] = useState('')
  return (
    <div className="mt-1 flex flex-wrap gap-1">
      <select className="input min-w-0 flex-1" value={id} onChange={(e) => setId(e.target.value)}><option value="">选我们的 SKU…</option>{(master.data?.skus || []).map((s: any) => <option key={s.id} value={s.id}>{s.code} {s.name}（{s.base_unit}）</option>)}</select>
      <input className="input w-24" type="number" inputMode="decimal" placeholder="换算×" value={factor} onChange={(e) => setFactor(e.target.value)} title="每个下单单位等于多少个 SKU 基本单位；同单位留空" />
      <button className="btn-secondary btn-sm" disabled={!id} onClick={() => onPick(id, factor ? Number(factor) : undefined)}>配对</button>
    </div>
  )
}

export function OrderDetail({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => void }) {
  const toast = useToast()
  const { data: so, reload: r0 } = useLoad(() => get(`/api/orders/${id}`), [id])
  const [dlg, setDlg] = useState<any>(null)
  const reload = () => { r0(); onChanged() }
  if (!so) return null
  const act = async (path: string, body?: any) => { try { await post(`/api/orders/${id}/${path}`, body); reload() } catch (e) { toast(errMsg(e), 'err') } }
  const map = async (lid: string, sku_id: string, factor?: number) => { try { await post(`/api/orders/lines/${lid}/map`, { sku_id, factor }); reload() } catch (e) { toast(errMsg(e), 'err') } }
  const changeDate = async () => {
    const v = prompt('要求交货日期（YYYY-MM-DD）', so.required_date || '')
    if (v === null) return
    try { await patch(`/api/orders/${id}`, { version: so.version, required_date: v }); reload() } catch (e) { toast(errMsg(e), 'err') }
  }
  const openish = ['confirmed', 'partial'].includes(so.status)
  return (
    <Modal open onClose={onClose} wide title={<span className="flex flex-wrap items-center gap-2">PO {so.po_no}<Badge color={ST_COLOR[so.status]}>{so.status_label}</Badge>{so.overdue && <Badge color="red">已过交期</Badge>}{so.due_soon && <Badge color="yellow">即将到期</Badge>}</span>}>
      <div className="space-y-4 pb-16 sm:pb-0">
        <dl className="kv">
          <dt>客户</dt><dd>{so.customer}{so.vendor_no && <span className="text-xs text-slate-400"> · 供应商号 {so.vendor_no}</span>}</dd>
          <dt>送货点</dt><dd>{so.site || <span className="text-amber-700">未指定</span>}</dd>
          <dt>交货</dt><dd className={so.overdue ? 'font-semibold text-red-600' : ''}>{so.required_date || '未定'} {so.can.manage && openish && <button className="btn-ghost btn-sm text-brand-700" onClick={changeDate}>修改</button>}</dd>
          <dt>下单</dt><dd>{so.po_date || '—'}{so.category && ` · ${so.category}`} · {so.code}</dd>
          <dt>金额</dt><dd><b>{money(so.total)}</b> · 已发 {money(so.shipped_amount)}</dd>
          {so.note && <><dt>备注</dt><dd className="whitespace-pre-wrap">{so.note}</dd></>}
        </dl>
        {so.exceptions.length > 0 && <div className="rounded-lg border border-violet-200 bg-violet-50 p-2 text-xs text-violet-900">{so.exceptions.map((e: any, i: number) => <div key={i}>{e.step}：{e.reason} — {e.by_name} · {dt(e.at)}</div>)}</div>}
        <div className="space-y-2">
          {so.lines.map((l: any) => (
            <div key={l.id} className={`rounded-lg border p-3 text-sm ${!l.sku_id ? 'border-amber-300 bg-amber-50/40' : l.short ? 'border-red-200' : 'border-slate-200'}`}>
              <div className="flex flex-wrap items-baseline gap-x-2"><span className="font-medium">{l.description}</span><span className="text-xs text-slate-400">{l.item_no && `#${l.item_no} · `}{l.pack_size} · {money(l.price)}/{unitLabel(l.order_unit)}</span><span className="ml-auto font-semibold">{money(l.amount)}</span></div>
              <div className="mt-1 grid grid-cols-4 gap-1 text-center text-xs">
                {[['订', l.qty_ordered], ['已发', l.qty_shipped], ['拒收', l.qty_rejected], ['剩余', l.remaining]].map(([k, v]: any) => (
                  <div key={k} className="rounded bg-slate-50 py-1"><div className="text-slate-400">{k}</div><div className={`font-semibold ${k === '拒收' && v > 0 ? 'text-red-600' : ''}`}>{num(v)}</div></div>
                ))}
              </div>
              {l.sku_id ? <div className="mt-1 text-xs text-slate-500">→ {l.sku_code} {l.sku_name}{l.factor !== 1 && ` ×${l.factor}`}{l.catch_weight && ' · 称重'} · 可用 {num(l.available_base)} {l.base_unit}{l.short && <span className="ml-1 font-semibold text-red-600">库存不够</span>}{l.catch_weight && ` · 在库 ${l.boxes_in_stock?.length || 0} 箱`}{l.boxes_shipped > 0 && ` · 已发 ${l.boxes_shipped} 箱`}</div>
                : so.can.manage ? <><div className="mt-1 text-xs text-amber-700">还没对上我们的 SKU</div><SkuPicker onPick={(sid, f) => map(l.id, sid, f)} /></> : <div className="text-xs text-amber-700">未配对 SKU</div>}
              {l.reject_reason && <div className="mt-1 text-xs text-red-600">拒收：{l.reject_reason}</div>}
            </div>
          ))}
        </div>
        {so.shipments.length > 0 && (
          <div>
            <div className="mb-2 text-sm font-semibold">发货记录</div>
            {so.shipments.map((s: any) => (
              <div key={s.id} className="mb-2 rounded-lg border border-slate-200 p-2 text-sm">
                <div className="flex flex-wrap items-center gap-2"><span className="font-mono">{s.code}</span><Badge color={s.status === 'delivered' ? 'green' : 'purple'}>{s.status === 'delivered' ? '已送达' : '已发出'}</Badge>
                  <span className="text-xs text-slate-400">{s.shipped_by} · {dt(s.shipped_at)}{s.delivered_at && ` · 送达 ${dt(s.delivered_at)}`}</span></div>
                <div className="text-xs text-slate-600">{s.lines.map((x: any) => { const l = so.lines.find((y: any) => y.id === x.line_id); return `${l?.description} ${num(x.qty)} ${unitLabel(l?.order_unit)}${x.boxes?.length ? `（${x.boxes.length} 箱）` : ''} ${x.lots?.join(',') || ''}` }).join('；')}</div>
                <div className="mt-1 flex gap-2">
                  <button className="btn-ghost btn-sm" onClick={() => setDlg({ t: 'slip', s })}><Printer size={14} />发货单</button>
                  {s.status !== 'delivered' && so.can.ship && <button className="btn-secondary btn-sm" onClick={() => setDlg({ t: 'deliver', s })}>登记送达</button>}
                </div>
              </div>
            ))}
          </div>
        )}
        <EvidencePanel title="PO 原件 / 附件" targetType="so" targetId={so.id} purposes={['PO 原件', '附件', '客户沟通']} compact />
        <div><div className="mb-2 text-sm font-semibold">沟通</div><Timeline targetType="so" targetId={so.id} /></div>
      </div>
      <div className="sticky bottom-0 -mx-4 -mb-4 flex gap-2 overflow-x-auto border-t bg-white px-4 py-2 pb-[max(0.5rem,env(safe-area-inset-bottom))] [&>button]:shrink-0">
        {openish && so.can.ship && <button className="btn-primary" onClick={() => setDlg({ t: 'ship' })}><Truck size={16} />发货</button>}
        <button className="btn-secondary" onClick={() => setDlg({ t: 'slip' })}><Printer size={16} />打印发货单</button>
        {openish && so.can.manage && so.shipments.length > 0 && <button className="btn-ghost" onClick={() => { const r = prompt('结案原因（剩余不再发，记得通知客户）'); if (r) act('close', { reason: r }) }}>按已发结案</button>}
        {openish && so.can.manage && !so.shipments.length && <button className="btn-ghost text-red-600" onClick={() => { const r = prompt('取消原因'); if (r) act('cancel', { reason: r }) }}>取消订单</button>}
      </div>
      {dlg?.t === 'ship' && <ShipDialog so={so} onClose={() => setDlg(null)} onDone={reload} />}
      {dlg?.t === 'slip' && <PackingSlip so={so} shipment={dlg.s} onClose={() => setDlg(null)} />}
      {dlg?.t === 'deliver' && <DeliverDialog so={so} shipment={dlg.s} onClose={() => setDlg(null)} onDone={reload} />}
    </Modal>
  )
}

// ---------------- customers ----------------
function Customers({ onClose }: { onClose: () => void }) {
  const toast = useToast()
  const { data, reload } = useLoad(() => get('/api/orders/customers'), [])
  const [site, setSite] = useState<any>(null)
  const add = async () => { try { await post(`/api/orders/customers/${site.customer_id}/sites`, site); setSite(null); reload() } catch (e) { toast(errMsg(e), 'err') } }
  return (
    <Modal open onClose={onClose} wide title="客户、送货点与品项对照">
      {(data || []).map((c: any) => (
        <div key={c.id} className="mb-4 space-y-2">
          <div className="font-semibold">{c.name} <span className="text-xs font-normal text-slate-400">{c.code} · 供应商号 {c.vendor_no || '—'} · 发票 {c.invoice_email || '—'}</span></div>
          <div className="text-sm font-medium">送货点</div>
          {c.sites.map((s: any) => <div key={s.id} className="rounded border border-slate-200 p-2 text-xs">{s.name}{s.store_no && ` (#${s.store_no})`} · {s.address}{s.dept && ` · ${s.dept}`}{s.contact && ` · ${s.contact}`}</div>)}
          <button className="btn-ghost btn-sm" onClick={() => setSite({ customer_id: c.id, name: '', store_no: '', address: '', dept: '', contact: '' })}><Plus size={14} />加送货点</button>
          <div className="text-sm font-medium">品项对照（{c.items.length}）</div>
          <div className="space-y-1">{c.items.map((i: any) => <div key={i.id} className="text-xs">{i.item_no && <span className="font-mono">#{i.item_no} </span>}{i.description} {i.pack_size && `(${i.pack_size})`} → <b>{i.sku_code}</b>{i.factor !== 1 && ` ×${i.factor}`} · {unitLabel(i.order_unit)}{i.last_price != null && ` · 上次 ${money(i.last_price)}`}</div>)}</div>
          {!c.items.length && <div className="text-xs text-slate-400">第一次录单时会自动记住</div>}
        </div>
      ))}
      {site && (
        <div className="space-y-2 rounded-lg bg-slate-50 p-3">
          <div className="grid grid-cols-2 gap-2">
            <input className="input" placeholder="名称，如 LA 门店 - Kitchen" value={site.name} onChange={(e) => setSite({ ...site, name: e.target.value })} />
            <input className="input" placeholder="门店号" value={site.store_no} onChange={(e) => setSite({ ...site, store_no: e.target.value })} />
            <input className="input col-span-2" placeholder="地址" value={site.address} onChange={(e) => setSite({ ...site, address: e.target.value })} />
            <input className="input" placeholder="部门" value={site.dept} onChange={(e) => setSite({ ...site, dept: e.target.value })} />
            <input className="input" placeholder="联系人" value={site.contact} onChange={(e) => setSite({ ...site, contact: e.target.value })} />
          </div>
          <button className="btn-primary w-full" disabled={!site.name} onClick={add}>保存送货点</button>
        </div>
      )}
    </Modal>
  )
}

function NewOrder({ onClose, onCreated }: { onClose: () => void; onCreated: (id: string) => void }) {
  const toast = useToast()
  const custs = useLoad(() => get('/api/orders/customers'), [])
  const master = useLoad(() => get('/api/inv/master'), [])
  const [f, setF] = useState<any>({ customer_id: '', site_id: '', po_no: '', po_date: '', required_date: '', category: '' })
  const [lines, setLines] = useState<any[]>([{ item_no: '', description: '', unit: '箱', qty: '', price: '', sku_id: '' }])
  useEffect(() => { if (custs.data?.length && !f.customer_id) setF((x: any) => ({ ...x, customer_id: custs.data[0].id, site_id: custs.data[0].sites[0]?.id || '' })) }, [custs.data, f.customer_id])
  const cust = (custs.data || []).find((c: any) => c.id === f.customer_id)
  const setLine = (i: number, p: any) => setLines(lines.map((l, j) => (i === j ? { ...l, ...p } : l)))
  const save = async () => {
    try {
      const r = await post('/api/orders', { ...f, site_id: f.site_id || null, po_date: f.po_date || null, required_date: f.required_date || null,
        lines: lines.filter((l) => l.qty && l.description).map((l) => ({ ...l, qty: Number(l.qty), price: Number(l.price) || 0, sku_id: l.sku_id || null })) })
      onCreated(r.id)
    } catch (e) { toast(errMsg(e), 'err') }
  }
  return (
    <Modal open onClose={onClose} wide title="手工录入订单" footer={<button className="btn-primary" disabled={!f.po_no} onClick={save}>保存</button>}>
      <p className="mb-2 text-xs text-slate-500">更方便的方式：把 PO 发给 ChatGPT，让它录入后你在「资料中心 → 批量导入」确认。</p>
      <div className="grid gap-2 sm:grid-cols-3">
        <Field label="客户"><select className="input" value={f.customer_id} onChange={(e) => setF({ ...f, customer_id: e.target.value })}>{(custs.data || []).map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
        <Field label="送货点"><select className="input" value={f.site_id} onChange={(e) => setF({ ...f, site_id: e.target.value })}><option value="">—</option>{(cust?.sites || []).map((s: any) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
        <Field label="PO 号 *"><input className="input" value={f.po_no} onChange={(e) => setF({ ...f, po_no: e.target.value })} /></Field>
        <Field label="下单日期"><input className="input" type="date" value={f.po_date} onChange={(e) => setF({ ...f, po_date: e.target.value })} /></Field>
        <Field label="要求交货"><input className="input" type="date" value={f.required_date} onChange={(e) => setF({ ...f, required_date: e.target.value })} /></Field>
        <Field label="类别"><input className="input" placeholder="General / Freezer / 中央厨房" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })} /></Field>
      </div>
      <div className="mt-3 space-y-2">
        {lines.map((l, i) => (
          <div key={i} className="grid grid-cols-2 gap-2 rounded-lg border border-slate-200 p-2 sm:grid-cols-[1fr_2fr_0.8fr_1fr_1fr_2fr_auto]">
            <input className="input" placeholder="Item#" value={l.item_no} onChange={(e) => setLine(i, { item_no: e.target.value })} />
            <input className="input" placeholder="品名" value={l.description} onChange={(e) => setLine(i, { description: e.target.value })} />
            <select className="input" value={l.unit} onChange={(e) => setLine(i, { unit: e.target.value })}><option value="箱">箱</option><option value="磅">lb</option></select>
            <input className="input" type="number" inputMode="decimal" placeholder="数量" value={l.qty} onChange={(e) => setLine(i, { qty: e.target.value })} />
            <input className="input" type="number" inputMode="decimal" placeholder="单价 $" value={l.price} onChange={(e) => setLine(i, { price: e.target.value })} />
            <select className="input" value={l.sku_id} onChange={(e) => setLine(i, { sku_id: e.target.value })}><option value="">SKU（可后配）</option>{(master.data?.skus || []).map((s: any) => <option key={s.id} value={s.id}>{s.code} {s.name}</option>)}</select>
            <button className="btn-ghost" aria-label="删除行" onClick={() => setLines(lines.filter((_, j) => j !== i))}><Trash2 size={16} /></button>
          </div>
        ))}
        <button className="btn-ghost btn-sm" onClick={() => setLines([...lines, { item_no: '', description: '', unit: '箱', qty: '', price: '', sku_id: '' }])}><Plus size={14} />加一行</button>
      </div>
    </Modal>
  )
}

// ---------------- module ----------------
export function OrdersModule({ preview }: ModProps) {
  const { can } = useAuth()
  const toast = useToast()
  const [sp, setSp] = useSearchParams()
  const [filter, setFilter] = useState('open')
  const [dlg, setDlg] = useState<string | null>(null)
  const q = ({ open: 'open_only=true', shipped: 'status=shipped', delivered: 'status=delivered,closed', all: '' } as any)[filter]
  const list = useLoad(() => (preview ? Promise.resolve([]) : get(`/api/orders?${q}`)), [q, preview])
  const sel = sp.get('so')
  const open = (id: string | null) => { const n = new URLSearchParams(sp); if (id) n.set('so', id); else n.delete('so'); setSp(n, { replace: true }) }
  const scan = async (code: string) => {
    try { const r = await get(`/api/inv/scan/${encodeURIComponent(code)}`); if (r.type === 'so') open(r.order_id); else toast('不是订单码（可扫 SO 号或 PO 号）', 'err') } catch (e) { toast(errMsg(e), 'err') }
  }
  if (preview) return <div className="text-sm text-slate-400">客户订单与发货（预览不加载数据）</div>
  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-2">
        {can('order.manage') && <button className="btn-secondary" onClick={() => setDlg('new')}><Plus size={16} />手工录单</button>}
        <button className="btn-secondary" onClick={() => setDlg('customers')}><Users size={16} />客户与品项</button>
        <ScanButton label="扫 PO / SO" onResult={scan} />
      </div>
      <p className="mb-2 text-xs text-slate-500">收到大统华 PO：把 PDF 或照片发给 ChatGPT，说「录进工作台」，然后在资料中心确认。<Send size={10} className="inline" /></p>
      <Segmented value={filter} onChange={setFilter} items={[{ key: 'open', label: '待发货' }, { key: 'shipped', label: '已发出' }, { key: 'delivered', label: '已送达' }, { key: 'all', label: '全部' }]} />
      <div className="mt-3 space-y-2">
        {(list.data || []).map((o: any) => (
          <button key={o.id} onClick={() => open(o.id)} className={`block w-full rounded-lg border p-3 text-left active:bg-slate-50 sm:hover:bg-slate-50 ${o.overdue ? 'border-red-200' : 'border-slate-200'}`}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono font-semibold">PO {o.po_no}</span>
              <Badge color={ST_COLOR[o.status]}>{o.status_label}</Badge>
              {o.overdue && <Badge color="red"><AlertTriangle size={10} />过交期</Badge>}
              {o.due_soon && <Badge color="yellow">今明要交</Badge>}
              {o.unmapped > 0 && <Badge color="yellow">未配对 {o.unmapped}</Badge>}
              {o.source.startsWith('agent') && <Badge color="purple">AI 录入</Badge>}
              <span className="ml-auto font-semibold">{money(o.total)}</span>
            </div>
            <div className="mt-1 flex flex-wrap gap-x-3 text-xs text-slate-500"><span>{o.customer_code}</span><span>{o.site}</span><span>交货 {o.required_date || '未定'}</span>{o.category && <span>{o.category}</span>}<span>{o.lines_count} 行</span></div>
          </button>
        ))}
        {list.data && !list.data.length && <Empty>没有订单</Empty>}
      </div>
      {dlg === 'new' && <NewOrder onClose={() => setDlg(null)} onCreated={(id) => { setDlg(null); list.reload(); open(id) }} />}
      {dlg === 'customers' && <Customers onClose={() => setDlg(null)} />}
      {sel && <OrderDetail id={sel} onClose={() => open(null)} onChanged={list.reload} />}
    </div>
  )
}
