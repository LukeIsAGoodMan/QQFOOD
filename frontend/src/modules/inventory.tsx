import { Fragment, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ChevronDown, ChevronRight, Download, PackageCheck, Plus, Printer, ShieldAlert, ShieldCheck, Tags, Truck, Warehouse } from 'lucide-react'
import { ApiError, get, post, uuid } from '../lib/api'
import { useAuth } from '../lib/auth'
import { ago, dt, num, skuLabel } from '../lib/fmt'
import { t } from '../lib/i18n'
import { Badge, Empty, ErrorBox, Field, LOT_COLOR, LOT_LABEL, Modal, Spinner, errMsg, useLoad, useToast } from '../components/ui'
import { EvidencePanel } from '../components/Evidence'
import { LotLabel, ScanButton } from '../components/Scan'
import { Timeline, type ModProps } from './basic'
import { AliasManager } from './aliases'
import { CatBadge, CategoryChips, ClassifyDialog, FOOD, UNCAT } from './categories'

const useMaster = () => useLoad(() => get('/api/inv/master'), [])

function useScanResolver(master: any, onSku: (id: string) => void, onLot: (lot: any) => void) {
  const toast = useToast()
  return async (code: string) => {
    try {
      const r = await get(`/api/inv/scan/${encodeURIComponent(code)}`)
      if (r.type === 'sku') onSku(r.sku_id)
      else if (r.type === 'lot') { onSku(r.sku_id); onLot(master?.lots.find((l: any) => l.id === r.lot_id) || r) }
      else toast(r.type === 'ambiguous' ? '多个 SKU 有相同批次号，请手动选择' : `识别为 ${r.type}`, 'err')
    } catch (e) { toast(errMsg(e), 'err') }
  }
}

