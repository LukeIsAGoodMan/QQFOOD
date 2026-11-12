import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AlertTriangle, Check, FileSignature, PackageCheck, Plus, Receipt, Send, ShoppingCart, Trash2, X } from 'lucide-react'
import { get, patch, post, put } from '../lib/api'
import { useAuth } from '../lib/auth'
import { dt, num, skuLabel } from '../lib/fmt'
import { Badge, Empty, Field, Modal, Segmented, Spinner, errMsg, useLoad, useToast } from '../components/ui'
import { EvidencePanel, PickButtons, UploadQueue, useUploader } from '../components/Evidence'
import { ScanButton } from '../components/Scan'
import { Timeline, type ModProps } from './basic'

const money = (n: any) => (n === null || n === undefined ? '—' : `$${Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`)
const ST_COLOR: Record<string, string> = { draft: 'gray', pending_approval: 'yellow', approved: 'blue', rejected: 'red', ordered: 'blue', partial: 'yellow', received: 'green', closed: 'gray', cancelled: 'gray' }
const PAY_COLOR: Record<string, string> = { none: 'gray', invoiced: 'blue', paid_pending: 'yellow', reconciled: 'green' }
const CATS = ['原料包材', '耗材', '设备', '日常用品']
const SIGNOFF = ['签收单', '收据', '到货照片', '发货单']

// ---------------- new PO ----------------
type LineDraft = { sku_id: string; item_name: string; unit: string; qty_ordered: string; unit_price: string }
const emptyLine = (): LineDraft => ({ sku_id: '', item_name: '', unit: '', qty_ordered: '', unit_price: '' })

