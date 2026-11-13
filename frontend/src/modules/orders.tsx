import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import QRCode from 'qrcode'
import { AlertTriangle, Download, FileSpreadsheet, Plus, Printer, Scale, Send, Trash2, Truck, Users } from 'lucide-react'
import { get, patch, post, uuid } from '../lib/api'
import { useAuth } from '../lib/auth'
import { dt, num, skuLabel, poLabel } from '../lib/fmt'
import { Badge, Empty, Field, Modal, Segmented, Spinner, errMsg, useLoad, useToast } from '../components/ui'
import { EvidencePanel } from '../components/Evidence'
import { ScanButton } from '../components/Scan'
import { Timeline, type ModProps } from './basic'

const money = (n: any) => (n === null || n === undefined ? '—' : `$${Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`)
const unitLabel = (u: string) => (u === '磅' ? 'lb' : u)
const PO_COLOR: Record<string, string> = { open: 'blue', partial: 'yellow', fulfilled: 'green', closed_balance: 'gray', cancelled: 'gray' }
const SH_COLOR: Record<string, string> = { shipped: 'purple', delivered: 'green', discrepancy: 'red', cancelled: 'gray' }
const POD_COLOR: Record<string, string> = { verified: 'green', pending: 'blue', to_upload: 'yellow', missing: 'red' }
const todayISO = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10)

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
            <Field label="品项（称重品）"><select className="input" value={f.sku_id} onChange={(e) => setF({ ...f, sku_id: e.target.value })}><option value="">选择…</option>{cw.map((s: any) => <option key={s.id} value={s.id}>{skuLabel(s)}</option>)}</select></Field>
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
          <div className="text-right"><div className="text-lg font-bold">PACKING SLIP</div><div>PO No.: <b>{so.po_no || '—'}</b></div><div>{shipment ? shipment.code : so.code}</div><div>Date: {dt(shipment?.shipped_at || new Date().toISOString(), { dateOnly: true })}</div></div>
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
  const [deliveryNo, setDeliveryNo] = useState('')
  const [shippedOn, setShippedOn] = useState(todayISO())
  const [hist, setHist] = useState(false)
  const [histReason, setHistReason] = useState('')
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
      const r = await post(`/api/orders/${so.id}/ship`, { idempotency_key: key, lines, note, over_reason: over, delivery_no: deliveryNo, shipped_on: shippedOn,
        historical: hist, historical_reason: histReason })
      toast(r.duplicate ? '重复提交已拦截' : `已发货 ${r.shipment_code}`); onDone(); onClose()
    } catch (e: any) { toast(errMsg(e), 'err') }
  }
  return (
    <Modal open onClose={onClose} wide title={`发货：${poLabel(so.po_no)}`} footer={<button className="btn-primary" onClick={submit}><Truck size={16} />确认发货</button>}>
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-2">
          <Field label="送货单号（可空）"><input className="input" placeholder="QQUSA20261108" value={deliveryNo} onChange={(e) => setDeliveryNo(e.target.value)} /></Field>
          <Field label="实际发货日期"><input className="input" type="date" value={shippedOn} onChange={(e) => setShippedOn(e.target.value)} /></Field>
        </div>
        <label className={`flex items-start gap-2 rounded-lg border p-2 text-sm ${hist ? 'border-amber-300 bg-amber-50' : 'border-slate-200'}`}>
          <input type="checkbox" className="mt-1" checked={hist} onChange={(e) => { setHist(e.target.checked); if (e.target.checked) setQty(Object.fromEntries(open.filter((l: any) => !l.catch_weight).map((l: any) => [l.id, String(l.remaining)]))) }} />
          <span>历史补录：这批货早就送了，盘点 / 期初录的已经是扣掉它之后的数 → <b>不再扣库存</b>，只记送了什么</span>
        </label>
        {hist && <Field label="为什么不扣库存 *"><input className="input" placeholder="如：11/5 期初 81 箱已是扣掉 11/4 送货后的净结存" value={histReason} onChange={(e) => setHistReason(e.target.value)} /></Field>}
        {open.some((l: any) => l.catch_weight) && <ScanButton label="扫箱标签" onResult={scan} />}
        {open.map((l: any) => {
          const sel = boxSel[l.id] || new Set()
          const w = (l.boxes_in_stock || []).filter((b: any) => sel.has(b.id)).reduce((a: number, b: any) => a + b.weight, 0)
          return (
            <div key={l.id} className="rounded-lg border border-slate-200 p-3 text-sm">
              <div className="font-medium">{l.description} <span className="text-xs font-normal text-slate-500">{l.item_no} · {l.sku_code}</span></div>
              <div className="text-xs text-slate-500">剩余 {num(l.remaining)} {unitLabel(l.order_unit)} · 可用 {num(l.available_base)} {l.base_unit}{l.factor !== 1 && ` · 1 ${unitLabel(l.order_unit)} = ${l.factor} ${l.base_unit}，发 ${num(Number(qty[l.id] || 0))} 扣 ${num(Number(qty[l.id] || 0) * l.factor)} ${l.base_unit}`}{l.short && !hist && <span className="ml-1 text-red-600">库存不够</span>}</div>
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
  const [on, setOn] = useState(shipment.shipped_on || todayISO())
  const [by, setBy] = useState('')
  const [pending, setPending] = useState(false)
  const lines = so.lines.filter((l: any) => shipment.lines.some((x: any) => x.line_id === l.id))
  const go = async () => {
    try {
      await post(`/api/orders/shipments/${shipment.id}/deliver`, { exception_reason: exc, delivered_on: on, received_by: by, pod_pending: pending,
        rejected: Object.entries(rej).filter(([, v]) => Number(v.qty) > 0).map(([line_id, v]) => ({ line_id, qty: Number(v.qty), reason: v.reason })) })
      onDone(); onClose()
    } catch (e) { toast(errMsg(e), 'err') }
  }
  return (
    <Modal open onClose={onClose} title={`确认本次送货完成：${shipment.delivery_no || shipment.code}`} footer={<button className="btn-primary" onClick={go}>确认本次送货完成</button>}>
      <div className="space-y-3">
        <p className="text-xs text-slate-500">只确认这一趟送货。PO 剩下没发的不影响；库存发货时已扣，这里不会再扣。</p>
        <div className="grid grid-cols-2 gap-2">
          <Field label="实际送达日期"><input className="input" type="date" value={on} onChange={(e) => setOn(e.target.value)} /></Field>
          <Field label="签收人 / 签收情况"><input className="input" placeholder="如：门店收货员签字" value={by} onChange={(e) => setBy(e.target.value)} /></Field>
        </div>
        <EvidencePanel targetType="shipment" targetId={shipment.id} purposes={['签收单', 'POD', '送货照片']} compact />
        {!shipment.pod && (
          <label className="flex items-start gap-2 rounded-lg border border-slate-200 p-2 text-sm">
            <input type="checkbox" className="mt-1" checked={pending} onChange={(e) => setPending(e.target.checked)} />
            <span>客户已签收，签收单原件<b>待补</b>（会标「原件待补」，不算已核验；记得补传）</span>
          </label>
        )}
        {!shipment.pod && !pending && <Field label="确实拿不到签收单？写原因（会留例外标签）"><input className="input" value={exc} onChange={(e) => setExc(e.target.value)} /></Field>}
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

const BAL = [['awaiting_new_po', '等客户新 PO（旧 PO 余额关闭）'], ['backordered', '补货中（仍欠，到货再发）'], ['cancelled_by_customer', '客户取消'], ['waived', '同意关闭剩余']]

/** 处置 PO 未履约的部分：只能由人操作；原订量、金额、已送部分和签收凭证都不变。 */
function BalanceDialog({ so, onClose, onDone }: any) {
  const toast = useToast()
  const open = so.lines.filter((l: any) => l.qty_ordered - l.qty_shipped > 1e-9)
  const [sel, setSel] = useState<Set<string>>(new Set(open.filter((l: any) => l.remaining > 1e-9).map((l: any) => l.id)))
  const [disp, setDisp] = useState('awaiting_new_po')
  const [reason, setReason] = useState('')
  const [ref, setRef] = useState('')
  const go = async (d = disp) => {
    try {
      await post(`/api/orders/${so.id}/balance`, { disposition: d, reason, customer_ref: ref, line_ids: [...sel] })
      toast(d ? '已处置' : '已撤销处置'); onDone(); onClose()
    } catch (e) { toast(errMsg(e), 'err') }
  }
  return (
    <Modal open onClose={onClose} title="处理未履约余额" footer={<button className="btn-primary" disabled={!sel.size || !reason.trim()} onClick={() => go()}>确认</button>}>
      <div className="space-y-3">
        <p className="text-xs text-slate-500">PO 剩下没发的怎么处理。已送的货、签收凭证、原 PO 数量金额都不变；送货单照样是「已签收完成」。</p>
        <div className="space-y-1">
          {open.map((l: any) => (
            <label key={l.id} className="flex items-center gap-2 rounded border border-slate-200 p-2 text-sm">
              <input type="checkbox" checked={sel.has(l.id)} onChange={() => { const n = new Set(sel); n.has(l.id) ? n.delete(l.id) : n.add(l.id); setSel(n) }} />
              <span className="min-w-0 flex-1 truncate">{l.description}</span>
              <span className="shrink-0 text-xs text-slate-500">未发 {num(l.qty_ordered - l.qty_shipped)} {unitLabel(l.order_unit)}{l.balance_status && ` · ${l.balance_label}`}</span>
            </label>
          ))}
        </div>
        <div className="grid gap-1.5">
          {BAL.map(([k, label]) => (
            <button key={k} type="button" aria-pressed={disp === k} onClick={() => setDisp(k)}
              className={`min-h-11 rounded-lg border px-3 text-left text-sm ${disp === k ? 'border-brand-600 bg-brand-50 font-semibold' : 'border-slate-200'}`}>{label}</button>
          ))}
        </div>
        <Field label="原因 *"><input className="input" placeholder="如：T&T 要求剩余商品重新开 PO" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
        <Field label="客户确认依据（邮件 / 电话 / 新 PO 号）"><input className="input" value={ref} onChange={(e) => setRef(e.target.value)} /></Field>
        {open.some((l: any) => l.balance_status) && <button className="btn-ghost btn-sm" onClick={() => go('')}>撤销已选行的处置（恢复为还欠着）</button>}
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
      <select className="input min-w-0 flex-1" value={id} onChange={(e) => setId(e.target.value)}><option value="">选我们的 SKU…</option>{(master.data?.skus || []).map((s: any) => <option key={s.id} value={s.id}>{skuLabel(s)}</option>)}</select>
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
  const fillPo = async () => {
    const v = prompt('大统华 PO 号（PO 原件发给 ChatGPT 录入时，会自动补 Item# 与单价）')
    if (!v?.trim()) return
    try { await patch(`/api/orders/${id}`, { version: so.version, po_no: v.trim() }); reload() } catch (e) { toast(errMsg(e), 'err') }
  }
  const openish = ['confirmed', 'partial'].includes(so.status)
  const hasBalance = so.lines.some((l: any) => l.remaining > 1e-9 || l.balance_status)
  const editShipment = async (s: any) => {
    const sh = prompt('实际发货日期（YYYY-MM-DD）', s.shipped_on || '')
    if (sh === null) return
    const dn = prompt('送货单号', s.delivery_no || '') ?? s.delivery_no
    const dv = s.status !== 'shipped' ? (prompt('实际送达日期（YYYY-MM-DD）', s.delivered_on || '') ?? s.delivered_on) : undefined
    const reason = prompt('更正原因（如：补录的历史送货，实际 11/4 发货送达）') || ''
    try { await patch(`/api/orders/shipments/${s.id}`, { shipped_on: sh, delivery_no: dn, delivered_on: dv, reason }); reload() } catch (e) { toast(errMsg(e), 'err') }
  }
  const changeFactor = async (l: any) => {
    const v = prompt(`换算：大统华 1 ${unitLabel(l.order_unit)} = 我们多少 ${l.base_unit}？（卤水料包：大统华 1 单位 10 包，我们 1 箱 20 包 → 0.5）`, String(l.factor))
    if (!v) return
    const reason = prompt('原因（可空）') || ''
    try { await post(`/api/orders/lines/${l.id}/factor`, { factor: Number(v), reason }); reload() } catch (e) { toast(errMsg(e), 'err') }
  }
  return (
    <Modal open onClose={onClose} wide title={<span className="flex flex-wrap items-center gap-2">{poLabel(so.po_no)}<Badge color={PO_COLOR[so.po_status]}>PO {so.po_status_label}</Badge>{so.latest_delivery && <Badge color={SH_COLOR[so.latest_delivery.status]}>最近送货 {so.latest_delivery.label}</Badge>}{so.overdue && <Badge color="red">已过交期</Badge>}{so.due_soon && <Badge color="yellow">即将到期</Badge>}</span>}>
      <div className="space-y-4 pb-16 sm:pb-0">
        <dl className="kv">
          <dt>客户</dt><dd>{so.customer}{so.vendor_no && <span className="text-xs text-slate-400"> · 供应商号 {so.vendor_no}</span>}</dd>
          <dt>送货点</dt><dd>{so.site || <span className="text-amber-700">未指定</span>}</dd>
          <dt>交货</dt><dd className={so.overdue ? 'font-semibold text-red-600' : ''}>{so.required_date || '未定'} {so.can.manage && openish && <button className="btn-ghost btn-sm text-brand-700" onClick={changeDate}>修改</button>}</dd>
          <dt>PO</dt><dd>{so.po_no ? <span className="font-mono">{so.po_no}</span> : <span className="text-amber-700">待补（先送货的单）</span>} {so.can.manage && !so.po_no && <button className="btn-ghost btn-sm text-brand-700" onClick={fillPo}>补 PO 号</button>}</dd>
          <dt>下单</dt><dd>{so.po_date || '—'}{so.category && ` · ${so.category}`} · {so.code}</dd>
          <dt>金额</dt><dd><b>{money(so.total)}</b></dd>
          {so.note && <><dt>备注</dt><dd className="whitespace-pre-wrap">{so.note}</dd></>}
        </dl>
        <div className="rounded-lg border border-slate-200 p-3" aria-label="PO 履约汇总">
          <div className="mb-2 flex items-center gap-2 text-sm font-semibold">PO 履约汇总 <Badge color={PO_COLOR[so.po_status]}>{so.po_status_label}</Badge>{so.backordered && <Badge color="yellow">有补货中</Badge>}</div>
          <div className="grid grid-cols-2 gap-1.5 text-center text-xs sm:grid-cols-5">
            {[['原 PO 总额', so.total, ''], ['已发', so.shipped_amount, ''], ['已签收', so.delivered_amount, 'text-emerald-700'], ['还欠', so.remaining_amount, so.remaining_amount > 0 ? 'text-amber-700' : ''], ['余额已处置', so.closed_amount, 'text-slate-500']].map(([k, v, c]: any) => (
              <div key={k} className="rounded bg-slate-50 py-1.5"><div className="text-slate-400">{k}</div><div className={`font-semibold ${c}`}>{money(v)}</div></div>
            ))}
          </div>
        </div>
        {so.exceptions.length > 0 && <div className="rounded-lg border border-violet-200 bg-violet-50 p-2 text-xs text-violet-900">{so.exceptions.map((e: any, i: number) => <div key={i}>{e.step}：{e.reason} — {e.by_name} · {dt(e.at)}</div>)}</div>}
        <div className="space-y-2">
          {so.lines.map((l: any) => (
            <div key={l.id} className={`rounded-lg border p-3 text-sm ${!l.sku_id ? 'border-amber-300 bg-amber-50/40' : l.short ? 'border-red-200' : 'border-slate-200'}`}>
              <div className="flex flex-wrap items-baseline gap-x-2"><span className="font-medium">{l.description}</span><span className="text-xs text-slate-400">{l.item_no && `#${l.item_no} · `}{l.pack_size} · {money(l.price)}/{unitLabel(l.order_unit)}</span><span className="ml-auto font-semibold">{money(l.amount)}</span></div>
              <div className="mt-1 grid grid-cols-4 gap-1 text-center text-xs">
                {[['订', l.qty_ordered], ['已发', l.qty_shipped], ['已签收', l.qty_delivered], ['还欠', l.remaining]].map(([k, v]: any) => (
                  <div key={k} className="rounded bg-slate-50 py-1"><div className="text-slate-400">{k}</div><div className={`font-semibold ${k === '还欠' && v > 0 ? 'text-amber-700' : ''}`}>{num(v)}</div></div>
                ))}
              </div>
              {(l.qty_rejected > 0 || l.balance_status) && <div className="mt-1 flex flex-wrap gap-1.5 text-xs">
                {l.qty_rejected > 0 && <Badge color="red">拒收 {num(l.qty_rejected)}</Badge>}
                {l.balance_status && <Badge color={l.balance_status === 'backordered' ? 'yellow' : 'gray'}>余额：{l.balance_label}{l.qty_closed > 0 && ` ${num(l.qty_closed)}`}</Badge>}
                {l.balance_reason && <span className="text-slate-500">{l.balance_reason}{l.balance_ref && `（依据：${l.balance_ref}）`}</span>}
              </div>}
              {l.sku_id ? <div className="mt-1 text-xs text-slate-500">→ {l.sku_code} {l.sku_name} · 1 {unitLabel(l.order_unit)} = {l.factor} {l.base_unit}{so.can.manage && l.remaining > 0 && <button className="btn-ghost btn-sm text-brand-700" onClick={() => changeFactor(l)}>改换算</button>}{l.catch_weight && ' · 称重'} · 可用 {num(l.available_base)} {l.base_unit}{l.short && <span className="ml-1 font-semibold text-red-600">库存不够</span>}{l.catch_weight && ` · 在库 ${l.boxes_in_stock?.length || 0} 箱`}{l.boxes_shipped > 0 && ` · 已发 ${l.boxes_shipped} 箱`}</div>
                : so.can.manage ? <><div className="mt-1 text-xs text-amber-700">还没对上我们的 SKU</div><SkuPicker onPick={(sid, f) => map(l.id, sid, f)} /></> : <div className="text-xs text-amber-700">未配对 SKU</div>}
              {l.reject_reason && <div className="mt-1 text-xs text-red-600">拒收：{l.reject_reason}</div>}
            </div>
          ))}
        </div>
        {so.shipments.length > 0 && (
          <div>
            <div className="mb-2 text-sm font-semibold">送货记录（每一趟单独完成）</div>
            {so.shipments.map((s: any) => (
              <div key={s.id} className="mb-2 rounded-lg border border-slate-200 p-3 text-sm" aria-label={`送货 ${s.code}`}>
                <div className="flex flex-wrap items-center gap-2"><span className="font-mono font-semibold">{s.delivery_no || s.code}</span>{s.delivery_no && <span className="font-mono text-xs text-slate-400">{s.code}</span>}
                  <Badge color={SH_COLOR[s.status]}>{s.status_label}</Badge><Badge color={POD_COLOR[s.pod_status]}>{s.pod_label}</Badge><span className="ml-auto font-semibold">{money(s.total)}</span></div>
                <div className="mt-0.5 text-xs text-slate-500">发货 {s.shipped_on || '—'}{s.delivered_on && ` · 送达 ${s.delivered_on}`}{s.received_by && ` · ${s.received_by}`} · <span className={s.inventory_effect === 'issued' ? '' : 'text-amber-700'}>{s.inventory_label}</span></div>
                <div className="mt-1 space-y-0.5 text-xs text-slate-700">{s.lines.map((x: any, i: number) => <div key={i}>{x.description} <b>{num(x.qty)} {unitLabel(x.order_unit)}</b>{x.base_qty != null && x.base_qty !== x.qty && <span className="text-slate-400">（扣 {num(x.base_qty)}）</span>}{x.boxes?.length ? `（${x.boxes.length} 箱）` : ''} <span className="text-slate-400">{x.lots?.join(',') || ''} · {money(x.amount)}</span></div>)}</div>
                <div className="mt-1 text-[11px] text-slate-400">录入 {s.shipped_by} · {dt(s.shipped_at)}</div>
                <div className="mt-1 flex flex-wrap gap-2">
                  <button className="btn-ghost btn-sm" onClick={() => setDlg({ t: 'slip', s })}><Printer size={14} />发货单</button>
                  {s.status === 'shipped' && so.can.ship && <button className="btn-secondary btn-sm" onClick={() => setDlg({ t: 'deliver', s })}>确认本次送货完成</button>}
                  <button className="btn-ghost btn-sm" onClick={() => setDlg({ t: 'pod', s })}>签收原件</button>
                  {so.can.ship && <button className="btn-ghost btn-sm" onClick={() => editShipment(s)}>更正日期 / 单号</button>}
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
        {so.can.manage && so.shipped_amount > 0 && <button className="btn-secondary" onClick={() => setDlg({ t: 'qb' })}><FileSpreadsheet size={16} />QuickBooks 发票</button>}
        {so.can.manage && hasBalance && so.status !== 'cancelled' && <button className="btn-secondary" onClick={() => setDlg({ t: 'balance' })}>处理未履约余额</button>}
        {openish && so.can.manage && !so.shipments.length && <button className="btn-ghost text-red-600" onClick={() => { const r = prompt('取消原因'); if (r) act('cancel', { reason: r }) }}>取消订单</button>}
      </div>
      {dlg?.t === 'ship' && <ShipDialog so={so} onClose={() => setDlg(null)} onDone={reload} />}
      {dlg?.t === 'slip' && <PackingSlip so={so} shipment={dlg.s} onClose={() => setDlg(null)} />}
      {dlg?.t === 'qb' && <QuickBooksDialog so={so} onClose={() => setDlg(null)} />}
      {dlg?.t === 'deliver' && <DeliverDialog so={so} shipment={dlg.s} onClose={() => setDlg(null)} onDone={reload} />}
      {dlg?.t === 'balance' && <BalanceDialog so={so} onClose={() => setDlg(null)} onDone={reload} />}
      {dlg?.t === 'pod' && <Modal open onClose={() => { setDlg(null); reload() }} title={`签收原件：${dlg.s.delivery_no || dlg.s.code}`}><EvidencePanel targetType="shipment" targetId={dlg.s.id} purposes={['签收单', 'POD', '送货照片']} onChange={reload} /></Modal>}
    </Modal>
  )
}

// ---------------- QuickBooks 发票导出 ----------------
const laToday = () => new Date().toLocaleDateString('en-CA', { timeZone: 'America/Los_Angeles' })

function QuickBooksDialog({ so, onClose }: { so: any; onClose: () => void }) {
  const toast = useToast()
  const others = useLoad(() => get('/api/orders?status=partial,shipped,delivered,closed'), [])
  const [picked, setPicked] = useState<Set<string>>(new Set([so.id]))
  const [f, setF] = useState({ invoice_no: `QQUSA${laToday().replaceAll('-', '')}`, invoice_date: laToday(), terms: 'Net 30', customer_name: so.customer_accounting_name, basis: 'shipped' })
  const [res, setRes] = useState<any>(null)
  const same = (others.data || []).filter((o: any) => o.customer_id === so.customer_id && o.shipped_amount > 0).slice(0, 30)
  const toggle = (id: string) => { const n = new Set(picked); n.has(id) ? n.delete(id) : n.add(id); setPicked(n); setRes(null) }
  const go = async () => { try { setRes(await post('/api/orders/quickbooks', { ...f, order_ids: [...picked] })) } catch (e) { toast(errMsg(e), 'err') } }
  const download = () => {
    const url = URL.createObjectURL(new Blob([res.csv], { type: 'text/csv;charset=utf-8' }))
    const a = document.createElement('a')
    a.href = url; a.download = res.filename; document.body.appendChild(a); a.click(); a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 2000)
  }
  return (
    <Modal open onClose={onClose} title="导出 QuickBooks 发票" footer={res
      ? <button className="btn-primary" onClick={download}><Download size={16} />下载 {res.filename}</button>
      : <button className="btn-primary" disabled={!picked.size || !f.invoice_no.trim()} onClick={go}>生成</button>}>
      <div className="space-y-3 text-sm">
        <p className="text-xs text-slate-500">生成 QuickBooks Online「导入发票」用的 CSV：商品用 QuickBooks 品名（在库存页「商品」里设），描述里带客户的 Item#。导入：QuickBooks ⚙ 设置 → 导入数据 → 发票。</p>
        <div>
          <div className="mb-1 font-medium">合并到这张发票的订单（同一客户）</div>
          <div className="max-h-48 space-y-1 overflow-y-auto">
            {same.map((o: any) => (
              <label key={o.id} className="flex min-h-11 items-center gap-2 rounded border border-slate-200 px-2">
                <input type="checkbox" checked={picked.has(o.id)} onChange={() => toggle(o.id)} />
                <span className="font-mono">{poLabel(o.po_no)}</span><span className="min-w-0 flex-1 truncate text-xs text-slate-500">{o.site} · {o.status_label}</span><span className="text-xs">${num(o.shipped_amount)}</span>
              </label>
            ))}
            {!others.data && <Spinner />}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <Field label="发票号"><input className="input" value={f.invoice_no} onChange={(e) => { setF({ ...f, invoice_no: e.target.value }); setRes(null) }} /></Field>
          <Field label="发票日期"><input className="input" type="date" value={f.invoice_date} onChange={(e) => { setF({ ...f, invoice_date: e.target.value }); setRes(null) }} /></Field>
          <Field label="账期"><input className="input" value={f.terms} onChange={(e) => { setF({ ...f, terms: e.target.value }); setRes(null) }} /></Field>
          <Field label="QuickBooks 客户名"><input className="input" value={f.customer_name} onChange={(e) => { setF({ ...f, customer_name: e.target.value }); setRes(null) }} /></Field>
        </div>
        <Segmented value={f.basis} onChange={(v: string) => { setF({ ...f, basis: v }); setRes(null) }} items={[{ key: 'shipped', label: '按实发（扣拒收）' }, { key: 'ordered', label: '按订量' }]} />
        {res && (
          <div className="rounded-lg bg-emerald-50 p-3 text-emerald-900">
            <div className="font-medium">{res.rows} 行 · 合计 ${num(res.total)}</div>
            <div className="text-xs">{res.customer} · {res.invoice_date} · 到期 {res.due_date}</div>
            {res.warnings.map((w: string, i: number) => <div key={i} className="mt-1 text-xs text-amber-800">{w}</div>)}
          </div>
        )}
      </div>
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
    <Modal open onClose={onClose} wide title="手工录入订单" footer={<button className="btn-primary" disabled={!f.customer_id} onClick={save}>保存</button>}>
      <p className="mb-2 text-xs text-slate-500">更方便的方式：把 PO 发给 ChatGPT，让它录入后你在「资料中心 → 批量导入」确认。</p>
      <div className="grid gap-2 sm:grid-cols-3">
        <Field label="客户"><select className="input" value={f.customer_id} onChange={(e) => setF({ ...f, customer_id: e.target.value })}>{(custs.data || []).map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
        <Field label="送货点"><select className="input" value={f.site_id} onChange={(e) => setF({ ...f, site_id: e.target.value })}><option value="">—</option>{(cust?.sites || []).map((s: any) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
        <Field label="PO 号（还没有可空着，之后补）"><input className="input" value={f.po_no} onChange={(e) => setF({ ...f, po_no: e.target.value })} /></Field>
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
            <select className="input" value={l.sku_id} onChange={(e) => setLine(i, { sku_id: e.target.value })}><option value="">SKU（可后配）</option>{(master.data?.skus || []).map((s: any) => <option key={s.id} value={s.id}>{skuLabel(s)}</option>)}</select>
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
      <Segmented value={filter} onChange={setFilter} items={[{ key: 'open', label: '还欠货' }, { key: 'shipped', label: '已发出' }, { key: 'delivered', label: '已完成 / 结案' }, { key: 'all', label: '全部' }]} />
      <div className="mt-3 space-y-2">
        {(list.data || []).map((o: any) => (
          <button key={o.id} onClick={() => open(o.id)} className={`block w-full rounded-lg border p-3 text-left active:bg-slate-50 sm:hover:bg-slate-50 ${o.overdue ? 'border-red-200' : 'border-slate-200'}`}>
            <div className="flex flex-wrap items-center gap-2">
              <span className={`font-mono font-semibold ${o.po_no ? '' : 'text-amber-700'}`}>{poLabel(o.po_no)}</span>
              <Badge color={PO_COLOR[o.po_status]}>PO {o.po_status_label}</Badge>
              {o.latest_delivery && <Badge color={SH_COLOR[o.latest_delivery.status]}>最近送货 {o.latest_delivery.label}</Badge>}
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
