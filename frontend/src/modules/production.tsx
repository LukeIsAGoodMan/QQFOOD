import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Factory, Plus, ShoppingCart, Trash2 } from 'lucide-react'
import { get, patch, post, put, uuid } from '../lib/api'
import { useAuth } from '../lib/auth'
import { dt, num, skuLabel } from '../lib/fmt'
import { Badge, Empty, Field, LOT_COLOR, LOT_LABEL, Modal, Segmented, Spinner, errMsg, useLoad, useToast } from '../components/ui'
import { BoxLabels } from './orders'
import type { ModProps } from './basic'
import { FOOD, UNCAT } from './categories'

const u = (x?: string) => (x === '磅' ? 'lb' : x || '')

// ---------------- run dialog ----------------
function RunDialog({ init, setup, onClose, onDone }: { init: { sku_id?: string; target?: number; order_id?: string }; setup: any; onClose: () => void; onDone: (boxes: any[]) => void }) {
  const { can } = useAuth()
  const toast = useToast()
  const [key] = useState(uuid())
  const [skuId, setSkuId] = useState(init.sku_id || '')
  const out = setup.skus.find((s: any) => s.id === skuId)
  const recipe = setup.recipes.find((r: any) => r.output_sku_id === skuId)
  const rawId = recipe?.lines[0]?.sku_id || ''
  const [inputSku, setInputSku] = useState(rawId)
  useEffect(() => setInputSku(rawId), [rawId])
  const lots = useLoad(() => (inputSku ? get(`/api/production/inputs/${inputSku}`) : Promise.resolve([])), [inputSku])
  const [pick, setPick] = useState<Record<string, string>>({})  // lot key -> qty (keep mode)
  const [chosen, setChosen] = useState<Set<string>>(new Set())
  useEffect(() => { if (lots.data?.length) setChosen(new Set([String(lots.data[0].lot_id)])) }, [lots.data])
  const [leftover, setLeftover] = useState<'loss' | 'keep'>('loss')
  const stamp = new Date().toISOString().slice(2, 10).replace(/-/g, '')
  const [lotCode, setLotCode] = useState('')
  useEffect(() => { if (out) setLotCode(`${out.code.replace(/^(FG|RM)-/, '').replace(/-/g, '')}${stamp}`) }, [out?.code])  // eslint-disable-line react-hooks/exhaustive-deps
  const n = out?.ship_pack && init.target ? Math.ceil(init.target / out.ship_pack) : 1
  const [weights, setWeights] = useState<string[]>(Array(n).fill(''))
  useEffect(() => setWeights(Array(out?.ship_pack && init.target ? Math.ceil(init.target / out.ship_pack) : 1).fill('')), [skuId])  // eslint-disable-line react-hooks/exhaustive-deps
  const [outQty, setOutQty] = useState('')
  const [release, setRelease] = useState(false)
  const [reason, setReason] = useState('切配修整损耗')
  const catchW = out?.catch_weight
  const consumed = (lots.data || []).filter((l: any) => chosen.has(String(l.lot_id))).reduce((a: number, l: any) => a + (leftover === 'loss' ? l.qty : Number(pick[String(l.lot_id)] || 0)), 0)
  const produced = catchW ? weights.reduce((a, w) => a + (Number(w) || 0), 0) : Number(outQty) || 0
  const loss = consumed - produced
  const yld = consumed ? (100 * produced) / consumed : 0
  const expected = recipe?.expected_yield_pct
  const alert = expected && consumed && produced && expected - yld >= 5
  const submit = async () => {
    try {
      const r = await post('/api/production/runs', {
        idempotency_key: key, output_sku_id: skuId, lot_code: lotCode, order_id: init.order_id || null, leftover, loss_reason: reason, release,
        inputs: (lots.data || []).filter((l: any) => chosen.has(String(l.lot_id))).map((l: any) => ({ lot_id: l.lot_id, sku_id: inputSku, qty: leftover === 'keep' ? Number(pick[String(l.lot_id)] || 0) : null })),
        box_weights: catchW ? weights.map(Number).filter((w) => w > 0) : [], output_qty: catchW ? null : Number(outQty),
      })
      toast(`${r.run.code}：产出 ${num(r.run.output_qty)}，损耗 ${num(r.run.loss_qty)}（出成率 ${r.run.yield_pct}%）`)
      onDone(r.boxes)
    } catch (e) { toast(errMsg(e), 'err') }
  }
  return (
    <Modal open onClose={onClose} wide title="生产完工（切配 / 分包 / 腌制）" footer={<button className="btn-primary" disabled={!skuId || !produced || !consumed} onClick={submit}><Factory size={16} />完工并{catchW ? '打印标签' : '入库'}</button>}>
      <div className="space-y-4">
        <div className="grid gap-2 sm:grid-cols-3">
          <Field label="做的是什么（成品）"><select className="input" value={skuId} onChange={(e) => setSkuId(e.target.value)}><option value="">选择…</option>{setup.skus.filter((s: any) => FOOD.has(s.category) || s.category === UNCAT || setup.recipes.some((r: any) => r.output_sku_id === s.id)).map((s: any) => <option key={s.id} value={s.id}>{skuLabel(s)}</option>)}</select></Field>
          <Field label="成品批次号"><input className="input" value={lotCode} onChange={(e) => setLotCode(e.target.value)} /></Field>
          <Field label="用什么原料"><select className="input" value={inputSku} onChange={(e) => setInputSku(e.target.value)}><option value="">选择…</option>{setup.skus.filter((s: any) => s.id !== skuId).map((s: any) => <option key={s.id} value={s.id}>{skuLabel(s)}</option>)}</select></Field>
        </div>
        {init.target && <div className="rounded bg-sky-50 p-2 text-sm text-sky-800">订单需要 {num(init.target)} {u(out?.base_unit)}{out?.ship_pack && `，按 ${out.ship_pack} ${u(out.base_unit)}/箱 约 ${Math.ceil(init.target / out.ship_pack)} 箱`}</div>}
        <div>
          <div className="mb-1 text-sm font-semibold">投入原料批次</div>
          <div className="space-y-1.5">
            {(lots.data || []).map((l: any) => (
              <label key={String(l.lot_id)} className="flex items-center gap-2 rounded-lg border border-slate-200 p-2 text-sm">
                <input type="checkbox" checked={chosen.has(String(l.lot_id))} onChange={() => { const s = new Set(chosen); s.has(String(l.lot_id)) ? s.delete(String(l.lot_id)) : s.add(String(l.lot_id)); setChosen(s) }} />
                <span className="flex-1">{l.lot_code} {l.expiry && <span className="text-xs text-slate-400">EXP {l.expiry}</span>}</span>
                <span className="text-slate-600">库存 {num(l.qty)}</span>
                {leftover === 'keep' && chosen.has(String(l.lot_id)) && <input className="input w-24" type="number" inputMode="decimal" placeholder="用量" value={pick[String(l.lot_id)] || ''} onChange={(e) => setPick({ ...pick, [String(l.lot_id)]: e.target.value })} />}
              </label>
            ))}
            {lots.data && !lots.data.length && inputSku && <Empty>这个原料没有可用库存（被隔离/待放行的批次不能领用）</Empty>}
          </div>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm">
            <label className="flex min-h-[36px] items-center gap-1.5"><input type="radio" checked={leftover === 'loss'} onChange={() => setLeftover('loss')} />剩余原料不要，算生产损耗</label>
            <label className="flex min-h-[36px] items-center gap-1.5"><input type="radio" checked={leftover === 'keep'} onChange={() => setLeftover('keep')} />只扣用量，剩余留库存</label>
          </div>
        </div>
        {catchW ? (
          <div>
            <div className="mb-1 text-sm font-semibold">逐箱称重（{u(out?.base_unit)}）{out?.ship_pack && <span className="font-normal text-slate-400"> · 目标 {out.ship_pack}/箱；留空的格子不计</span>}</div>
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
              {weights.map((w, i) => (
                <input key={i} className="input text-lg" type="number" inputMode="decimal" step="0.01" placeholder={out?.ship_pack ? String(out.ship_pack) : `#${i + 1}`} value={w} onChange={(e) => setWeights(weights.map((x, j) => (i === j ? e.target.value : x)))} aria-label={`第 ${i + 1} 箱`} />
              ))}
              <button className="btn-secondary" onClick={() => setWeights([...weights, ''])}><Plus size={16} />一箱</button>
              {weights.length > 1 && <button className="btn-ghost" aria-label="减一箱" onClick={() => setWeights(weights.slice(0, -1))}><Trash2 size={16} />一箱</button>}
            </div>
          </div>
        ) : (
          <Field label={`产出数量（${u(out?.base_unit)}）`}><input className="input text-lg" type="number" inputMode="decimal" value={outQty} onChange={(e) => setOutQty(e.target.value)} /></Field>
        )}
        <div className={`grid grid-cols-4 gap-1 rounded-lg p-2 text-center text-xs ${alert ? 'bg-red-50' : 'bg-slate-50'}`}>
          <div><div className="text-slate-400">投入</div><div className="text-base font-bold">{num(consumed)}</div></div>
          <div><div className="text-slate-400">产出</div><div className="text-base font-bold">{num(produced)}</div></div>
          <div><div className="text-slate-400">损耗</div><div className={`text-base font-bold ${loss < 0 ? 'text-red-600' : ''}`}>{num(loss)}</div></div>
          <div><div className="text-slate-400">出成率</div><div className={`text-base font-bold ${alert ? 'text-red-600' : ''}`}>{consumed ? `${yld.toFixed(1)}%` : '—'}</div>{expected && <div className="text-[10px] text-slate-400">预计 {expected}%</div>}</div>
        </div>
        {alert && <div className="text-xs text-red-600">损耗明显高于预计，请检查切配或供应商箱子分量</div>}
        <Field label="损耗原因"><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
        {can('quality.release') && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={release} onChange={(e) => setRelease(e.target.checked)} />完工即放行（电子签名：我已检查本批次产品，批准放行）</label>}
        {!release && <p className="text-xs text-slate-500">不勾选则成品为「待放行」，放行前不能发货。</p>}
      </div>
    </Modal>
  )
}

