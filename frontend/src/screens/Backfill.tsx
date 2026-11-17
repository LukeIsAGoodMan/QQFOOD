import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Bot, Plus } from 'lucide-react'
import { get, post } from '../lib/api'
import { useAuth } from '../lib/auth'
import { dt, num } from '../lib/fmt'
import { Badge, Empty, ErrorBox, Field, Modal, Spinner, errMsg, useLoad, useToast } from '../components/ui'
import { EvidencePanel } from '../components/Evidence'
import { BackfillEditor, PlanView } from './BackfillEditor'

const ST: Record<string, [string, string]> = { draft: ['草稿（未生效）', 'yellow'], confirmed: ['已确认', 'green'], voided: ['已作废', 'gray'] }
const ACT: Record<string, string> = { created: '创建', confirmed: '确认', revised: '修改', voided: '作废', change_proposed: '提出修改（待确认）', change_discarded: '放弃提议' }

const isAdmin = (role?: string) => role === 'boss' || role === 'sys_admin'

function NoAccess() {
  return <div className="card p-6 text-sm text-slate-500">历史业务补录只有老板或系统管理员可以使用。</div>
}

export function BackfillList() {
  const { me } = useAuth()
  const nav = useNavigate()
  const toast = useToast()
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('')
  const [creating, setCreating] = useState(false)
  const { data, error, reload } = useLoad(() => (isAdmin(me?.role) ? get(`/api/backfill?q=${encodeURIComponent(q)}${status ? `&status=${status}` : ''}`) : Promise.resolve([])), [q, status, me?.role])
  if (!isAdmin(me?.role)) return <NoAccess />
  return (
    <div className="mx-auto max-w-3xl space-y-3 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold">历史业务补录</h1>
        <button className="btn-primary ml-auto" onClick={() => setCreating(true)}><Plus size={16} />新建补录</button>
      </div>
      <p className="text-xs text-slate-500">补以前发生、系统里没记全的采购小票、采购单、收货、调拨、生产耗用、销售交付和费用。每行默认「只补单据」，库存确实没记过才选「补记库存」。存为草稿不改任何数据，确认后才生效。</p>
      <div className="flex flex-wrap gap-2">
        <input className="input min-w-0 flex-1" placeholder="搜单号 / 柜号 / 供应商 / 品名" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="input w-auto" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">全部</option><option value="draft">草稿</option><option value="confirmed">已确认</option><option value="voided">已作废</option></select>
      </div>
      {error ? <ErrorBox error={error} onRetry={reload} /> : !data ? <Spinner /> : !data.length ? <Empty>还没有补录单</Empty> : data.map((r: any) => (
        <button key={r.id} className="card block w-full p-3 text-left" onClick={() => nav(`/backfill/${r.id}`)}>
          <div className="flex flex-wrap items-center gap-2"><b className="font-mono">{r.code}</b><Badge color={ST[r.status][1]}>{ST[r.status][0]}</Badge><span className="text-xs text-slate-500">{r.kind_label}</span>
            {r.pending && <Badge color="purple">有待确认修改</Badge>}{r.source.startsWith('agent') && <Bot size={14} className="text-slate-400" />}</div>
          <div className="text-xs text-slate-500">{r.business_date} · {r.supplier || r.doc_no || '—'}{r.container_no ? ` · 柜 ${r.container_no}` : ''} · {r.lines} 行{r.posted_lines ? `（${r.posted_lines} 行记了库存）` : '（未动库存）'}{r.total_amount != null ? ` · $${num(r.total_amount)}` : ''}</div>
        </button>
      ))}
      <Modal open={creating} onClose={() => setCreating(false)} wide title="新建历史补录">
        {creating && <BackfillEditor onCancel={() => setCreating(false)} onSave={async (body) => { const r = await post('/api/backfill', body); toast('已存为草稿，核对后确认'); nav(`/backfill/${r.id}`) }} />}
      </Modal>
    </div>
  )
}