// ---------- receive / count / issue ----------
function ReceiveDialog({ master, onClose, onDone }: { master: any; onClose: () => void; onDone: () => void }) {
  const toast = useToast()
  const [key] = useState(uuid())
  const [f, setF] = useState<any>({ sku_id: '', warehouse_id: master.warehouses.find((w: any) => w.tracked)?.id, qty: '', unit: '', lot_code: '', expiry: '', counted: true, source_id: '', source_kind: 'company' })
  const [posting, setPosting] = useState<any>(null)
  const sku = master.skus.find((s: any) => s.id === f.sku_id)
  const scan = useScanResolver(master, (id) => setF((x: any) => ({ ...x, sku_id: id })), (lot) => setF((x: any) => ({ ...x, lot_code: lot.lot_code })))
  const save = async () => {
    try {
      const r = await post('/api/inv/receive', { ...f, idempotency_key: key, qty: Number(f.qty), unit: f.unit || null, lot_code: f.lot_code || null, expiry: f.expiry || null, source_type: f.source_kind, source_id: f.source_id || null })
      setPosting(r); onDone()
      toast(r.duplicate ? '重复提交已拦截，库存未重复增加' : '已入账')
    } catch (e) { toast(errMsg(e), 'err') }
  }
  return (
    <Modal open onClose={onClose} title={t('inv.receive')} footer={!posting && <button className="btn-primary" disabled={!f.sku_id || !f.qty} onClick={save}>{t('common.submit')}</button>}>
      {posting ? (
        <div className="space-y-3">
          <div className="rounded bg-emerald-50 p-3 text-sm text-emerald-700">已过账 {posting.posting_id}。请上传签收依据（签收单、到货照片）。</div>
          <EvidencePanel targetType="posting" targetId={posting.posting_id} purposes={['签收单', '到货照片', 'BOL']} />
        </div>
      ) : (
        <div className="space-y-3">
          <ScanButton onResult={scan} />
          <Field label="SKU"><select className="input" value={f.sku_id} onChange={(e) => setF({ ...f, sku_id: e.target.value, unit: '' })}><option value="">—</option>{master.skus.map((s: any) => <option key={s.id} value={s.id}>{skuLabel(s)}</option>)}</select></Field>
          <Field label="仓库"><select className="input" value={f.warehouse_id} onChange={(e) => setF({ ...f, warehouse_id: e.target.value })}>{master.warehouses.filter((w: any) => w.tracked && w.active).map((w: any) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></Field>
          <Field label="货从哪来"><select className="input" value={f.source_kind} onChange={(e) => setF({ ...f, source_kind: e.target.value })}>
            <option value="po">采购到货</option><option value="company">公司调入（多伦多、温哥华等）</option><option value="other">其他（退货、样品、期初…）</option></select></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label={t('inv.qty')}><input className="input text-lg" type="number" inputMode="decimal" value={f.qty} onChange={(e) => setF({ ...f, qty: e.target.value })} /></Field>
            <Field label="单位" hint={sku && Object.keys(sku.conversions || {}).length ? `1 ${sku.base_unit} = ${Object.entries(sku.conversions).map(([u, n]) => `${n} ${u}`).join(' / ')}` : '未定义其他单位'}>
              <select className="input" value={f.unit} onChange={(e) => setF({ ...f, unit: e.target.value })}>
                <option value="">{sku?.base_unit || '基本单位'}</option>
                {sku && Object.keys(sku.conversions || {}).map((u) => <option key={u}>{u}</option>)}
              </select>
            </Field>
            <Field label={t('inv.lot')}><input className="input" value={f.lot_code} onChange={(e) => setF({ ...f, lot_code: e.target.value })} /></Field>
            <Field label="到期日"><input className="input" type="date" value={f.expiry} onChange={(e) => setF({ ...f, expiry: e.target.value })} /></Field>
          </div>
          <Field label={f.source_kind === 'po' ? '采购单号' : f.source_kind === 'company' ? '来源与单据（如 多伦多 BOL 12345）' : '说明'}><input className="input" value={f.source_id} onChange={(e) => setF({ ...f, source_id: e.target.value })} /></Field>
          <div className="flex gap-2">
            <button type="button" className={`btn flex-1 ring-1 ${f.counted ? 'bg-emerald-600 text-white ring-emerald-600' : 'ring-slate-300'}`} onClick={() => setF({ ...f, counted: true })}>{t('inv.counted')}</button>
            <button type="button" className={`btn flex-1 ring-1 ${!f.counted ? 'bg-amber-500 text-white ring-amber-500' : 'ring-slate-300'}`} onClick={() => setF({ ...f, counted: false })}>{t('inv.estimated')}</button>
          </div>
          {!f.counted && <p className="text-xs text-amber-700">未核数数量单独展示，不计入可用量；盘点核数后才转为实物库存。</p>}
        </div>
      )}
    </Modal>
  )
}

function CountDialog({ master, row, lot, onClose, onDone }: any) {
  const toast = useToast()
  const [key] = useState(uuid())
  const [qty, setQty] = useState('')
  const [reason, setReason] = useState('')
  const save = async () => {
    try {
      await post('/api/inv/count', { idempotency_key: key, sku_id: row.sku_id, warehouse_id: row.warehouse_id, lot_id: lot?.lot_id || null, counted_qty: Number(qty), reason })
      toast('盘点已过账'); onDone(); onClose()
    } catch (e) { toast(errMsg(e), 'err') }
  }
  void master
  return (
    <Modal open onClose={onClose} title={`盘点/核数：${row.sku_name} @ ${row.warehouse}${lot ? ` · ${lot.lot_code}` : ''}`} footer={<button className="btn-primary" disabled={qty === '' || !reason} onClick={save}>{t('common.submit')}</button>}>
      <p className="mb-3 text-sm text-slate-600">账面实物 {num(lot ? lot.on_hand : row.on_hand)}，未核数 {num(lot ? lot.unverified : row.unverified)}。提交后未核数清零，实盘量成为实物库存，差异形成调整流水。</p>
      <Field label="实盘数量"><input className="input text-lg" type="number" inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} /></Field>
      <div className="mt-3"><Field label="原因 / 盘点说明 *"><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></Field></div>
    </Modal>
  )
}

function IssueDialog({ row, lot, onClose, onDone }: any) {
  const toast = useToast()
  const [key] = useState(uuid())
  const [f, setF] = useState<any>({ qty: '', order_ref: '', customer: '' })
  const save = async () => {
    try {
      const r = await post('/api/inv/issue', { idempotency_key: key, sku_id: row.sku_id, warehouse_id: row.warehouse_id, lot_id: lot?.lot_id || null, qty: Number(f.qty), order_ref: f.order_ref, customer: f.customer })
      toast(r.duplicate ? '重复提交已拦截' : '已出库'); onDone(); onClose()
    } catch (e) { toast(errMsg(e), 'err') }
  }
  return (
    <Modal open onClose={onClose} title={`出库：${row.sku_name}${lot ? ` · ${lot.lot_code}` : ''}`} footer={<button className="btn-primary" disabled={!f.qty} onClick={save}>{t('common.submit')}</button>}>
      <div className="space-y-3">
        <Field label={t('inv.qty')}><input className="input text-lg" type="number" value={f.qty} onChange={(e) => setF({ ...f, qty: e.target.value })} /></Field>
        <Field label="订单号"><input className="input" value={f.order_ref} onChange={(e) => setF({ ...f, order_ref: e.target.value })} /></Field>
        <Field label="客户"><input className="input" value={f.customer} onChange={(e) => setF({ ...f, customer: e.target.value })} /></Field>
      </div>
    </Modal>
  )
}

export function StockModule(_: ModProps) {
  const { can } = useAuth()
  const [params, setParams] = useSearchParams()
  const skuParam = params.get('sku')
  const master = useMaster()
  const [wh, setWh] = useState('')
  const stock = useLoad(() => get(`/api/inv/stock${wh ? `?warehouse_id=${wh}` : ''}`), [wh])
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [dlg, setDlg] = useState<any>(null)
  const [cat, setCat] = useState(() => { try { return localStorage.getItem('stock.cat') || '' } catch { return '' } })
  if (stock.error) return <ErrorBox error={stock.error} />
  if (!stock.data || !master.data) return <Spinner />
  const reload = () => { stock.reload(); master.reload() }
  const skuOf = (id: string) => master.data.skus.find((s: any) => s.id === id)
  const catOf = (r: any) => r.sku_category || UNCAT
  const counts: Record<string, number> = {}
  for (const id of new Set(stock.data.rows.map((r: any) => r.sku_id))) { const c = skuOf(id as string)?.category || UNCAT; counts[c] = (counts[c] || 0) + 1 }
  const pickCat = (c: string) => { setCat(c); try { localStorage.setItem('stock.cat', c) } catch { /* 私密模式 */ } }
  const curCat = cat && counts[cat] ? cat : ''
  const rows = stock.data.rows.filter((r: any) => !curCat || catOf(r) === curCat)
  const todo = master.data.skus.filter((s: any) => !s.category || s.category === UNCAT).length
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <select className="input w-auto min-w-0 flex-1 sm:flex-none" value={wh} onChange={(e) => setWh(e.target.value)}>
          <option value="">全部仓库</option>{master.data.warehouses.filter((w: any) => w.tracked).map((w: any) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </select>
        {can('inventory.post') && <button className="btn-primary" onClick={() => setDlg({ type: 'receive' })}><PackageCheck size={16} />{t('inv.receive')}</button>}
        {can('inventory.master') && <button className="btn-secondary" onClick={() => setDlg({ type: 'warehouses' })}><Warehouse size={16} />仓库与来源地</button>}
        <button className="btn-secondary" onClick={() => setDlg({ type: 'aliases' })}><Tags size={16} />商品</button>
        {can('inventory.master') && <button className="btn-secondary" onClick={() => setDlg({ type: 'classify' })}>整理分类</button>}
        <span className="w-full text-xs text-slate-400 sm:ml-auto sm:w-auto">计算于 {ago(stock.data.computed_at)} · 可用 = 实物 − 隔离/待放行 − 预留；未核数不计入</span>
      </div>
      {todo > 0 && can('inventory.master') && (
        <button className="mb-3 flex min-h-11 w-full items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 text-left text-sm text-amber-800" onClick={() => setDlg({ type: 'classify' })}>
          <span className="flex-1">有 <b>{todo}</b> 个商品还没分类</span><span className="font-medium">去分类 →</span>
        </button>
      )}
      <CategoryChips counts={counts} value={curCat} onChange={pickCat} />
      {/* phones: one card per SKU × warehouse, available qty first */}
      <div className="space-y-2 sm:hidden">
        {rows.map((r: any) => {
          const k = `${r.sku_id}|${r.warehouse_id}`
          const chip = (label: string, v: any, cls = '') => <span className={`rounded bg-slate-50 px-2 py-1 ${cls}`}>{label} <b>{num(v)}</b></span>
          return (
            <div key={k} className={`rounded-lg border p-3 ${r.below_safety ? 'border-red-200 bg-red-50/40' : 'border-slate-200'}`}>
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <div className="font-medium">{r.sku_name}</div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-slate-500"><CatBadge c={catOf(r)} /><span>{r.warehouse}{r.warehouse_kind === 'external' && ' · 外库'}</span></div>
                </div>
                <div className="text-right">
                  <div className={`text-2xl font-bold leading-none ${r.below_safety ? 'text-red-600' : ''}`}>{num(r.available)}</div>
                  <div className="text-[11px] text-slate-400">可用 {r.unit}{r.safety_stock != null && ` · 安全 ${r.safety_stock}`}</div>
                </div>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5 text-xs">
                {chip('实物', r.on_hand)}
                {r.blocked > 0 && chip('隔离/待放行', r.blocked, 'text-red-600')}
                {r.reserved > 0 && chip('预留', r.reserved)}
                {r.in_transit > 0 && chip('在途', r.in_transit, 'text-sky-700')}
                {r.unverified > 0 && chip('未核数', r.unverified, 'text-amber-700')}
              </div>
              <div className="mt-2 flex gap-2">
                {r.lots.length > 0 && <button className="btn-ghost btn-sm" onClick={() => setOpen({ ...open, [k]: !open[k] })}>{open[k] ? <ChevronDown size={14} /> : <ChevronRight size={14} />}批次 {r.lots.length}</button>}
                {can('inventory.post') && <button className="btn-secondary btn-sm ml-auto" onClick={() => setDlg({ type: 'count', row: r })}>盘点</button>}
              </div>
              {open[k] && (
                <div className="mt-2 space-y-2 border-t pt-2">
                  {r.lots.map((l: any) => (
                    <div key={l.lot_id || 'none'} className="text-sm">
                      <div className="flex flex-wrap items-center gap-1.5">Lot <b>{l.lot_code}</b><Badge color={LOT_COLOR[l.quality_status]}>{LOT_LABEL[l.quality_status]}</Badge>{l.expiry && <span className="text-xs text-slate-400">EXP {l.expiry}</span>}</div>
                      <div className="text-xs text-slate-500">实物 {num(l.on_hand)}{l.in_transit ? ` · 在途 ${num(l.in_transit)}` : ''}{l.unverified ? ` · 未核数 ${num(l.unverified)}` : ''}</div>
                      <div className="mt-1 flex gap-1">
                        {l.lot_id && <button className="btn-ghost btn-sm" onClick={() => setDlg({ type: 'label', sku: skuOf(r.sku_id), lot: l })}><Printer size={14} />标签</button>}
                        {can('inventory.post') && <button className="btn-ghost btn-sm" onClick={() => setDlg({ type: 'count', row: r, lot: l })}>核数</button>}
                        {can('inventory.post') && l.on_hand > 0 && <button className="btn-ghost btn-sm" onClick={() => setDlg({ type: 'issue', row: r, lot: l })}>出库</button>}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
      <div className="-mx-4 hidden overflow-x-auto sm:block">
        <table className="min-w-full">
          <thead className="bg-slate-50"><tr>
            <th className="th"></th><th className="th">SKU</th><th className="th">仓库</th><th className="th text-right">实物</th><th className="th text-right">隔离/待放行</th>
            <th className="th text-right">预留</th><th className="th text-right">可用</th><th className="th text-right">安全库存</th><th className="th text-right">在途</th><th className="th text-right">未核数</th><th className="th"></th>
          </tr></thead>
          <tbody className="divide-y">
            {rows.map((r: any) => {
              const k = `${r.sku_id}|${r.warehouse_id}`
              return (
                <Fragment key={k}>
                  <tr className={r.below_safety ? 'bg-red-50/50' : ''}>
                    <td className="td"><button className="text-slate-400" onClick={() => setOpen({ ...open, [k]: !open[k] })}>{open[k] ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</button></td>
                    <td className="td"><div className="font-medium">{r.sku_name}</div><div className="mt-0.5 flex items-center gap-1.5 text-xs text-slate-500"><CatBadge c={catOf(r)} />{r.sku_code}</div></td>
                    <td className="td whitespace-nowrap">{r.warehouse}{r.warehouse_kind === 'external' && <Badge color="purple">外库</Badge>}</td>
                    <td className="td text-right">{num(r.on_hand)}</td>
                    <td className="td text-right text-red-600">{r.blocked ? num(r.blocked) : '—'}</td>
                    <td className="td text-right">{r.reserved ? num(r.reserved) : '—'}</td>
                    <td className={`td text-right font-bold ${r.below_safety ? 'text-red-600' : ''}`}>{num(r.available)} <span className="text-xs font-normal text-slate-400">{r.unit}</span></td>
                    <td className="td text-right">{r.safety_stock ?? '—'}</td>
                    <td className="td text-right">{r.in_transit ? num(r.in_transit) : '—'}</td>
                    <td className="td text-right text-amber-600">{r.unverified ? num(r.unverified) : '—'}</td>
                    <td className="td whitespace-nowrap">
                      {can('inventory.post') && <button className="btn-ghost btn-sm" onClick={() => setDlg({ type: 'count', row: r })}>盘点</button>}
                    </td>
                  </tr>
                  {open[k] && r.lots.map((l: any) => (
                    <tr key={k + l.lot_id} className="bg-slate-50/60 text-xs">
                      <td></td>
                      <td className="td pl-6" colSpan={2}>Lot <b>{l.lot_code}</b> <Badge color={LOT_COLOR[l.quality_status]}>{LOT_LABEL[l.quality_status]}</Badge>{l.expiry && <span className="ml-1 text-slate-400">EXP {l.expiry}</span>}</td>
                      <td className="td text-right">{num(l.on_hand)}</td><td></td><td></td><td></td><td></td>
                      <td className="td text-right">{l.in_transit ? num(l.in_transit) : '—'}</td>
                      <td className="td text-right">{l.unverified ? num(l.unverified) : '—'}</td>
                      <td className="td whitespace-nowrap">
                        {l.lot_id && <button className="btn-ghost btn-sm" onClick={() => setDlg({ type: 'label', sku: skuOf(r.sku_id), lot: l })}><Printer size={12} /></button>}
                        {can('inventory.post') && <button className="btn-ghost btn-sm" onClick={() => setDlg({ type: 'count', row: r, lot: l })}>核数</button>}
                        {can('inventory.post') && l.on_hand > 0 && <button className="btn-ghost btn-sm" onClick={() => setDlg({ type: 'issue', row: r, lot: l })}>出库</button>}
                      </td>
                    </tr>
                  ))}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
      {!rows.length && <Empty />}
      {dlg?.type === 'classify' && <ClassifyDialog skus={master.data.skus} onClose={() => setDlg(null)} onDone={reload} />}
      {dlg?.type === 'receive' && <ReceiveDialog master={master.data} onClose={() => setDlg(null)} onDone={reload} />}
      {dlg?.type === 'count' && <CountDialog master={master.data} row={dlg.row} lot={dlg.lot} onClose={() => setDlg(null)} onDone={reload} />}
      {dlg?.type === 'issue' && <IssueDialog row={dlg.row} lot={dlg.lot} onClose={() => setDlg(null)} onDone={reload} />}
      {dlg?.type === 'label' && <LotLabel sku={dlg.sku} lot={dlg.lot} onClose={() => setDlg(null)} />}
      {(dlg?.type === 'aliases' || skuParam) && <AliasManager skuId={skuParam || undefined} onClose={() => { setDlg(null); if (skuParam) setParams((p) => { p.delete('sku'); return p }, { replace: true }) }} />}
      {dlg?.type === 'warehouses' && <Warehouses list={master.data.warehouses} onClose={() => setDlg(null)} onChanged={reload} />}
    </div>
  )
}

const WH_KIND: Record<string, string> = { main: '主场地', external: '外库（第三方）', company: '公司其他地点' }

function Warehouses({ list, onClose, onChanged }: { list: any[]; onClose: () => void; onChanged: () => void }) {
  const toast = useToast()
  const [f, setF] = useState<any>({ code: '', name: '', kind: 'company', tracked: false })
  const add = async () => {
    try { await post('/api/inv/warehouses', f); setF({ code: '', name: '', kind: 'company', tracked: false }); onChanged() } catch (e) { toast(errMsg(e), 'err') }
  }
  return (
    <Modal open onClose={onClose} title="仓库与来源地">
      <p className="mb-3 text-sm text-slate-600">「在本系统管账」的仓库会记录库存；多伦多、温哥华这类只作为货物来源、库存不在这里管的地点，选「不管账」——调入时不会检查或扣减它们的库存。</p>
      <div className="space-y-2">
        {list.map((w) => (
          <div key={w.id} className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 p-2 text-sm">
            <span className="font-medium">{w.name}</span><span className="text-xs text-slate-400">{w.code} · {WH_KIND[w.kind] || w.kind}</span>
            <Badge color={w.tracked ? 'green' : 'gray'}>{w.tracked ? '在本系统管账' : '不管账（仅来源地）'}</Badge>
          </div>
        ))}
      </div>
      <div className="mt-4 space-y-2 rounded-lg bg-slate-50 p-3">
        <div className="text-sm font-semibold">新增</div>
        <div className="grid grid-cols-2 gap-2">
          <input className="input" placeholder="编码，如 MTL" value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} />
          <input className="input" placeholder="名称，如 蒙特利尔（公司）" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
          <select className="input" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>{Object.entries(WH_KIND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
          <select className="input" value={f.tracked ? '1' : '0'} onChange={(e) => setF({ ...f, tracked: e.target.value === '1' })}><option value="0">不管账（仅来源地）</option><option value="1">在本系统管账</option></select>
        </div>
        <button className="btn-primary w-full" disabled={!f.code || !f.name} onClick={add}>添加</button>
      </div>
    </Modal>
  )
}

// ---------- transfers ----------
const TR_LABEL: Record<string, [string, string]> = { requested: ['已申请', 'gray'], shipped: ['已发运', 'blue'], partial: ['部分签收', 'yellow'], received: ['已签收', 'green'], cancelled: ['取消', 'gray'] }

function NewTransfer({ master, onClose, onDone }: any) {
  const toast = useToast()
  const firstUntracked = master.warehouses.find((w: any) => !w.tracked && w.active)
  const [f, setF] = useState<any>({ from_wh: (firstUntracked || master.warehouses.find((w: any) => w.kind === 'external'))?.id || '', to_wh: master.warehouses.find((w: any) => w.kind === 'main')?.id || '', expected_date: '', note: '', shipped_now: !!firstUntracked, lines: [{ sku_id: '', lot_id: '', lot_code: '', qty: '' }] })
  const src = master.warehouses.find((w: any) => w.id === f.from_wh)
  const inbound = src && !src.tracked
  const save = async () => {
    try {
      await post('/api/inv/transfers', { ...f, expected_date: f.expected_date || null, lines: f.lines.filter((l: any) => l.sku_id && l.qty).map((l: any) => ({ sku_id: l.sku_id, lot_id: inbound ? null : (l.lot_id || null), lot_code: inbound ? (l.lot_code || null) : null, qty: Number(l.qty) })) })
      onDone(); onClose()
    } catch (e) { toast(errMsg(e), 'err') }
  }
  const setLine = (i: number, p: any) => setF({ ...f, lines: f.lines.map((l: any, j: number) => (i === j ? { ...l, ...p } : l)) })
  return (
    <Modal open onClose={onClose} title={inbound ? `新建调入（来自 ${src.name}）` : '新建调拨'} wide footer={<button className="btn-primary" onClick={save}>{t('common.save')}</button>}>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="从哪里来"><select className="input" value={f.from_wh} onChange={(e) => { const w = master.warehouses.find((x: any) => x.id === e.target.value); setF({ ...f, from_wh: e.target.value, shipped_now: w && !w.tracked }) }}>
          {master.warehouses.filter((w: any) => w.active).map((w: any) => <option key={w.id} value={w.id}>{w.name}{w.tracked ? '' : '（来源地）'}</option>)}</select></Field>
        <Field label="到哪里"><select className="input" value={f.to_wh} onChange={(e) => setF({ ...f, to_wh: e.target.value })}>{master.warehouses.filter((w: any) => w.tracked && w.active).map((w: any) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></Field>
        <Field label="预计到货"><input type="date" className="input" value={f.expected_date} onChange={(e) => setF({ ...f, expected_date: e.target.value })} /></Field>
      </div>
      <div className="mt-3 space-y-2">
        {f.lines.map((l: any, i: number) => (
          <div key={i} className="grid grid-cols-2 gap-2 rounded-lg border border-slate-100 p-2 sm:grid-cols-[2fr_1.5fr_1fr] sm:border-0 sm:p-0 [&>select:first-child]:col-span-2 sm:[&>select:first-child]:col-span-1">
            <select className="input" value={l.sku_id} onChange={(e) => setLine(i, { sku_id: e.target.value, lot_id: '' })}><option value="">SKU</option>{master.skus.map((s: any) => <option key={s.id} value={s.id}>{skuLabel(s)}</option>)}</select>
            {inbound
              ? <label><span className="label sm:hidden">Lot</span><input className="input" placeholder="Lot（按对方单据填写）" value={l.lot_code} onChange={(e) => setLine(i, { lot_code: e.target.value })} /></label>
              : <select className="input" value={l.lot_id} onChange={(e) => setLine(i, { lot_id: e.target.value })}><option value="">(无批次)</option>{master.lots.filter((x: any) => x.sku_id === l.sku_id).map((x: any) => <option key={x.id} value={x.id}>{x.lot_code}</option>)}</select>}
            <label><span className="label sm:hidden">数量</span><input className="input" type="number" inputMode="decimal" placeholder="数量" value={l.qty} onChange={(e) => setLine(i, { qty: e.target.value })} /></label>
          </div>
        ))}
        <button className="btn-ghost btn-sm" onClick={() => setF({ ...f, lines: [...f.lines, { sku_id: '', lot_id: '', lot_code: '', qty: '' }] })}><Plus size={14} />加一行</button>
      </div>
      <Field label="备注（车次、柜号、BOL 等）"><input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
      <label className="mt-3 flex items-center gap-2 text-sm"><input type="checkbox" checked={f.shipped_now} onChange={(e) => setF({ ...f, shipped_now: e.target.checked })} />对方已经发出：按单据数量直接记为「在途」</label>
      <p className="mt-2 text-xs text-slate-500">{inbound
        ? `${src.name} 不在本系统管账：不会检查或扣减那边的库存，只在目的地记在途；到货时现场签收，短少/破损自动生成差异任务。装箱单、BOL、报关单可在调拨单里上传。`
        : '调拨申请不改变库存；实际发运才从来源仓扣减并进入在途。'}</p>
    </Modal>
  )
}

function TransferDetail({ id, onClose, onChanged }: any) {
  const { can } = useAuth()
  const toast = useToast()
  const { data: tr, reload } = useLoad(() => get(`/api/inv/transfers/${id}`), [id])
  const [mode, setMode] = useState<'' | 'ship' | 'receive'>('')
  const [qty, setQty] = useState<Record<string, any>>({})
  const [key, setKey] = useState(uuid())
  if (!tr) return null
  const act = async (reason = '') => {
    try {
      const body = mode === 'ship'
        ? { idempotency_key: key, shortfall_reason: reason, lines: tr.lines.map((l: any) => ({ line_id: l.id, qty: Number(qty[l.id]?.q || 0) })) }
        : { idempotency_key: key, lines: tr.lines.filter((l: any) => l.in_transit > 0).map((l: any) => ({ line_id: l.id, qty_received: Number(qty[l.id]?.q || 0), qty_damaged: Number(qty[l.id]?.d || 0) })) }
      const r = await post(`/api/inv/transfers/${id}/${mode}`, body)
      toast(r.duplicate ? '重复提交已拦截，未重复过账' : mode === 'ship' ? '已发运，进入在途' : `已签收（${TR_LABEL[r.transfer.status][0]}）`)
      setMode(''); setQty({}); setKey(uuid()); reload(); onChanged()
    } catch (e) {
      // 外库账面不够（期初没录全）：例外，写原因按单据补记
      if (e instanceof ApiError && e.detail?.code === 'source_short') {
        const r = prompt(`${e.message}\n\n补记原因：`)
        if (r?.trim()) return act(r.trim())
        return
      }
      toast(errMsg(e), 'err')
    }
  }
  const writeOff = async (l: any) => {
    const reason = prompt(`核销在途 ${l.in_transit}（短少/损坏）原因：`)
    if (!reason) return
    try { await post(`/api/inv/transfers/${id}/write-off`, { idempotency_key: uuid(), line_id: l.id, qty: l.in_transit, reason }); reload(); onChanged() } catch (e) { toast(errMsg(e), 'err') }
  }
  return (
    <Modal open onClose={onClose} wide title={<span>{tr.code} <Badge color={TR_LABEL[tr.status][1]}>{TR_LABEL[tr.status][0]}</Badge></span>}>
      <div className="mb-3 text-sm text-slate-600">{tr.from_name} → {tr.to_name} · 预计 {tr.expected_date || '未定'} {tr.note && `· ${tr.note}`}</div>
      <div className="space-y-2 sm:hidden">
        {tr.lines.map((l: any) => (
          <div key={l.id} className="rounded-lg border border-slate-200 p-3 text-sm">
            <div className="font-medium">{l.sku_code} <span className="text-xs font-normal text-slate-500">{l.sku_name} {l.lot_code && `· Lot ${l.lot_code}`}</span></div>
            <div className="mt-1 grid grid-cols-5 gap-1 text-center text-xs">
              {[['申请', l.qty_requested], ['已发', l.qty_shipped], ['已收', l.qty_received], ['损坏', l.qty_damaged], ['在途', l.in_transit]].map(([k, v]: any) => (
                <div key={k} className="rounded bg-slate-50 py-1"><div className="text-slate-400">{k}</div><div className={`font-semibold ${k === '在途' && v > 0 ? 'text-sky-700' : ''}`}>{num(v)}</div></div>
              ))}
            </div>
            {mode === 'ship' && <label className="mt-2 block"><span className="label">本次发运</span><input className="input text-lg" type="number" inputMode="decimal" value={qty[l.id]?.q ?? Math.max(l.qty_requested - l.qty_shipped, 0)} onChange={(e) => setQty({ ...qty, [l.id]: { q: e.target.value } })} /></label>}
            {mode === 'receive' && l.in_transit > 0 && (
              <div className="mt-2 grid grid-cols-2 gap-2">
                <label><span className="label">本次实收</span><input className="input text-lg" type="number" inputMode="decimal" placeholder="实收" value={qty[l.id]?.q ?? ''} onChange={(e) => setQty({ ...qty, [l.id]: { ...qty[l.id], q: e.target.value } })} /></label>
                <label><span className="label">损坏</span><input className="input text-lg" type="number" inputMode="decimal" placeholder="损坏" value={qty[l.id]?.d ?? ''} onChange={(e) => setQty({ ...qty, [l.id]: { ...qty[l.id], d: e.target.value } })} /></label>
              </div>
            )}
            {l.in_transit > 0 && tr.status === 'partial' && can('inventory.post') && !mode && <button className="btn-secondary btn-sm mt-2" onClick={() => writeOff(l)}>核销差异</button>}
          </div>
        ))}
      </div>
      <div className="-mx-4 hidden overflow-x-auto sm:block">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-50"><tr><th className="th">SKU / Lot</th><th className="th text-right">申请</th><th className="th text-right">已发</th><th className="th text-right">已收</th><th className="th text-right">损坏</th><th className="th text-right">在途</th>{mode && <th className="th">{mode === 'ship' ? '本次发运' : '本次实收 / 损坏'}</th>}<th className="th"></th></tr></thead>
          <tbody className="divide-y">
            {tr.lines.map((l: any) => (
              <tr key={l.id}>
                <td className="td">{l.sku_code} <span className="text-xs text-slate-500">{l.lot_code || ''}</span></td>
                <td className="td text-right">{num(l.qty_requested)}</td><td className="td text-right">{num(l.qty_shipped)}</td>
                <td className="td text-right">{num(l.qty_received)}</td><td className="td text-right">{l.qty_damaged ? num(l.qty_damaged) : '—'}</td>
                <td className="td text-right font-semibold">{num(l.in_transit)}</td>
                {mode === 'ship' && <td className="td"><input className="input w-24" type="number" value={qty[l.id]?.q ?? Math.max(l.qty_requested - l.qty_shipped, 0)} onChange={(e) => setQty({ ...qty, [l.id]: { q: e.target.value } })} /></td>}
                {mode === 'receive' && <td className="td">{l.in_transit > 0 && <div className="flex gap-1"><input className="input w-20" type="number" placeholder="实收" value={qty[l.id]?.q ?? ''} onChange={(e) => setQty({ ...qty, [l.id]: { ...qty[l.id], q: e.target.value } })} /><input className="input w-20" type="number" placeholder="损坏" value={qty[l.id]?.d ?? ''} onChange={(e) => setQty({ ...qty, [l.id]: { ...qty[l.id], d: e.target.value } })} /></div>}</td>}
                <td className="td">{l.in_transit > 0 && ['partial'].includes(tr.status) && can('inventory.post') && <button className="btn-ghost btn-sm" onClick={() => writeOff(l)}>核销差异</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="sticky bottom-0 -mx-4 mt-3 flex flex-wrap gap-2 border-t bg-white px-4 py-2 sm:static sm:mx-0 sm:border-0 sm:p-0 [&>button]:flex-1 sm:[&>button]:flex-none">
        {!mode && ['requested', 'shipped', 'partial'].includes(tr.status) && can('inventory.transfer') && tr.lines.some((l: any) => l.qty_requested > l.qty_shipped) &&
          <button className="btn-secondary" onClick={() => { setMode('ship'); setQty(Object.fromEntries(tr.lines.map((l: any) => [l.id, { q: Math.max(l.qty_requested - l.qty_shipped, 0) }]))) }}><Truck size={16} />记录实际发运</button>}
        {!mode && ['shipped', 'partial'].includes(tr.status) && can('inventory.post') && <button className="btn-primary" onClick={() => setMode('receive')}><PackageCheck size={16} />现场签收</button>}
        {mode && <><button className="btn-primary" onClick={() => act()}>{t('common.confirm')}</button><button className="btn-ghost" onClick={() => setMode('')}>{t('common.cancel')}</button></>}
      </div>
      {mode === 'receive' && <p className="mt-2 text-xs text-slate-500">部分签收保留剩余在途；短缺与损坏自动生成差异任务。重复点击不会重复过账。</p>}
      <div className="mt-5 grid gap-6 md:grid-cols-2">
        <EvidencePanel title="单据与照片" targetType="transfer" targetId={id} purposes={['BOL', '装箱单', '报关单', '外库确认', '签收单', '到货照片', '差异照片']} onChange={reload} />
        <div><div className="mb-2 text-sm font-semibold">沟通记录</div><Timeline targetType="transfer" targetId={id} /></div>
      </div>
    </Modal>
  )
}

export function TransfersModule(_: ModProps) {
  const { can } = useAuth()
  const master = useMaster()
  const list = useLoad(() => get('/api/inv/transfers'), [])
  const [newOpen, setNewOpen] = useState(false)
  const [detail, setDetail] = useState<string | null>(null)
  const scanTransfer = async (code: string) => {
    try { const r = await get(`/api/inv/scan/${encodeURIComponent(code)}`); if (r.type === 'transfer') setDetail(r.transfer_id) } catch { /* ignore */ }
  }
  if (!list.data || !master.data) return <Spinner />
  return (
    <div>
      <div className="mb-3 flex gap-2">
        {can('inventory.transfer') && <button className="btn-secondary" onClick={() => setNewOpen(true)}><Plus size={16} />新建调拨 / 调入</button>}
        <ScanButton label="扫调拨单" onResult={scanTransfer} />
      </div>
      <div className="space-y-2">
        {list.data.map((tr: any) => (
          <button key={tr.id} onClick={() => setDetail(tr.id)} className="flex w-full flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-slate-200 p-3 text-left text-sm hover:bg-slate-50 active:bg-slate-50">
            <span className="font-mono font-medium">{tr.code}</span>
            {tr.inbound && <Badge color="purple">调入</Badge>}
            <Badge color={TR_LABEL[tr.status][1]}>{TR_LABEL[tr.status][0]}</Badge>
            <span className="text-slate-500">{tr.from_name} → {tr.to_name}</span>
            <span className="text-xs text-slate-400">{tr.lines.map((l: any) => `${l.sku_code} ×${num(l.qty_requested)}`).join('，')}</span>
            <span className="text-xs text-slate-400 sm:ml-auto">预计 {tr.expected_date || '未定'}</span>
          </button>
        ))}
        {!list.data.length && <Empty />}
      </div>
      {newOpen && <NewTransfer master={master.data} onClose={() => setNewOpen(false)} onDone={list.reload} />}
      {detail && <TransferDetail id={detail} onClose={() => setDetail(null)} onChanged={list.reload} />}
    </div>
  )
}

// ---------- lots / quality ----------
function QualityDialog({ lot, action, onClose, onDone }: any) {
  const toast = useToast()
  const [reason, setReason] = useState('')
  const [basis, setBasis] = useState('')
  const events = useLoad(() => get(`/api/inv/lots/${lot.id}/quality-events`), [lot.id])
  const meaning = action === 'release' ? '我已审核依据，批准该批次放行' : '确认该批次存在质量疑问，暂停使用'
  const save = async () => {
    try {
      const r = await post(`/api/inv/lots/${lot.id}/${action}`, { reason, basis })
      if (r.affected_orders?.length) toast(`受影响订单：${r.affected_orders.map((o: any) => o.order_ref).join('、')}`, 'err')
      onDone(); onClose()
    } catch (e) { toast(errMsg(e), 'err') }
  }
  return (
    <Modal open onClose={onClose} title={`${action === 'release' ? '放行' : '隔离'}批次 ${lot.lot_code}`}
      footer={<button className={action === 'release' ? 'btn-primary' : 'btn-danger'} disabled={!reason || (action === 'release' && !basis)} onClick={save}>电子签名并{action === 'release' ? '放行' : '隔离'}</button>}>
      <div className="space-y-3">
        <Field label="理由 *"><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
        {action === 'release' && <Field label="依据 *（检验记录、整改证据等）"><input className="input" value={basis} onChange={(e) => setBasis(e.target.value)} /></Field>}
        <div className="rounded bg-slate-50 p-2 text-xs text-slate-600">签名含义：{meaning}。系统记录签名人、时间与含义，不可修改。</div>
        <EvidencePanel targetType="lot" targetId={lot.id} purposes={['检验记录', '整改证明', '批次标签', '隔离照片']} compact />
        {!!events.data?.length && (
          <div className="text-xs text-slate-600">
            <div className="mb-1 font-semibold">质量签核历史</div>
            {events.data.map((e: any, i: number) => <div key={i}>{dt(e.at)} {e.by} <b>{e.action === 'release' ? '放行' : '隔离'}</b>：{e.reason}{e.basis && `（依据：${e.basis}）`}</div>)}
          </div>
        )}
      </div>
    </Modal>
  )
}

export function LotsModule(_: ModProps) {
  const { can } = useAuth()
  const master = useMaster()
  const [filter, setFilter] = useState('blocked')
  const [dlg, setDlg] = useState<any>(null)
  if (!master.data) return <Spinner />
  const skus = Object.fromEntries(master.data.skus.map((s: any) => [s.id, s]))
  const lots = master.data.lots.filter((l: any) => filter === 'all' || (filter === 'blocked' ? l.quality_status !== 'released' : l.quality_status === filter))
  return (
    <div>
      <div className="mb-3 flex gap-2">
        <select className="input w-auto" value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="blocked">隔离 + 待放行</option><option value="all">全部批次</option><option value="released">已放行</option>
        </select>
      </div>
      <div className="space-y-2">
        {lots.map((l: any) => (
          <div key={l.id} className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 p-3 text-sm">
            <span className="font-medium">{l.lot_code}</span>
            <Badge color={LOT_COLOR[l.quality_status]}>{LOT_LABEL[l.quality_status]}</Badge>
            <span className="w-full text-slate-500 sm:w-auto">{skus[l.sku_id]?.code} {skus[l.sku_id]?.name}</span>
            <div className="flex w-full justify-end gap-1 sm:ml-auto sm:w-auto">
              <button className="btn-ghost btn-sm" onClick={() => setDlg({ type: 'label', lot: l })}><Printer size={14} /></button>
              {l.quality_status === 'released' && can('quality.quarantine') && <button className="btn-secondary btn-sm" onClick={() => setDlg({ type: 'quarantine', lot: l })}><ShieldAlert size={14} />隔离</button>}
              {l.quality_status !== 'released' && can('quality.release') && <button className="btn-primary btn-sm" onClick={() => setDlg({ type: 'release', lot: l })}><ShieldCheck size={14} />放行</button>}
              {l.quality_status !== 'released' && !can('quality.release') && <button className="btn-ghost btn-sm" onClick={() => setDlg({ type: 'quarantine_view', lot: l })}>查看</button>}
            </div>
          </div>
        ))}
        {!lots.length && <Empty>没有符合条件的批次</Empty>}
      </div>
      {dlg?.type === 'label' && <LotLabel sku={skus[dlg.lot.sku_id]} lot={dlg.lot} onClose={() => setDlg(null)} />}
      {(dlg?.type === 'quarantine' || dlg?.type === 'release') && <QualityDialog lot={dlg.lot} action={dlg.type} onClose={() => setDlg(null)} onDone={master.reload} />}
      {dlg?.type === 'quarantine_view' && <Modal open onClose={() => setDlg(null)} title={`批次 ${dlg.lot.lot_code}`}><EvidencePanel targetType="lot" targetId={dlg.lot.id} purposes={['隔离照片', '检验记录']} /></Modal>}
    </div>
  )
}

// ---------- production ----------
function NewBatch({ master, onClose, onDone }: any) {
  const toast = useToast()
  const [key] = useState(uuid())
  const [f, setF] = useState<any>({ product_sku_id: '', warehouse_id: master.warehouses.find((w: any) => w.kind === 'main')?.id, output_lot_code: '', output_qty: '', sop_ref: '', inputs: [{ lot_id: '', qty: '' }] })
  const products = master.skus.filter((s: any) => FOOD.has(s.category) || s.category === UNCAT)
  const rawLots = master.lots.filter((l: any) => l.sku_id !== f.product_sku_id)
  const skuName = (id: string) => master.skus.find((s: any) => s.id === id)?.name
  const save = async () => {
    try {
      const r = await post('/api/inv/batches', { ...f, idempotency_key: key, output_qty: Number(f.output_qty), inputs: f.inputs.filter((i: any) => i.lot_id && i.qty).map((i: any) => ({ lot_id: i.lot_id, qty: Number(i.qty) })) })
      toast(`${r.code ?? '批次'} 已完工，成品待质量放行`); onDone(); onClose()
    } catch (e) { toast(errMsg(e), 'err') }
  }
  return (
    <Modal open onClose={onClose} wide title="生产完工登记" footer={<button className="btn-primary" onClick={save}>{t('common.submit')}</button>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="成品 SKU"><select className="input" value={f.product_sku_id} onChange={(e) => setF({ ...f, product_sku_id: e.target.value })}><option value="">—</option>{products.map((s: any) => <option key={s.id} value={s.id}>{skuLabel(s)}</option>)}</select></Field>
        <Field label="成品批次号"><input className="input" value={f.output_lot_code} onChange={(e) => setF({ ...f, output_lot_code: e.target.value })} /></Field>
        <Field label="产出数量（箱）"><input className="input" type="number" value={f.output_qty} onChange={(e) => setF({ ...f, output_qty: e.target.value })} /></Field>
        <Field label="配方/SOP 版本"><input className="input" placeholder="例如 SOP-切肉 v3" value={f.sop_ref} onChange={(e) => setF({ ...f, sop_ref: e.target.value })} /></Field>
      </div>
      <div className="mt-3 text-sm font-semibold">原料投入（按批次）</div>
      <div className="mt-1 space-y-2">
        {f.inputs.map((i: any, idx: number) => (
          <div key={idx} className="grid grid-cols-[1fr_96px] gap-2">
            <select className="input" value={i.lot_id} onChange={(e) => setF({ ...f, inputs: f.inputs.map((x: any, j: number) => (j === idx ? { ...x, lot_id: e.target.value } : x)) })}>
              <option value="">选择原料批次</option>
              {rawLots.map((l: any) => <option key={l.id} value={l.id} disabled={l.quality_status !== 'released'}>{l.lot_code} · {skuName(l.sku_id)}{l.quality_status !== 'released' ? `（${LOT_LABEL[l.quality_status]}）` : ''}</option>)}
            </select>
            <input className="input" type="number" placeholder="数量" value={i.qty} onChange={(e) => setF({ ...f, inputs: f.inputs.map((x: any, j: number) => (j === idx ? { ...x, qty: e.target.value } : x)) })} />
          </div>
        ))}
        <button className="btn-ghost btn-sm" onClick={() => setF({ ...f, inputs: [...f.inputs, { lot_id: '', qty: '' }] })}><Plus size={14} />加原料</button>
      </div>
      <p className="mt-2 text-xs text-slate-500">生产完工不代表可发货：成品批次默认「待放行」，由质量负责人签核后才能分配或出库。</p>
    </Modal>
  )
}

export function ProductionModule(_: ModProps) {
  const { can } = useAuth()
  const master = useMaster()
  const list = useLoad(() => get('/api/inv/batches'), [])
  const [open, setOpen] = useState(false)
  if (!list.data || !master.data) return <Spinner />
  return (
    <div>
      {can('production.run') && <button className="btn-primary mb-3" onClick={() => setOpen(true)}><Plus size={16} />生产完工登记</button>}
      <div className="space-y-2">
        {list.data.map((b: any) => (
          <div key={b.id} className="rounded-lg border border-slate-100 p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono font-medium">{b.code}</span><span>{b.product}</span><span>×{num(b.output_qty)}</span>
              <span>Lot {b.output_lot}</span><Badge color={LOT_COLOR[b.quality_status]}>{LOT_LABEL[b.quality_status]}</Badge>
              <span className="ml-auto text-xs text-slate-400">{dt(b.created_at)}</span>
            </div>
            <div className="mt-1 text-xs text-slate-500">原料：{b.inputs.map((i: any) => `${i.sku} ${i.lot_code} ×${num(i.qty)}`).join('，')}{b.sop_ref && ` · ${b.sop_ref}`}</div>
          </div>
        ))}
        {!list.data.length && <Empty>还没有生产批次</Empty>}
      </div>
      {open && <NewBatch master={master.data} onClose={() => setOpen(false)} onDone={() => { list.reload(); master.reload() }} />}
    </div>
  )
}

// ---------- trace ----------
export function TraceModule(_: ModProps) {
  const master = useMaster()
  const [lotId, setLotId] = useState('')
  const trace = useLoad(() => (lotId ? get(`/api/inv/trace/${lotId}`) : Promise.resolve(null)), [lotId])
  const scan = useScanResolver(master.data, () => {}, (lot) => setLotId(lot.lot_id || lot.id))
  if (!master.data) return <Spinner />
  const skus = Object.fromEntries(master.data.skus.map((s: any) => [s.id, s]))
  const exportCsv = () => {
    const d = trace.data
    const rows = [['方向', '层级', 'SKU', 'Lot', '质量状态', '去向类型', '单号', '客户/目的地', '数量', '时间']]
    d.backward.forEach((b: any) => rows.push(['反向(来源)', b.depth, b.sku_code, b.lot_code, b.quality_status, '投入', b.via_batch, '', b.qty, '']))
    d.forward.forEach((n: any) => {
      if (!n.shipments.length) rows.push(['正向', n.depth, n.sku_code, n.lot_code, n.quality_status, '', '', '', '', ''])
      n.shipments.forEach((s: any) => rows.push(['正向', n.depth, n.sku_code, n.lot_code, n.quality_status, s.type, s.order_ref || '', s.customer || '', s.qty, s.at]))
    })
    const csv = '﻿' + rows.map((r) => r.map((c) => `"${String(c ?? '').replace(/"/g, '""')}"`).join(',')).join('\n')
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
    a.download = `trace_${d.root.lot_code}.csv`
    a.click()
  }
  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-2">
        <select className="input w-full sm:max-w-sm" value={lotId} onChange={(e) => setLotId(e.target.value)}>
          <option value="">选择批次…</option>
          {master.data.lots.map((l: any) => <option key={l.id} value={l.id}>{l.lot_code} · {skus[l.sku_id]?.name}</option>)}
        </select>
        <ScanButton label="扫批次标签" onResult={scan} />
        {trace.data && <button className="btn-secondary" onClick={exportCsv}><Download size={16} />导出 CSV</button>}
      </div>
      {trace.error && <ErrorBox error={trace.error} />}
      {trace.data && (
        <div className="grid gap-4 md:grid-cols-2">
          <div>
            <div className="mb-1 text-sm font-semibold">反向：来源原料</div>
            {trace.data.backward.length ? trace.data.backward.map((b: any, i: number) => (
              <div key={i} className="text-sm" style={{ paddingLeft: b.depth * 12 }}>← {b.sku} <b>{b.lot_code}</b> ×{num(b.qty)} <span className="text-xs text-slate-400">经 {b.via_batch}{b.sop_ref && ` · ${b.sop_ref}`}</span></div>
            )) : <div className="text-xs text-slate-400">该批次不是生产产出（外购原料）</div>}
          </div>
          <div>
            <div className="mb-1 text-sm font-semibold">正向：去向</div>
            {trace.data.forward.map((n: any, i: number) => (
              <div key={i} className="mb-2 text-sm" style={{ paddingLeft: n.depth * 12 }}>
                <div>→ {n.sku} <b>{n.lot_code}</b> <Badge color={LOT_COLOR[n.quality_status]}>{LOT_LABEL[n.quality_status]}</Badge></div>
                {n.shipments.map((s: any, j: number) => <div key={j} className="pl-4 text-xs text-slate-600">{s.type} {s.order_ref} {s.customer} ×{num(s.qty)} <span className="text-slate-400">{dt(s.at)}</span></div>)}
              </div>
            ))}
          </div>
          <div className="text-xs text-slate-400 md:col-span-2">生成于 {dt(trace.data.generated_at, { site: true })}；查询已记入审计日志</div>
        </div>
      )}
    </div>
  )
}