// ---------------- plan ----------------
function Plan({ onProduce }: { onProduce: (init: any) => void }) {
  const toast = useToast()
  const nav = useNavigate()
  const { data, reload } = useLoad(() => get('/api/production/plan'), [])
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [supplier, setSupplier] = useState('')
  useEffect(() => { if (data) setSel(new Set(data.materials.filter((m: any) => m.short > 0).map((m: any) => m.sku.id))) }, [data])
  if (!data) return <Spinner />
  const buy = async () => {
    try {
      const items = data.materials.filter((m: any) => sel.has(m.sku.id) && m.suggest_qty > 0).map((m: any) => ({ sku_id: m.sku.id, qty: m.suggest_qty, cases: m.suggest_cases }))
      const r = await post('/api/production/plan/purchase', { supplier: supplier || '待定', items })
      toast(`已生成采购单草稿 ${r.code}，请补单价后提交`); nav(`/purchasing?po=${r.id}`)
    } catch (e) { toast(errMsg(e), 'err') }
  }
  return (
    <div className="space-y-4">
      <div>
        <div className="mb-2 text-sm font-semibold">要生产的（按未发完的订单）</div>
        {data.outputs.map((o: any) => (
          <div key={o.sku.id} className="mb-2 rounded-lg border border-slate-200 p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2"><b>{o.sku.name}</b><span className="text-xs text-slate-400">{o.sku.code}</span>{!o.has_recipe && <Badge color="yellow">未设出成率</Badge>}
              <span className="ml-auto">{o.to_make > 0 ? <>要做 <b className="text-lg">{num(o.to_make)}</b> {u(o.sku.base_unit)}{o.boxes_suggested && ` ≈ ${o.boxes_suggested} 箱`}</> : <span className="text-emerald-700">成品库存够</span>}</span></div>
            <div className="mt-1 text-xs text-slate-500">订单 {num(o.demand)} · 成品可用 {num(o.finished_available)} · {o.orders.map((x: any) => `PO ${x.po_no}（${x.required_date || '未定'}）`).join('、')}</div>
            {o.inputs.map((i: any) => <div key={i.sku_code} className="text-xs text-slate-500">原料 {i.sku_code} 需要 {num(i.need)}（出成率 {i.yield_pct}%）</div>)}
            {o.to_make > 0 && <button className="btn-secondary btn-sm mt-2" onClick={() => onProduce({ sku_id: o.sku.id, target: o.to_make, order_id: o.orders.length === 1 ? o.orders[0].order_id : undefined })}><Factory size={14} />去生产</button>}
          </div>
        ))}
        {!data.outputs.length && <Empty>没有待发的订单需求</Empty>}
      </div>
      <div>
        <div className="mb-2 text-sm font-semibold">原料备料（按供应商箱规向上取整）</div>
        {data.materials.map((m: any) => (
          <label key={m.sku.id} className={`mb-2 flex gap-3 rounded-lg border p-3 text-sm ${m.short > 0 ? 'border-amber-300' : 'border-slate-200'}`}>
            <input type="checkbox" className="mt-1" disabled={!m.short} checked={sel.has(m.sku.id)} onChange={() => { const s = new Set(sel); s.has(m.sku.id) ? s.delete(m.sku.id) : s.add(m.sku.id); setSel(s) }} />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2"><b>{m.sku.name}</b><span className="text-xs text-slate-400">用于 {m.for.join('、')}</span></div>
              <div className="text-xs text-slate-600">需要 {num(m.need)} · 库存 {num(m.available)} · 缺 <b>{num(m.short)}</b> {u(m.sku.base_unit)}</div>
              {m.short > 0 && <div className="mt-1">建议买 <b className="text-base">{m.suggest_cases ? `${m.suggest_cases} 箱 × ${m.sku.purchase_pack} = ` : ''}{num(m.suggest_qty)} {u(m.sku.base_unit)}</b>
                <span className="ml-1 text-xs text-slate-500">预计剩余 {num(m.expected_leftover)}（作损耗）</span></div>}
              {m.note && <div className="text-xs text-amber-700">{m.note}</div>}
            </div>
          </label>
        ))}
        {!data.materials.length && <Empty>没有需要备的原料（或成品还没设出成率）</Empty>}
        {data.materials.some((m: any) => m.short > 0) && (
          <div className="flex gap-2"><input className="input" placeholder="供应商（可空）" value={supplier} onChange={(e) => setSupplier(e.target.value)} />
            <button className="btn-primary shrink-0" disabled={!sel.size} onClick={buy}><ShoppingCart size={16} />生成采购单草稿</button></div>
        )}
      </div>
      <button className="btn-ghost btn-sm" onClick={reload}>重新计算</button>
    </div>
  )
}