function NewPO({ onClose, onCreated, limit }: { onClose: () => void; onCreated: (id: string) => void; limit: number }) {
  const toast = useToast()
  const master = useLoad(() => get('/api/inv/master'), [])
  const [f, setF] = useState<any>({ supplier: '', category: '原料包材', warehouse_id: '', expected_date: '', tax: '', freight: '', install_fee: '', note: '' })
  const [lines, setLines] = useState<LineDraft[]>([emptyLine()])
  if (!master.data) return null
  const skus = master.data.skus
  const goods = lines.reduce((s, l) => s + (Number(l.qty_ordered) || 0) * (Number(l.unit_price) || 0), 0)
  const total = goods + (Number(f.tax) || 0) + (Number(f.freight) || 0) + (Number(f.install_fee) || 0)
  const setLine = (i: number, p: Partial<LineDraft>) => setLines(lines.map((l, j) => (i === j ? { ...l, ...p } : l)))
  const save = async () => {
    try {
      const r = await post('/api/purchasing/pos', {
        ...f, warehouse_id: f.warehouse_id || null, expected_date: f.expected_date || null,
        tax: Number(f.tax) || 0, freight: Number(f.freight) || 0, install_fee: Number(f.install_fee) || 0,
        lines: lines.filter((l) => l.qty_ordered).map((l) => ({ sku_id: l.sku_id || null, item_name: l.item_name || null, unit: l.unit || null, qty_ordered: Number(l.qty_ordered), unit_price: Number(l.unit_price) || 0 })),
      })
      toast(`已创建 ${r.code}（草稿）`); onCreated(r.id)
    } catch (e) { toast(errMsg(e), 'err') }
  }
  return (
    <Modal open onClose={onClose} wide title="新建采购单" footer={<button className="btn-primary" disabled={!f.supplier} onClick={save}>保存草稿</button>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="供应商 *"><input className="input" value={f.supplier} onChange={(e) => setF({ ...f, supplier: e.target.value })} /></Field>
        <Field label="分类"><select className="input" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>{CATS.map((c) => <option key={c}>{c}</option>)}</select></Field>
        <Field label="收货仓库"><select className="input" value={f.warehouse_id} onChange={(e) => setF({ ...f, warehouse_id: e.target.value })}><option value="">默认（597 主场地）</option>{master.data.warehouses.filter((w: any) => w.tracked).map((w: any) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></Field>
        <Field label="预计到货"><input className="input" type="date" value={f.expected_date} onChange={(e) => setF({ ...f, expected_date: e.target.value })} /></Field>
      </div>
      <div className="mt-4 text-sm font-semibold">采购行</div>
      <div className="mt-2 space-y-2">
        {lines.map((l, i) => (
          <div key={i} className="grid grid-cols-2 gap-2 rounded-lg border border-slate-200 p-2 sm:grid-cols-[2fr_1.5fr_1fr_1fr_auto]">
            <select className="input col-span-2 sm:col-span-1" value={l.sku_id} onChange={(e) => setLine(i, { sku_id: e.target.value })}>
              <option value="">非库存品（设备/服务）</option>
              {skus.map((s: any) => <option key={s.id} value={s.id}>{skuLabel(s)}</option>)}
            </select>
            {l.sku_id ? <div className="col-span-2 flex items-center text-xs text-slate-500 sm:col-span-1">收货后自动入库</div>
              : <input className="input col-span-2 sm:col-span-1" placeholder="品名，如 研磨机" value={l.item_name} onChange={(e) => setLine(i, { item_name: e.target.value })} />}
            <label><span className="label sm:hidden">数量</span><input className="input" type="number" inputMode="decimal" placeholder="数量" value={l.qty_ordered} onChange={(e) => setLine(i, { qty_ordered: e.target.value })} /></label>
            <label><span className="label sm:hidden">单价 $</span><input className="input" type="number" inputMode="decimal" placeholder="单价 $" value={l.unit_price} onChange={(e) => setLine(i, { unit_price: e.target.value })} /></label>
            <button className="btn-ghost col-span-2 sm:col-span-1" aria-label="删除行" onClick={() => setLines(lines.filter((_, j) => j !== i))}><Trash2 size={16} /></button>
          </div>
        ))}
        <button className="btn-ghost btn-sm" onClick={() => setLines([...lines, emptyLine()])}><Plus size={14} />加一行</button>
      </div>
      <div className="mt-3 grid grid-cols-3 gap-2">
        <Field label="税 $"><input className="input" type="number" inputMode="decimal" value={f.tax} onChange={(e) => setF({ ...f, tax: e.target.value })} /></Field>
        <Field label="运费 $"><input className="input" type="number" inputMode="decimal" value={f.freight} onChange={(e) => setF({ ...f, freight: e.target.value })} /></Field>
        <Field label="安装费 $"><input className="input" type="number" inputMode="decimal" value={f.install_fee} onChange={(e) => setF({ ...f, install_fee: e.target.value })} /></Field>
      </div>
      <Field label="备注"><input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
      <div className="mt-3 rounded-lg bg-slate-50 p-3 text-sm">合计 <b className="text-lg">{money(total)}</b>
        {total > limit && <span className="ml-2 text-amber-700">超过审批额度 {money(limit)}，提交后需老板批准</span>}</div>
    </Modal>
  )
}

// ---------------- quick purchase (already bought) ----------------
function QuickPurchase({ limit, onClose, onDone, onOpen }: { limit: number; onClose: () => void; onDone: () => void; onOpen: (id: string) => void }) {
  const toast = useToast()
  const master = useLoad(() => get('/api/inv/master'), [])
  const [f, setF] = useState<any>({ supplier: '', category: '日常用品', tax: '', paid: true, doc_type: 'receipt', doc_no: '', reason: '', note: '' })
  const [lines, setLines] = useState<any[]>([{ sku_id: '', item_name: '', unit: '件', qty: '', unit_price: '' }])
  const [done, setDone] = useState<any>(null)
  const [shots, setShots] = useState(0)
  const extra = useCallback(() => ({ target_type: 'po', target_id: done?.id || '', purpose: f.doc_type === 'invoice' ? '发票' : '收据' }), [done, f.doc_type])
  const up = useUploader(extra, () => setShots((n) => n + 1))
  const total = lines.reduce((s, l) => s + (Number(l.qty) || 0) * (Number(l.unit_price) || 0), 0) + (Number(f.tax) || 0)
  const setLine = (i: number, p: any) => setLines(lines.map((l, j) => (i === j ? { ...l, ...p } : l)))
  const save = async () => {
    try {
      const r = await post('/api/purchasing/pos/quick', { ...f, tax: Number(f.tax) || 0, lines: lines.filter((l) => l.qty).map((l) => ({ sku_id: l.sku_id || null, item_name: l.item_name || null, unit: l.unit || null, qty: Number(l.qty), unit_price: Number(l.unit_price) || 0 })) })
      setDone(r); onDone()
      toast(r.needs_approval ? `已补录 ${r.code}，超过额度，已提醒老板事后审批` : `已补录 ${r.code}`)
    } catch (e) { toast(errMsg(e), 'err') }
  }
  return (
    <Modal open onClose={onClose} wide title="补录已完成采购（日常用品、门店现买、紧急采购）"
      footer={done ? <button className="btn-primary" onClick={() => onOpen(done.id)}>{shots ? '完成，查看采购单' : '稍后再传，查看采购单'}</button>
        : <button className="btn-primary" disabled={!f.supplier || (f.doc_type === 'none' && !f.reason)} onClick={save}>保存</button>}>
      {done ? (
        <div className="space-y-3">
          <div className="rounded bg-emerald-50 p-3 text-sm text-emerald-700">已补录 {done.code}，合计 {money(done.total)}。请拍{f.doc_type === 'invoice' ? '发票' : '收据'}照片。</div>
          {f.doc_type !== 'none' && <><PickButtons onFiles={up.add} /><UploadQueue queue={up.queue} onRetry={up.retry} /></>}
          {shots > 0 && <div className="text-sm text-emerald-700">已上传 {shots} 张</div>}
        </div>
      ) : (
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="在哪买的 *"><input className="input" placeholder="如 Costco、Home Depot" value={f.supplier} onChange={(e) => setF({ ...f, supplier: e.target.value })} /></Field>
            <Field label="分类"><select className="input" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>{CATS.map((c) => <option key={c}>{c}</option>)}</select></Field>
          </div>
          {lines.map((l, i) => (
            <div key={i} className="grid grid-cols-2 gap-2 rounded-lg border border-slate-200 p-2 sm:grid-cols-[1.5fr_2fr_1fr_1fr]">
              <select className="input col-span-2 sm:col-span-1" value={l.sku_id} onChange={(e) => setLine(i, { sku_id: e.target.value })}>
                <option value="">不入库存</option>{(master.data?.skus || []).map((s: any) => <option key={s.id} value={s.id}>{skuLabel(s)}</option>)}
              </select>
              <input className="input col-span-2 sm:col-span-1" placeholder={l.sku_id ? '（品名取自 SKU）' : '品名，如 垃圾袋'} disabled={!!l.sku_id} value={l.item_name} onChange={(e) => setLine(i, { item_name: e.target.value })} />
              <label><span className="label">数量</span><input className="input" type="number" inputMode="decimal" placeholder="数量" value={l.qty} onChange={(e) => setLine(i, { qty: e.target.value })} /></label>
              <label><span className="label">单价 $</span><input className="input" type="number" inputMode="decimal" placeholder="单价 $" value={l.unit_price} onChange={(e) => setLine(i, { unit_price: e.target.value })} /></label>
            </div>
          ))}
          <button className="btn-ghost btn-sm" onClick={() => setLines([...lines, { sku_id: '', item_name: '', unit: '件', qty: '', unit_price: '' }])}><Plus size={14} />加一行</button>
          <div className="grid grid-cols-2 gap-2">
            <Field label="税 $"><input className="input" type="number" inputMode="decimal" value={f.tax} onChange={(e) => setF({ ...f, tax: e.target.value })} /></Field>
            <Field label="付款"><select className="input" value={f.paid ? '1' : '0'} onChange={(e) => setF({ ...f, paid: e.target.value === '1' })}><option value="1">已付款</option><option value="0">未付款（挂账）</option></select></Field>
          </div>
          <Field label="票据"><Segmented value={f.doc_type} onChange={(k) => setF({ ...f, doc_type: k })} items={[{ key: 'receipt', label: '收据' }, { key: 'invoice', label: '发票' }, { key: 'none', label: '无票据' }]} /></Field>
          {f.doc_type === 'none' && <input className="input" placeholder="没有票据的原因 *（会留例外标签）" value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} />}
          <div className="rounded-lg bg-slate-50 p-3 text-sm">合计 <b className="text-lg">{money(total)}</b>
            {total > limit ? <span className="ml-2 text-amber-700">超过审批额度 {money(limit)}：照常记录，老板会收到事后审批</span> : <span className="ml-2 text-slate-500">额度内，无需审批</span>}</div>
          <p className="text-xs text-slate-400">货已到手：选了 SKU 的行会直接入库；保存后拍收据即可。</p>
        </div>
      )}
    </Modal>
  )
}

// ---------------- receive ----------------
function ReceiveDialog({ po, onClose, onDone }: { po: any; onClose: () => void; onDone: () => void }) {
  const toast = useToast()
  const { can } = useAuth()
  const open = po.lines.filter((l: any) => l.open_qty > 0)
  const [rows, setRows] = useState<Record<string, any>>(Object.fromEntries(open.map((l: any) => [l.id, { qty: '', lot_code: '', expiry: '', counted: true, rejected: '', reject_reason: '' }])))
  const [over, setOver] = useState('')
  const [rc, setRc] = useState<any>(null)
  const [signed, setSigned] = useState(0)
  const [proofPurpose, setProofPurpose] = useState('签收单')
  const [excReason, setExcReason] = useState('')
  const extra = useCallback(() => ({ target_type: 'receipt', target_id: rc?.id || '', purpose: proofPurpose }), [rc, proofPurpose])
  const up = useUploader(extra, () => setSigned((n) => n + 1))
  const set = (id: string, p: any) => setRows({ ...rows, [id]: { ...rows[id], ...p } })
  const exceeds = open.some((l: any) => Number(rows[l.id]?.qty) > l.open_qty)
  const submit = async () => {
    try {
      const lines = open.map((l: any) => ({ po_line_id: l.id, qty: Number(rows[l.id].qty) || 0, rejected: Number(rows[l.id].rejected) || 0, reject_reason: rows[l.id].reject_reason,
        lot_code: rows[l.id].lot_code || null, expiry: rows[l.id].expiry || null, counted: rows[l.id].counted }))
      setRc(await post(`/api/purchasing/pos/${po.id}/receipts`, { lines, over_reason: over }))
      onDone()
    } catch (e) { toast(errMsg(e), 'err') }
  }
  const postIt = async () => {
    try { const r = await post(`/api/purchasing/receipts/${rc.id}/post`, { exception_reason: signed ? '' : excReason }); toast(r.duplicate ? '已入账（重复提交已拦截）' : '已签名入账，库存已更新'); onDone(); onClose() } catch (e) { toast(errMsg(e), 'err') }
  }
  return (
    <Modal open onClose={onClose} wide title={`收货：${po.code} ${po.supplier}`}
      footer={!rc ? <button className="btn-primary" onClick={submit}><Send size={16} />提交收货</button>
        : can('inventory.post') ? <><button className="btn-secondary" onClick={onClose}>稍后入账</button><button className="btn-primary" disabled={!signed && !excReason.trim()} onClick={postIt}><FileSignature size={16} />{signed ? '签名入账' : '例外入账'}</button></>
          : <button className="btn-primary" onClick={onClose}>完成</button>}>
      {!rc ? (
        <div className="space-y-3">
          {open.map((l: any) => {
            const r = rows[l.id]
            return (
              <div key={l.id} className="rounded-lg border border-slate-200 p-3">
                <div className="font-medium">{l.item_name} <span className="text-xs font-normal text-slate-500">{l.sku_code || '非库存'} · 未到 {num(l.open_qty)} {l.unit}</span></div>
                <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
                  <label><span className="label">实收</span><input className="input text-lg" type="number" inputMode="decimal" value={r.qty} onChange={(e) => set(l.id, { qty: e.target.value })} /></label>
                  {l.inventory && <label><span className="label">Lot</span><input className="input" value={r.lot_code} onChange={(e) => set(l.id, { lot_code: e.target.value })} /></label>}
                  {l.inventory && <label><span className="label">到期日</span><input className="input" type="date" value={r.expiry} onChange={(e) => set(l.id, { expiry: e.target.value })} /></label>}
                  <label><span className="label">拒收</span><input className="input" type="number" inputMode="decimal" value={r.rejected} onChange={(e) => set(l.id, { rejected: e.target.value })} /></label>
                </div>
                {Number(r.rejected) > 0 && <input className="input mt-2" placeholder="拒收原因 *（破损、规格不符…）" value={r.reject_reason} onChange={(e) => set(l.id, { reject_reason: e.target.value })} />}
                {l.inventory && (
                  <div className="mt-2 flex gap-2">
                    <button type="button" className={`btn flex-1 ring-1 ${r.counted ? 'bg-emerald-600 text-white ring-emerald-600' : 'ring-slate-300'}`} onClick={() => set(l.id, { counted: true })}>已点数</button>
                    <button type="button" className={`btn flex-1 ring-1 ${!r.counted ? 'bg-amber-500 text-white ring-amber-500' : 'ring-slate-300'}`} onClick={() => set(l.id, { counted: false })}>未核数（估计）</button>
                  </div>
                )}
              </div>
            )
          })}
          {exceeds && <Field label="超收原因 *"><input className="input" value={over} onChange={(e) => setOver(e.target.value)} /></Field>}
          <p className="text-xs text-slate-500">提交后不改库存；上传签收单并由仓管签名后才入账。</p>
        </div>
      ) : (
        <div className="space-y-3">
          <div className="rounded bg-emerald-50 p-3 text-sm text-emerald-700">收货单 {rc.code} 已提交。请拍签收依据：签收单、收据、到货照片都可以。</div>
          <div className="flex flex-wrap items-center gap-2">
            <select className="input w-auto" value={proofPurpose} onChange={(e) => setProofPurpose(e.target.value)} aria-label="依据类型">{SIGNOFF.map((x) => <option key={x}>{x}</option>)}</select>
            <PickButtons onFiles={up.add} />
          </div>
          <UploadQueue queue={up.queue} onRetry={up.retry} />
          {signed > 0 && <div className="text-sm text-emerald-700">已上传签收依据 {signed} 张</div>}
          {!signed && can('inventory.post') && (
            <Field label="确实没有任何依据？填写例外原因后可入账（会留例外标签）"><input className="input" placeholder="例如：司机未留签收单，已电话确认数量" value={excReason} onChange={(e) => setExcReason(e.target.value)} /></Field>
          )}
          {can('inventory.post') ? <p className="text-xs text-slate-500">签名含义：我已核对实物与签收单，确认入账。</p> : <p className="text-xs text-slate-500">仓管核对后签名入账，你可以关闭此窗口。</p>}
        </div>
      )}
    </Modal>
  )
}

// ---------------- PO detail ----------------
function Approval({ po, reload }: { po: any; reload: () => void }) {
  const toast = useToast()
  const [reason, setReason] = useState('')
  const a = po.approval || {}
  if (po.awaiting_approval && po.can.approve) {
    const act = async (decision: string) => {
      try { await post(`/api/purchasing/pos/${po.id}/approve`, { decision, reason }); reload() } catch (e) { toast(errMsg(e), 'err') }
    }
    return (
      <div className="rounded-lg border-2 border-amber-300 bg-amber-50 p-3">
        <div className="font-semibold">{a.after_the_fact ? '补录采购，需要你事后审批' : '需要你审批'}：{money(a.total)}（额度 {money(a.limit)}）</div>
        <input className="input mt-2" placeholder="意见（驳回必填）" value={reason} onChange={(e) => setReason(e.target.value)} />
        <div className="mt-2 flex gap-2 [&>button]:flex-1">
          <button className="btn-danger" onClick={() => act('reject')}><X size={16} />驳回</button>
          <button className="btn-primary" onClick={() => act('approve')}><Check size={16} />签名批准</button>
        </div>
        <p className="mt-1 text-xs text-slate-500">签名含义：我批准按此金额向该供应商采购。</p>
      </div>
    )
  }
  if (po.awaiting_approval) return <div className="rounded bg-amber-50 p-2 text-sm text-amber-800">{a.after_the_fact ? '补录采购，待老板事后审批' : '待老板审批'}：{money(a.total)} 超过额度 {money(a.limit)}</div>
  if (a.decision) return <div className="text-xs text-slate-500">{a.decision === 'approve' ? '批准' : '驳回'}：{a.by_name} · {dt(a.at)}{a.reason && ` · ${a.reason}`}{a.self && '（本人提交）'}</div>
  if (a.required === false) return <div className="text-xs text-slate-500">金额 {money(a.total)} 未超过额度 {money(a.limit)}，免审批</div>
  return null
}

function InvoicePay({ po, reload }: { po: any; reload: () => void }) {
  const toast = useToast()
  const [inv, setInv] = useState({ type: po.doc_type || 'invoice', no: po.invoice_no || '', amount: po.invoice_amount ?? '', reason: '' })
  const [note, setNote] = useState('')
  const [payExc, setPayExc] = useState('')
  const m = po.match
  const act = async (path: string, body: any) => { try { await post(`/api/purchasing/pos/${po.id}/${path}`, body); reload() } catch (e) { toast(errMsg(e), 'err') } }
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2"><span className="text-sm font-semibold">票据与付款</span><Badge color={PAY_COLOR[po.payment_status]}>{po.payment_label}</Badge><Badge>{po.doc_label}</Badge>{po.missing_doc && <Badge color="yellow">缺{po.doc_label}照片</Badge>}</div>
      {(po.purchase_date || po.payment_method) && <div className="text-xs text-slate-600">{po.purchase_date && `购买日期 ${po.purchase_date}`}{po.payment_method && ` · 付款：${po.payment_method_label}`}{po.paid_by && `（${po.paid_by} 垫付，待报销）`}</div>}
      <div className="grid grid-cols-3 gap-2 text-center text-xs">
        <div className="rounded bg-slate-50 p-2"><div className="text-slate-400">订单金额</div><div className="font-semibold">{money(po.total)}</div></div>
        <div className="rounded bg-slate-50 p-2"><div className="text-slate-400">应付参考（按已收）</div><div className="font-semibold">{money(po.payable)}</div></div>
        <div className={`rounded p-2 ${m ? (m.ok ? 'bg-emerald-50' : 'bg-red-50') : 'bg-slate-50'}`}><div className="text-slate-400">{po.doc_label}金额</div><div className="font-semibold">{money(po.invoice_amount)}</div>
          {m && !m.ok && <div className="text-red-600">差 {money(m.diff)}</div>}</div>
      </div>
      {po.payment_status !== 'reconciled' && (po.can.manage || po.can.reconcile) && (
        <div className="space-y-2">
          <Segmented value={inv.type} onChange={(k) => setInv({ ...inv, type: k })} items={[{ key: 'invoice', label: '发票' }, { key: 'receipt', label: '收据' }, { key: 'none', label: '无票据' }]} />
          <div className="grid grid-cols-[1fr_1fr_auto] gap-2">
            <input className="input" placeholder={inv.type === 'invoice' ? '发票号 *' : '票据号（可空）'} value={inv.no} onChange={(e) => setInv({ ...inv, no: e.target.value })} />
            <input className="input" type="number" inputMode="decimal" placeholder="金额 $" value={inv.amount} onChange={(e) => setInv({ ...inv, amount: e.target.value })} />
            <button className="btn-secondary" disabled={(inv.type === 'invoice' && !inv.no) || inv.amount === '' || (inv.type === 'none' && !inv.reason)}
              onClick={() => act('invoice', { doc_type: inv.type, invoice_no: inv.no, invoice_amount: Number(inv.amount), reason: inv.reason })}>登记</button>
          </div>
          {inv.type === 'none' && <input className="input" placeholder="没有票据的原因 *（会留例外标签）" value={inv.reason} onChange={(e) => setInv({ ...inv, reason: e.target.value })} />}
        </div>
      )}
      <EvidencePanel targetType="po" targetId={po.id} purposes={['发票', '收据', '付款证明', '报价', '采购单PDF', '验收照片']} compact onChange={reload} />
      <div className="flex flex-wrap gap-2">
        {['none', 'invoiced'].includes(po.payment_status) && (po.can.manage || po.can.reconcile) && (
          po.evidence.payment_proof
            ? <button className="btn-secondary" onClick={() => act('payment', { status: 'paid_pending' })}>登记已付款</button>
            : <div className="flex w-full gap-2"><input className="input" placeholder="没有付款凭证？写原因（如现金）" value={payExc} onChange={(e) => setPayExc(e.target.value)} />
                <button className="btn-secondary shrink-0" disabled={!payExc.trim()} onClick={() => act('payment', { status: 'paid_pending', exception_reason: payExc })}>例外登记已付款</button></div>
        )}
        {po.payment_status === 'paid_pending' && po.can.reconcile && (
          <div className="w-full space-y-2">
            {m && !m.ok && <input className="input" placeholder="差异说明 *" value={note} onChange={(e) => setNote(e.target.value)} />}
            <button className="btn-primary w-full sm:w-auto" onClick={() => act('payment', { status: 'reconciled', note })}><FileSignature size={16} />财务核对签名</button>
          </div>
        )}
        {po.payment_status === 'reconciled' && <div className="text-xs text-slate-500">已核对：{po.reconcile.by_name} · {dt(po.reconcile.at)}{po.reconcile.note && ` · ${po.reconcile.note}`}</div>}
      </div>
      <p className="text-xs text-slate-400">付款证明或收据都可以作为付款凭证；上传后只代表「已付款待核对」，由财务核对签名后才算完成。</p>
    </div>
  )
}

function ReceiptCard({ r, po, reload }: { r: any; po: any; reload: () => void }) {
  const toast = useToast()
  const lineName = (id: string) => po.lines.find((l: any) => l.id === id)?.item_name
  const act = async (path: string, body?: any) => { try { await post(`/api/purchasing/receipts/${r.id}/${path}`, body); reload() } catch (e) { toast(errMsg(e), 'err') } }
  return (
    <div className="rounded-lg border border-slate-200 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono font-medium">{r.code}</span>
        <Badge color={r.status === 'posted' ? 'green' : r.status === 'voided' ? 'gray' : 'yellow'}>{({ submitted: '待入账', posted: '已入账', voided: '已作废' } as any)[r.status]}</Badge>
        {r.self_posted && <Badge color="purple">本人提交本人入账</Badge>}
        <span className="text-xs text-slate-400">{r.submitted_by} · {dt(r.submitted_at)}{r.posted_by && ` · 入账 ${r.posted_by}`}</span>
      </div>
      <div className="mt-1 text-xs text-slate-600">{r.lines.map((x: any) => `${lineName(x.po_line_id)} 实收 ${x.qty}${x.lot_code ? ` Lot ${x.lot_code}` : ''}${x.counted === false ? '（未核数）' : ''}${x.rejected ? ` 拒收 ${x.rejected}` : ''}`).join('；')}</div>
      {r.status === 'submitted' && (
        <div className="mt-2 space-y-2">
          <EvidencePanel targetType="receipt" targetId={r.id} purposes={[...SIGNOFF, '差异照片']} compact onChange={reload} />
          <div className="flex flex-wrap gap-2">
            {po.can.post && r.signoff_evidence > 0 && <button className="btn-primary btn-sm" onClick={() => act('post', {})}><FileSignature size={14} />签名入账</button>}
            {po.can.post && !r.signoff_evidence && <button className="btn-secondary btn-sm" onClick={() => { const reason = prompt('没有任何签收依据的例外原因（会留例外标签）'); if (reason) act('post', { exception_reason: reason }) }}><AlertTriangle size={14} />例外入账</button>}
            <button className="btn-ghost btn-sm" onClick={() => { const reason = prompt('作废原因'); if (reason) act('void', { reason }) }}>作废</button>
            {!r.signoff_evidence && <span className="self-center text-xs text-amber-700">缺签收依据（签收单/收据/到货照片任一即可）</span>}
          </div>
        </div>
      )}
    </div>
  )
}

export function PODetail({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => void }) {
  const toast = useToast()
  const { data: po, reload: r0 } = useLoad(() => get(`/api/purchasing/pos/${id}`), [id])
  const [receiving, setReceiving] = useState(false)
  const [eta, setEta] = useState<string | null>(null)
  const reload = () => { r0(); onChanged() }
  if (!po) return null
  const act = async (path: string, body?: any, msg?: string) => {
    try { await post(`/api/purchasing/pos/${id}/${path}`, body); if (msg) toast(msg); reload() } catch (e) { toast(errMsg(e), 'err') }
  }
  const saveEta = async () => { try { await patch(`/api/purchasing/pos/${id}`, { version: po.version, expected_date: eta || null }); setEta(null); reload() } catch (e) { toast(errMsg(e), 'err') } }
  const ask = (q: string) => prompt(q) || ''
  return (
    <Modal open onClose={onClose} wide title={<span className="flex flex-wrap items-center gap-2">{po.code}<Badge color={ST_COLOR[po.status]}>{po.status_label}</Badge>{po.overdue && <Badge color="red">逾期</Badge>}</span>}>
      <div className="space-y-4 pb-16 sm:pb-0">
        <dl className="kv">
          <dt>供应商</dt><dd className="font-medium">{po.supplier} <span className="text-xs text-slate-400">{po.category}</span></dd>
          <dt>金额</dt><dd><b>{money(po.total)}</b> <span className="text-xs text-slate-400">含税 {money(po.tax)} 运 {money(po.freight)} 装 {money(po.install_fee)}</span></dd>
          {!po.quick && <><dt>预计到货</dt><dd>{eta !== null ? (
            <span className="flex gap-2"><input className="input" type="date" value={eta} onChange={(e) => setEta(e.target.value)} /><button className="btn-primary btn-sm" onClick={saveEta}>保存</button></span>
          ) : <span className={po.overdue ? 'font-semibold text-red-600' : ''}>{po.expected_date || '未定'} {po.can.manage && !['closed', 'cancelled', 'received'].includes(po.status) && <button className="btn-ghost btn-sm ml-1 text-brand-700" onClick={() => setEta(po.expected_date || '')}>修改</button>}</span>}</dd></>}
          <dt>收货仓库</dt><dd>{po.warehouse || '—'}</dd>
          <dt>负责人</dt><dd>{po.owner}</dd>
          {po.note && <><dt>备注</dt><dd>{po.note}</dd></>}
          {po.close_reason && <><dt>关闭原因</dt><dd>{po.close_reason}</dd></>}
        </dl>
        {po.quick && <div className="rounded bg-sky-50 p-2 text-xs text-sky-800">补录的已完成采购：货已到手，已直接入库。</div>}
        {po.exceptions.length > 0 && (
          <div className="rounded-lg border border-violet-200 bg-violet-50 p-2 text-sm">
            <div className="mb-1 font-semibold text-violet-800">例外记录（{po.exceptions.length}）</div>
            {po.exceptions.map((e: any, i: number) => <div key={i} className="text-xs text-violet-900">{e.step}：{e.reason} <span className="text-violet-500">— {e.by_name} · {dt(e.at)}</span></div>)}
          </div>
        )}
        <Approval po={po} reload={reload} />
        <div>
          <div className="mb-2 text-sm font-semibold">采购行 · 到货 {po.received_pct}%</div>
          <div className="space-y-2">
            {po.lines.map((l: any) => (
              <div key={l.id} className="rounded-lg border border-slate-200 p-2 text-sm">
                <div className="flex flex-wrap items-baseline gap-x-2"><span className="font-medium">{l.item_name}</span><span className="text-xs text-slate-400">{l.sku_code || '非库存'} · {money(l.unit_price)}/{l.unit}</span><span className="ml-auto font-semibold">{money(l.amount)}</span></div>
                <div className="mt-1 grid grid-cols-4 gap-1 text-center text-xs">
                  {[['订购', l.qty_ordered], ['已收', l.qty_received], ['拒收', l.qty_rejected], ['未到', l.open_qty]].map(([k, v]: any) => (
                    <div key={k} className="rounded bg-slate-50 py-1"><div className="text-slate-400">{k}</div><div className={`font-semibold ${k === '拒收' && v > 0 ? 'text-red-600' : ''}`}>{num(v)}</div></div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
        {po.receipts.length > 0 && <div><div className="mb-2 text-sm font-semibold">收货单</div><div className="space-y-2">{po.receipts.map((r: any) => <ReceiptCard key={r.id} r={r} po={po} reload={reload} />)}</div></div>}
        {['ordered', 'partial', 'received', 'closed'].includes(po.status) && <InvoicePay po={po} reload={reload} />}
        {!['ordered', 'partial', 'received', 'closed'].includes(po.status) && <EvidencePanel title="报价 / 附件" targetType="po" targetId={po.id} purposes={['报价', '采购单PDF', '附件']} compact />}
        <div><div className="mb-2 text-sm font-semibold">沟通</div><Timeline targetType="po" targetId={po.id} /></div>
      </div>
      {/* actions: sticky on phones; hidden when nothing can be done */}
      {(() => {
        const canAct = (['draft', 'rejected'].includes(po.status) && po.can.manage) || (po.status === 'approved' && po.can.manage)
          || (['ordered', 'partial'].includes(po.status) && (po.can.receive || po.can.manage))
          || (!['closed', 'cancelled', 'received'].includes(po.status) && po.can.manage && !po.receipts.some((r: any) => r.status === 'posted'))
        return canAct
      })() && <div className="sticky bottom-0 -mx-4 -mb-4 flex gap-2 overflow-x-auto border-t bg-white px-4 py-2 pb-[max(0.5rem,env(safe-area-inset-bottom))] [&>button]:shrink-0">
        {['draft', 'rejected'].includes(po.status) && po.can.manage && <button className="btn-primary" onClick={() => act('submit', undefined, '已提交')}><Send size={16} />提交{po.total > po.approval_limit ? '审批' : ''}</button>}
        {po.status === 'approved' && po.can.manage && <button className="btn-primary" onClick={() => act('order', undefined, '已标记下单')}><ShoppingCart size={16} />标记已下单</button>}
        {['ordered', 'partial'].includes(po.status) && po.can.receive && <button className="btn-primary" onClick={() => setReceiving(true)}><PackageCheck size={16} />收货</button>}
        {['ordered', 'partial'].includes(po.status) && po.can.manage && <button className="btn-secondary" onClick={() => { const r = ask('按短收关闭的原因（剩余数量不再等待）'); if (r) act('close', { reason: r }) }}>按短收关闭</button>}
        {!['closed', 'cancelled', 'received'].includes(po.status) && po.can.manage && !po.receipts.some((r: any) => r.status === 'posted') &&
          <button className="btn-ghost text-red-600" onClick={() => { const r = ask('取消原因'); if (r) act('cancel', { reason: r }) }}>取消采购单</button>}
      </div>}
      {receiving && <ReceiveDialog po={po} onClose={() => setReceiving(false)} onDone={reload} />}
    </Modal>
  )
}

// ---------------- module ----------------
export function PurchasingModule({ preview }: ModProps) {
  const { can } = useAuth()
  const toast = useToast()
  const [sp, setSp] = useSearchParams()
  const [filter, setFilter] = useState('open')
  const [creating, setCreating] = useState(false)
  const [quick, setQuick] = useState(false)
  const settings = useLoad(() => get('/api/purchasing/settings'), [])
  const q = ({ open: 'open_only=true', pending_approval: 'status=pending_approval', receiving: 'status=ordered,partial', done: 'status=received,closed,cancelled', all: '' } as any)[filter]
  const list = useLoad(() => get(`/api/purchasing/pos?${q}`), [q])
  const sel = sp.get('po')
  const open = (id: string | null) => { const n = new URLSearchParams(sp); if (id) n.set('po', id); else n.delete('po'); setSp(n, { replace: true }) }
  useEffect(() => { if (sel) setCreating(false) }, [sel])
  const editLimit = async () => {
    const v = prompt('采购审批额度（美元，超过需老板批准）', String(settings.data?.approval_limit ?? 2000))
    if (v === null) return
    try { await put('/api/purchasing/settings', { approval_limit: Number(v) }); settings.reload() } catch (e) { toast(errMsg(e), 'err') }
  }
  const scan = async (code: string) => { try { const r = await get(`/api/inv/scan/${encodeURIComponent(code)}`); if (r.type === 'po') open(r.po_id); else toast('不是采购单码', 'err') } catch (e) { toast(errMsg(e), 'err') } }
  if (preview) return <div className="text-sm text-slate-400">采购单与收货（预览不加载数据）</div>
  if (!list.data) return <Spinner />
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {can('po.manage') && <button className="btn-primary" onClick={() => setCreating(true)}><Plus size={16} />新建采购单</button>}
        {can('po.manage') && <button className="btn-secondary" onClick={() => setQuick(true)}><Receipt size={16} />补录已完成采购</button>}
        <ScanButton label="扫采购单" onResult={scan} />
        <span className="w-full text-xs text-slate-400 sm:ml-auto sm:w-auto">审批额度 {money(settings.data?.approval_limit)}{settings.data?.can_edit && <button className="btn-ghost btn-sm ml-1 text-brand-700" onClick={editLimit}>修改</button>}</span>
      </div>
      <Segmented value={filter} onChange={setFilter} items={[{ key: 'open', label: '进行中' }, { key: 'pending_approval', label: '待审批' }, { key: 'receiving', label: '待收货' }, { key: 'done', label: '已完结' }, { key: 'all', label: '全部' }]} />
      <div className="mt-3 space-y-2">
        {list.data.map((p: any) => (
          <button key={p.id} onClick={() => open(p.id)} className="block w-full rounded-lg border border-slate-200 p-3 text-left active:bg-slate-50 sm:hover:bg-slate-50">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-sm font-medium">{p.code}</span>
              <Badge color={ST_COLOR[p.status]}>{p.status_label}</Badge>
              {p.overdue && <Badge color="red"><AlertTriangle size={10} />逾期</Badge>}
              {p.pending_receipts > 0 && <Badge color="yellow">待入账 {p.pending_receipts}</Badge>}
              {p.awaiting_approval && p.status !== 'pending_approval' && <Badge color="yellow">事后待审批</Badge>}
              {p.quick && <Badge color="blue">补录</Badge>}
              {p.exception_count > 0 && <Badge color="purple">例外 {p.exception_count}</Badge>}
              {p.missing_doc && <Badge color="yellow">缺{p.doc_label}</Badge>}
              <span className="ml-auto font-semibold">{money(p.total)}</span>
            </div>
            <div className="mt-1 flex flex-wrap gap-x-3 text-xs text-slate-500">
              <span>{p.supplier}</span><span>{p.category}</span>{!p.quick && <span>预计 {p.expected_date || '未定'}</span>}<span>{p.payment_label}</span>
            </div>
            {['ordered', 'partial', 'received'].includes(p.status) && <div className="mt-2 h-1.5 overflow-hidden rounded bg-slate-100"><div className="h-full bg-brand-500" style={{ width: `${p.received_pct}%` }} /></div>}
          </button>
        ))}
        {!list.data.length && <Empty />}
      </div>
      {creating && <NewPO limit={settings.data?.approval_limit ?? 2000} onClose={() => setCreating(false)} onCreated={(id) => { setCreating(false); list.reload(); open(id) }} />}
      {quick && <QuickPurchase limit={settings.data?.approval_limit ?? 2000} onClose={() => setQuick(false)} onDone={() => list.reload()} onOpen={(id) => { setQuick(false); open(id) }} />}
      {sel && <PODetail id={sel} onClose={() => open(null)} onChanged={list.reload} />}
    </div>
  )
}

// ---------------- requisitions & quotes ----------------
function ReqForm({ initial, title, onClose, onSave }: { initial?: any; title: string; onClose: () => void; onSave: (body: any) => Promise<void> }) {
  const master = useLoad(() => get('/api/inv/master'), [])
  const [f, setF] = useState<any>(initial || { title: '', category: '原料包材', margin_pct: '15', note: '', lines: [{ sku_id: '', item_name: '', base_qty: '', unit: '箱' }] })
  const setLine = (i: number, p: any) => setF({ ...f, lines: f.lines.map((l: any, j: number) => (i === j ? { ...l, ...p } : l)) })
  const skus = master.data?.skus || []
  const save = () => onSave({
    ...f, margin_pct: Number(f.margin_pct) || 0,
    lines: f.lines.filter((l: any) => l.base_qty).map((l: any) => ({ key: l.key, sku_id: l.sku_id || null, item_name: l.item_name || skus.find((s: any) => s.id === l.sku_id)?.name || '', base_qty: Number(l.base_qty), unit: l.unit || '箱' })),
  })
  return (
    <Modal open onClose={onClose} wide title={title} footer={<button className="btn-primary" onClick={save}>保存</button>}>
      <div className="grid gap-3 sm:grid-cols-3">
        {!initial && <Field label="需求名称"><input className="input" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>}
        {!initial && <Field label="分类"><select className="input" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>{CATS.map((c) => <option key={c}>{c}</option>)}</select></Field>}
        <Field label="采购余量 %" hint="余量只影响采购数量，不改配方"><input className="input" type="number" inputMode="decimal" value={f.margin_pct} onChange={(e) => setF({ ...f, margin_pct: e.target.value })} /></Field>
      </div>
      <div className="mt-3 space-y-2">
        {f.lines.map((l: any, i: number) => (
          <div key={i} className="grid grid-cols-2 gap-2 rounded-lg border border-slate-200 p-2 sm:grid-cols-[2fr_1.5fr_1fr_1fr]">
            <select className="input col-span-2 sm:col-span-1" value={l.sku_id || ''} onChange={(e) => setLine(i, { sku_id: e.target.value })}><option value="">非库存品</option>{skus.map((s: any) => <option key={s.id} value={s.id}>{skuLabel(s)}</option>)}</select>
            <input className="input col-span-2 sm:col-span-1" placeholder="品名" value={l.item_name} onChange={(e) => setLine(i, { item_name: e.target.value })} />
            <input className="input" type="number" inputMode="decimal" placeholder="基础数量" value={l.base_qty} onChange={(e) => setLine(i, { base_qty: e.target.value })} />
            <input className="input" placeholder="单位" value={l.unit} onChange={(e) => setLine(i, { unit: e.target.value })} />
          </div>
        ))}
        <button className="btn-ghost btn-sm" onClick={() => setF({ ...f, lines: [...f.lines, { sku_id: '', item_name: '', base_qty: '', unit: '箱' }] })}><Plus size={14} />加一行</button>
      </div>
      <Field label="版本说明"><input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
    </Modal>
  )
}

function QuoteForm({ version, onClose, onSave }: { version: any; onClose: () => void; onSave: (b: any) => Promise<void> }) {
  const [f, setF] = useState<any>({ supplier: '', tax: '', freight: '', install_fee: '', lead_time_days: '', valid_until: '', note: '', prices: {} })
  return (
    <Modal open onClose={onClose} title={`添加报价（基于 v${version.version_no}）`} footer={<button className="btn-primary" disabled={!f.supplier} onClick={() => onSave({
      ...f, tax: Number(f.tax) || 0, freight: Number(f.freight) || 0, install_fee: Number(f.install_fee) || 0, lead_time_days: f.lead_time_days ? Number(f.lead_time_days) : null,
      valid_until: f.valid_until || null, version_id: version.id, prices: Object.fromEntries(Object.entries(f.prices).filter(([, v]) => v !== '').map(([k, v]) => [k, Number(v)])),
    })}>保存</button>}>
      <div className="space-y-3">
        <Field label="供应商"><input className="input" value={f.supplier} onChange={(e) => setF({ ...f, supplier: e.target.value })} /></Field>
        {version.lines.map((l: any) => (
          <Field key={l.key} label={`${l.item_name} 单价 $（需求 ${num(l.required_qty)} ${l.unit}）`}>
            <input className="input" type="number" inputMode="decimal" value={f.prices[l.key] ?? ''} onChange={(e) => setF({ ...f, prices: { ...f.prices, [l.key]: e.target.value } })} />
          </Field>
        ))}
        <div className="grid grid-cols-3 gap-2">
          <Field label="税 $"><input className="input" type="number" inputMode="decimal" value={f.tax} onChange={(e) => setF({ ...f, tax: e.target.value })} /></Field>
          <Field label="运费 $"><input className="input" type="number" inputMode="decimal" value={f.freight} onChange={(e) => setF({ ...f, freight: e.target.value })} /></Field>
          <Field label="安装费 $"><input className="input" type="number" inputMode="decimal" value={f.install_fee} onChange={(e) => setF({ ...f, install_fee: e.target.value })} /></Field>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <Field label="交期（天）"><input className="input" type="number" inputMode="numeric" value={f.lead_time_days} onChange={(e) => setF({ ...f, lead_time_days: e.target.value })} /></Field>
          <Field label="报价有效至"><input className="input" type="date" value={f.valid_until} onChange={(e) => setF({ ...f, valid_until: e.target.value })} /></Field>
        </div>
      </div>
    </Modal>
  )
}

function ReqDetail({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => void }) {
  const toast = useToast()
  const [sp, setSp] = useSearchParams()
  const { data: r, reload } = useLoad(() => get(`/api/purchasing/requisitions/${id}`), [id])
  const [dlg, setDlg] = useState<string | null>(null)
  if (!r) return null
  const cur = r.versions.find((v: any) => v.id === r.current_version_id)
  const toPO = async (q: any) => {
    try {
      const res = await post(`/api/purchasing/quotes/${q.id}/to-po`, {})
      toast(`已生成 ${res.code}（草稿）`); onChanged()
      const n = new URLSearchParams(sp); n.delete('rq'); n.set('po', res.id); setSp(n)
    } catch (e) { toast(errMsg(e), 'err') }
  }
  return (
    <Modal open onClose={onClose} wide title={<span>{r.code} {r.title}</span>}>
      <div className="space-y-4">
        <div>
          <div className="mb-2 flex items-center gap-2"><span className="text-sm font-semibold">需求版本</span>{r.can_manage && r.status === 'open' && <button className="btn-secondary btn-sm ml-auto shrink-0 whitespace-nowrap" onClick={() => setDlg('version')}>新版本</button>}</div>
          <div className="space-y-2">
            {r.versions.map((v: any) => (
              <div key={v.id} className={`rounded-lg border p-2 text-sm ${v.id === r.current_version_id ? 'border-brand-500 bg-brand-50/40' : 'border-slate-200 text-slate-500'}`}>
                <div className="flex flex-wrap gap-2"><b>v{v.version_no}</b><span>余量 {v.margin_pct}%</span>{v.id === r.current_version_id && <Badge color="green">当前</Badge>}<span className="text-xs">{v.by} · {dt(v.at)}</span>{v.note && <span className="text-xs">· {v.note}</span>}</div>
                <div className="mt-1 text-xs">{v.lines.map((l: any) => `${l.item_name} ${num(l.base_qty)}→${num(l.required_qty)} ${l.unit}`).join('；')}</div>
              </div>
            ))}
          </div>
        </div>
        <div>
          <div className="mb-2 flex items-center gap-2"><span className="text-sm font-semibold">报价对比<span className="block text-xs font-normal text-slate-400 sm:inline sm:ml-1">到手总价 = 货款 + 税 + 运费 + 安装费</span></span>{r.can_manage && r.status === 'open' && <button className="btn-secondary btn-sm ml-auto shrink-0 whitespace-nowrap" onClick={() => setDlg('quote')}><Plus size={14} />报价</button>}</div>
          <div className="space-y-2">
            {r.quotes.map((q: any) => (
              <div key={q.id} className={`rounded-lg border p-3 text-sm ${q.stale || q.status === 'rejected' ? 'border-slate-200 opacity-60' : q.best_price ? 'border-emerald-400' : 'border-slate-200'}`}>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{q.supplier}</span>
                  {q.best_price && <Badge color="green">最低总价</Badge>}{q.fastest && <Badge color="blue">交期最快</Badge>}
                  {q.stale && <Badge>基于旧版本 v{q.version_no}</Badge>}{q.status === 'selected' && <Badge color="purple">已选用</Badge>}
                  {q.missing_lines.length > 0 && <Badge color="red">缺报价行</Badge>}
                  <span className="ml-auto text-lg font-bold">{money(q.total)}</span>
                </div>
                <div className="mt-1 text-xs text-slate-500">货款 {money(q.subtotal)} · 税 {money(q.tax)} · 运 {money(q.freight)} · 装 {money(q.install_fee)} · 交期 {q.lead_time_days ?? '—'} 天{q.valid_until && ` · 有效至 ${q.valid_until}`}</div>
                {r.can_manage && r.status === 'open' && !q.stale && !q.missing_lines.length && q.status === 'active' &&
                  <button className="btn-primary btn-sm mt-2" onClick={() => toPO(q)}><ShoppingCart size={14} />选这家，生成采购单</button>}
              </div>
            ))}
            {!r.quotes.length && <Empty>还没有报价</Empty>}
          </div>
        </div>
        <EvidencePanel title="报价单原件" targetType="requisition" targetId={r.id} purposes={['报价单']} compact />
        {r.pos.length > 0 && <div className="text-sm">已生成采购单：{r.pos.map((p: any) => <button key={p.id} className="ml-2 text-brand-700 underline" onClick={() => { const n = new URLSearchParams(sp); n.delete('rq'); n.set('po', p.id); setSp(n) }}>{p.code}</button>)}</div>}
      </div>
      {dlg === 'version' && <ReqForm title={`新版本（当前 v${cur.version_no}）`} initial={{ margin_pct: String(cur.margin_pct), note: '', lines: cur.lines.map((l: any) => ({ ...l, base_qty: String(l.base_qty) })) }}
        onClose={() => setDlg(null)} onSave={async (b) => { try { await post(`/api/purchasing/requisitions/${id}/versions`, b); setDlg(null); reload() } catch (e) { toast(errMsg(e), 'err') } }} />}
      {dlg === 'quote' && <QuoteForm version={cur} onClose={() => setDlg(null)} onSave={async (b) => { try { await post(`/api/purchasing/requisitions/${id}/quotes`, b); setDlg(null); reload() } catch (e) { toast(errMsg(e), 'err') } }} />}
    </Modal>
  )
}

export function RequisitionsModule({ preview }: ModProps) {
  const { can } = useAuth()
  const toast = useToast()
  const [sp, setSp] = useSearchParams()
  const [creating, setCreating] = useState(false)
  const list = useLoad(() => (preview ? Promise.resolve([]) : get('/api/purchasing/requisitions')), [preview])
  const sel = sp.get('rq')
  const open = (id: string | null) => { const n = new URLSearchParams(sp); if (id) n.set('rq', id); else n.delete('rq'); setSp(n, { replace: true }) }
  if (preview) return <div className="text-sm text-slate-400">需求与报价对比（预览不加载数据）</div>
  return (
    <div>
      {can('po.manage') && <button className="btn-secondary mb-3" onClick={() => setCreating(true)}><Plus size={16} />新建需求</button>}
      <div className="space-y-2">
        {(list.data || []).map((r: any) => (
          <button key={r.id} onClick={() => open(r.id)} className="block w-full rounded-lg border border-slate-200 p-3 text-left active:bg-slate-50 sm:hover:bg-slate-50">
            <div className="flex flex-wrap items-center gap-2"><span className="font-medium">{r.title}</span><Badge color={r.status === 'open' ? 'blue' : 'gray'}>{({ open: '询价中', ordered: '已下单', closed: '已关闭', cancelled: '已取消' } as any)[r.status]}</Badge></div>
            <div className="mt-1 text-xs text-slate-500">{r.code} · {r.category} · 当前 v{r.version_no}（余量 {r.margin_pct}%）· 报价 {r.quotes} 份</div>
          </button>
        ))}
        {list.data && !list.data.length && <Empty />}
      </div>
      {creating && <ReqForm title="新建采购需求" onClose={() => setCreating(false)} onSave={async (b) => { try { const r = await post('/api/purchasing/requisitions', b); setCreating(false); list.reload(); open(r.id) } catch (e) { toast(errMsg(e), 'err') } }} />}
      {sel && <ReqDetail id={sel} onClose={() => open(null)} onChanged={list.reload} />}
    </div>
  )
}