export function BackfillDetail() {
  const { id = '' } = useParams()
  const { me } = useAuth()
  const toast = useToast()
  const { data: r, error, reload, setData } = useLoad(() => (isAdmin(me?.role) ? get(`/api/backfill/${id}`) : Promise.resolve(null)), [id, me?.role])
  const [ack, setAck] = useState<Set<string>>(new Set())
  const [ackDup, setAckDup] = useState(false)
  const [neg, setNeg] = useState('')
  const [editing, setEditing] = useState(false)
  const [voiding, setVoiding] = useState<any>(null)
  const [voidReason, setVoidReason] = useState('')
  const [pending, setPending] = useState<any>(null)
  if (!isAdmin(me?.role)) return <NoAccess />
  if (error) return <div className="p-4"><ErrorBox error={error} onRetry={reload} /></div>
  if (!r) return <Spinner />
  const opts = { version: r.version, acknowledged_lines: [...ack], ack_duplicates: ackDup, negative_reason: neg }
  const done = (x: any, msg: string) => { setData(x.record); setAck(new Set()); setAckDup(false); toast(msg) }
  const act = async (fn: () => Promise<any>) => { try { await fn() } catch (e) { toast(errMsg(e), 'err'); reload() } }
  const plan = r.plan
  const needsDupAck = plan && plan.dups.length > 0 && plan.posting_lines > 0
  return (
    <div className="mx-auto max-w-3xl space-y-3 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="font-mono text-lg font-semibold">{r.code}</h1><Badge color={ST[r.status][1]}>{ST[r.status][0]}</Badge>
        {r.source.startsWith('agent') && <span className="chip bg-slate-100"><Bot size={12} />ChatGPT 提交</span>}
      </div>
      <div className="card space-y-1 p-3 text-sm">
        <div><b>{r.kind_label}</b> · 业务日期 {r.business_date} · {r.goods_status_label}</div>
        <div className="text-slate-600">{[r.supplier, r.doc_no && `单号 ${r.doc_no}`, r.container_no && `柜 ${r.container_no}`, r.from_warehouse && `从 ${r.from_warehouse}`, r.warehouse && `到 ${r.warehouse}`].filter(Boolean).join(' · ')}</div>
        {(r.total_amount != null || r.payment_label) && <div className="text-slate-600">{r.total_amount != null && `总额 $${num(r.total_amount)}`} {r.payment_label}{r.paid_by ? `（${r.paid_by}）` : ''}</div>}
        <div>补录原因：{r.reason}</div>
        {r.links.length > 0 && <div className="flex flex-wrap gap-1">{r.links.map((x: any, i: number) => <span key={i} className="chip bg-slate-100">{x.label}</span>)}</div>}
        <div className="text-xs text-slate-500">创建 {r.created_by} {dt(r.created_at)}{r.confirmed_by && ` · 确认 ${r.confirmed_by} ${dt(r.confirmed_at)}`}{r.voided_by && ` · 作废 ${r.voided_by} ${dt(r.voided_at)}（${r.void_reason}）`}</div>
      </div>

      {r.status === 'draft' && plan && (
        <div className="card space-y-3 p-3">
          <div className="font-semibold">确认前核对：每行的库存影响</div>
          <PlanView plan={plan} ack={ack} setAck={setAck} />
          {needsDupAck && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={ackDup} onChange={(e) => setAckDup(e.target.checked)} />我核对过，不是同一张单据重复补录</label>}
          {plan.lines.some((l: any) => l.blockers.some((b: string) => b.startsWith('库存不足'))) && <Field label="例外：账面不够扣时按负数记的原因"><input className="input" value={neg} onChange={(e) => setNeg(e.target.value)} /></Field>}
          <div className="text-xs text-slate-500">将记库存 {plan.posting_lines} 行；其余只补单据。确认后不能直接改账，修改按差额、作废按冲销记新流水。</div>
          <div className="flex flex-wrap gap-2">
            <button className="btn-primary flex-1" onClick={() => act(async () => done(await post(`/api/backfill/${r.id}/confirm`, opts), '已确认'))}>确认补录</button>
            <button className="btn-secondary" onClick={() => setEditing(true)}>修改</button>
            <button className="btn-ghost text-red-600" onClick={() => { setVoiding({ plan: null }); setVoidReason('') }}>作废</button>
          </div>
        </div>
      )}

      {r.pending?.reason && (
        <div className="card space-y-2 border-purple-300 p-3">
          <div className="font-semibold">待确认的{r.pending.void ? '作废' : '修改'}（{r.pending.by_name} 经 {r.pending.source.replace('agent:', '')} 提出）</div>
          <div className="text-sm">原因：{r.pending.reason}</div>
          {!pending ? <button className="btn-secondary" onClick={() => act(async () => setPending(await post(`/api/backfill/${r.id}/pending/preview`)))}>看库存差额</button> : <>
            <PlanView plan={pending.plan} ack={ack} setAck={setAck} />
            <div className="flex gap-2">
              <button className="btn-primary flex-1" onClick={() => act(async () => { done(await post(`/api/backfill/${r.id}/pending/apply`, opts), '已按提议更新'); setPending(null) })}>确认这次{r.pending.void ? '作废' : '修改'}</button>
              <button className="btn-secondary" onClick={() => act(async () => { setData(await post(`/api/backfill/${r.id}/pending/discard`)); setPending(null) })}>放弃</button>
            </div>
          </>}
        </div>
      )}

      <div className="card p-3">
        <div className="mb-1 font-semibold">明细</div>
        {r.lines.map((l: any) => (
          <div key={l.id} className="border-t border-slate-100 py-1.5 text-sm first:border-0">
            <div className="flex flex-wrap items-center gap-2"><b>{l.name}</b><Badge color={l.inventory === 'post' ? 'blue' : 'gray'}>{l.inventory === 'post' ? '补记库存' : '只补单据'}</Badge>{l.item_type && <span className="text-xs text-slate-500">{l.item_type}</span>}</div>
            <div className="text-xs text-slate-500">{l.category} · {l.sku || (l.new_sku ? `新建「${l.new_sku.name}」` : '不对应商品')} · {l.qty != null ? `${num(l.qty)} ${l.unit || ''}` : ''}{l.factor ? `（1 ${l.unit} = ${l.factor} ${l.base_unit}）` : ''}{l.amount != null ? ` · $${num(l.amount)}` : ''}{l.lot_code ? ` · Lot ${l.lot_code}` : ''}</div>
          </div>
        ))}
      </div>

      {r.status !== 'draft' && (
        <div className="card p-3">
          <div className="mb-1 font-semibold">这张单记了的库存（净额）</div>
          {r.posted.length ? r.posted.map((p: any, i: number) => <div key={i} className="text-sm">{p.sku} @ {p.warehouse}{p.lot ? ` · Lot ${p.lot}` : ''}：{p.qty > 0 ? '+' : ''}{num(p.qty)}</div>) : <div className="text-sm text-slate-400">没有动库存</div>}
          {r.status === 'confirmed' && !r.pending?.reason && <div className="mt-2 flex gap-2">
            <button className="btn-secondary" onClick={() => setEditing(true)}>修改（记差额）</button>
            <button className="btn-ghost text-red-600" onClick={() => act(async () => { setVoiding(await post(`/api/backfill/${r.id}/void`, { version: r.version, preview_only: true })); setVoidReason('') })}>作废（冲回）</button>
          </div>}
        </div>
      )}

      <EvidencePanel targetType="backfill" targetId={r.id} purposes={['小票 / 收据', '采购单', '送货单 / BOL', '装柜 / 运输资料', '付款凭证', '照片', '附件']} title="原件（小票、单据、照片）" />

      <div className="card p-3">
        <div className="mb-1 font-semibold">修改日志</div>
        {r.log.slice().reverse().map((x: any, i: number) => (
          <div key={i} className="border-t border-slate-100 py-1 text-xs first:border-0">
            <b>{ACT[x.action] || x.action}</b> · {x.by_name} · {dt(x.at)}{x.source && x.source !== 'web' ? ` · ${x.source}` : ''}
            {x.reason && <div className="text-slate-600">原因：{x.reason}</div>}
            {x.changes?.stock?.map((s: any, j: number) => <div key={j} className="text-slate-500">{s.line}：{s.effects.join('，')}</div>)}
            {x.changes && Object.keys(x.changes).filter((k) => !['stock', 'posting', 'acknowledged', 'negative_reason', 'lines', 'void', 'fields'].includes(k)).map((k) => <div key={k} className="text-slate-500">{k}：{String(x.changes[k]?.[0] ?? '—')} → {String(x.changes[k]?.[1] ?? '—')}</div>)}
            {x.changes?.lines && <div className="text-slate-500">明细变动：{Object.keys(x.changes.lines).join('、')}</div>}
          </div>
        ))}
      </div>

      <Modal open={editing} onClose={() => setEditing(false)} wide title={r.status === 'confirmed' ? '修改已确认的补录（库存只记差额）' : '修改草稿'}>
        {editing && <BackfillEditor initial={r} confirmed={r.status === 'confirmed'} onCancel={() => setEditing(false)} onSave={async (body, reason) => {
          const x = await post(`/api/backfill/${r.id}/revise`, { ...opts, changes: body, reason })
          setEditing(false); done(x, x.posting_id ? '已修改，库存按差额记了新流水' : '已修改')
        }} />}
      </Modal>
      <Modal open={!!voiding} onClose={() => setVoiding(null)} title="作废补录单"
        footer={<button className="btn-danger" disabled={!voidReason.trim()} onClick={() => act(async () => { done(await post(`/api/backfill/${r.id}/void`, { version: r.version, reason: voidReason }), '已作废'); setVoiding(null) })}>确认作废</button>}>
        <div className="space-y-2">
          {voiding?.plan ? <><div className="text-sm">这张单记过的库存会按原数冲回（新记一笔冲销流水，旧流水保留）：</div><PlanView plan={voiding.plan} /></> : <div className="text-sm">草稿没有动过库存，作废后保留记录。</div>}
          <Field label="作废原因 *"><input className="input" value={voidReason} onChange={(e) => setVoidReason(e.target.value)} /></Field>
        </div>
      </Modal>
    </div>
  )
}