// ---------------- setup: packs & recipes ----------------
function Setup({ setup, reload }: { setup: any; reload: () => void }) {
  const toast = useToast()
  const [edit, setEdit] = useState<any>(null)
  const recipeOf = (id: string) => setup.recipes.find((r: any) => r.output_sku_id === id)
  const save = async () => {
    try {
      await patch(`/api/production/skus/${edit.sku.id}`, { purchase_pack: edit.purchase_pack === '' ? 0 : Number(edit.purchase_pack), ship_pack: edit.ship_pack === '' ? 0 : Number(edit.ship_pack) })
      if (edit.raw && edit.yield) await put(`/api/production/recipes/${edit.sku.id}`, { lines: [{ sku_id: edit.raw, yield_pct: Number(edit.yield) }], note: edit.note || '' })
      setEdit(null); reload()
    } catch (e) { toast(errMsg(e), 'err') }
  }
  const rows = setup.skus.filter((s: any) => s.catch_weight || s.purchase_pack || s.ship_pack || recipeOf(s.id) || s.category === '原料肉' || s.category === '原料菜')
  return (
    <div className="space-y-2">
      <p className="text-xs text-slate-500">换供应商、换箱子时直接在这里改；以前的采购单和生产记录保留当时的数字。</p>
      {rows.map((s: any) => {
        const r = recipeOf(s.id)
        return (
          <button key={s.id} onClick={() => setEdit({ sku: s, purchase_pack: s.purchase_pack ?? '', ship_pack: s.ship_pack ?? '', raw: r?.lines[0]?.sku_id || '', yield: r?.expected_yield_pct ?? '', note: r?.note })}
            className="block w-full rounded-lg border border-slate-200 p-3 text-left text-sm active:bg-slate-50">
            <div className="flex flex-wrap items-center gap-2"><b>{s.name}</b><span className="text-xs text-slate-400">{s.code} · {u(s.base_unit)}</span>{s.catch_weight && <Badge>称重品</Badge>}</div>
            <div className="mt-1 text-xs text-slate-600">
              {s.purchase_pack ? `采购 ${s.purchase_pack} ${u(s.base_unit)}/箱` : '采购箱规未设'} · {s.ship_pack ? `出货 ${s.ship_pack} ${u(s.base_unit)}/箱` : '出货箱规未设'}
              {r && ` · 原料 ${r.lines[0]?.sku_code} 出成率 ${r.expected_yield_pct}%`}
            </div>
          </button>
        )
      })}
      {edit && (
        <Modal open onClose={() => setEdit(null)} title={`${edit.sku.name}`} footer={<button className="btn-primary" onClick={save}>保存</button>}>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <Field label={`采购：供应商每箱（${u(edit.sku.base_unit)}）`}><input className="input" type="number" inputMode="decimal" value={edit.purchase_pack} onChange={(e) => setEdit({ ...edit, purchase_pack: e.target.value })} /></Field>
              <Field label={`出货：每箱目标（${u(edit.sku.base_unit)}）`}><input className="input" type="number" inputMode="decimal" value={edit.ship_pack} onChange={(e) => setEdit({ ...edit, ship_pack: e.target.value })} /></Field>
            </div>
            <div className="text-sm font-semibold">如果这是成品：用什么原料、出成率多少</div>
            <div className="grid grid-cols-[1fr_100px] gap-2">
              <select className="input" value={edit.raw} onChange={(e) => setEdit({ ...edit, raw: e.target.value })}><option value="">（不是成品 / 不设）</option>{setup.skus.filter((x: any) => x.id !== edit.sku.id).map((x: any) => <option key={x.id} value={x.id}>{skuLabel(x)}</option>)}</select>
              <input className="input" type="number" inputMode="decimal" placeholder="出成率%" value={edit.yield} onChange={(e) => setEdit({ ...edit, yield: e.target.value })} />
            </div>
            <p className="text-xs text-slate-500">例：鸡腿肉切丁出成率 95% → 300 lb 鸡丁需要约 316 lb 鸡腿肉，按 40 lb/箱买 8 箱。</p>
          </div>
        </Modal>
      )}
    </div>
  )
}

// ---------------- module ----------------
export function ProductionPlanModule({ preview }: ModProps) {
  const { can } = useAuth()
  const [tab, setTab] = useState('plan')
  const setup = useLoad(() => (preview ? Promise.resolve(null) : get('/api/production/setup')), [preview])
  const runs = useLoad(() => (preview ? Promise.resolve([]) : get('/api/production/runs')), [preview])
  const [run, setRun] = useState<any>(null)
  const [labels, setLabels] = useState<any[] | null>(null)
  const totals = useMemo(() => {
    const rs = (runs.data || []).filter((r: any) => r.yield_pct != null)
    const inp = rs.reduce((a: number, r: any) => a + r.consumed_qty, 0), outp = rs.reduce((a: number, r: any) => a + r.output_qty, 0)
    return inp ? { yld: (100 * outp) / inp, loss: inp - outp } : null
  }, [runs.data])
  if (preview) return <div className="text-sm text-slate-400">切配生产（预览不加载数据）</div>
  if (!setup.data) return <Spinner />
  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-2">
        {can('production.run') && <button className="btn-primary" onClick={() => setRun({})}><Factory size={16} />生产完工登记</button>}
        {totals && <span className="self-center text-xs text-slate-500">近期出成率 {totals.yld.toFixed(1)}% · 损耗 {num(totals.loss)}</span>}
      </div>
      <Segmented value={tab} onChange={setTab} items={[{ key: 'plan', label: '备料计划' }, { key: 'runs', label: '生产记录' }, { key: 'setup', label: '配方与规格' }]} />
      <div className="mt-3">
        {tab === 'plan' && <Plan onProduce={(init) => setRun(init)} />}
        {tab === 'runs' && (
          <div className="space-y-2">
            {(runs.data || []).map((r: any) => (
              <div key={r.id} className={`rounded-lg border p-3 text-sm ${r.loss_alert ? 'border-red-200' : 'border-slate-200'}`}>
                <div className="flex flex-wrap items-center gap-2"><span className="font-mono">{r.code}</span><b>{r.product}</b><Badge color={LOT_COLOR[r.quality_status]}>{LOT_LABEL[r.quality_status]}</Badge>
                  {r.yield_pct != null && <Badge color={r.loss_alert ? 'red' : 'green'}>出成率 {r.yield_pct}%{r.expected_yield_pct && ` / 预计 ${r.expected_yield_pct}%`}</Badge>}
                  <span className="ml-auto text-xs text-slate-400">{dt(r.created_at)}</span></div>
                <div className="mt-1 text-xs text-slate-600">投入 {num(r.consumed_qty)} → 产出 {num(r.output_qty)} {u(r.unit)}{r.boxes ? `（${r.boxes} 箱）` : ''} · 损耗 {num(r.loss_qty)}{r.loss_reason && `（${r.loss_reason}）`} · Lot {r.output_lot}</div>
                <div className="text-xs text-slate-400">原料：{r.inputs.map((i: any) => `${i.sku_code} ${i.lot_code} ×${num(i.qty)}`).join('，')}</div>
              </div>
            ))}
            {runs.data && !runs.data.length && <Empty>还没有生产记录</Empty>}
          </div>
        )}
        {tab === 'setup' && <Setup setup={setup.data} reload={setup.reload} />}
      </div>
      {run && <RunDialog init={run} setup={setup.data} onClose={() => setRun(null)} onDone={(boxes) => { setRun(null); runs.reload(); if (boxes.length) setLabels(boxes) }} />}
      {labels && <BoxLabels boxes={labels} onClose={() => setLabels(null)} />}
    </div>
  )
}
