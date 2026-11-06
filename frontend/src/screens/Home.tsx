import { useState } from 'react'
import { Link } from 'react-router-dom'
import { CheckCircle2, CircleAlert, ClipboardCheck } from 'lucide-react'
import { get } from '../lib/api'
import { useAuth } from '../lib/auth'
import { dt } from '../lib/fmt'
import { t } from '../lib/i18n'
import { Badge, EV_COLOR, Empty, LOT_COLOR, LOT_LABEL, Modal, Spinner, errMsg, useLoad, useToast } from '../components/ui'
import { ScanButton } from '../components/Scan'
import { IngestModule, TaskCard } from '../modules/basic'
import { Preview } from '../components/Evidence'
import { PageByName } from './PageView'

export function Home() {
  const { me } = useAuth()
  if (me?.role === 'boss') return <PageByName match="老板总览" />
  return <ShiftHome />
}

function ShiftHome() {
  const { me } = useAuth()
  const toast = useToast()
  const { data } = useLoad(() => get('/api/my-shift'), [])
  const [scan, setScan] = useState<any>(null)
  const onScan = async (code: string) => {
    try {
      const r = await get(`/api/inv/scan/${encodeURIComponent(code)}`)
      setScan({ code, ...r })
    } catch (e) { toast(errMsg(e), 'err') }
  }
  if (!data) return <Spinner />
  const todo = data.checklists.filter((c: any) => !c.done_today)
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <h1 className="text-xl font-bold">{t('nav.home')} · {me?.name}</h1>
      <div className="card p-4">
        <div className="mb-3 flex items-center gap-2 font-semibold"><ClipboardCheck size={18} />今日检查</div>
        {data.checklists.length === 0 && <div className="text-sm text-slate-400">没有分配给你的每日检查</div>}
        <div className="space-y-2">
          {data.checklists.map((c: any) => (
            <Link key={c.module_id} to={`/p/${c.page_id}`} className={`flex items-center gap-3 rounded-lg p-3 ring-1 ${c.done_today ? 'ring-slate-200' : 'bg-amber-50 ring-amber-300'}`}>
              {c.done_today ? <CheckCircle2 className="text-emerald-600" /> : <CircleAlert className="text-amber-600" />}
              <div className="flex-1"><div className="font-medium">{c.title}</div><div className="text-xs text-slate-500">{c.page} · {c.done_today ? `今日已提交 ${dt(c.last_at)}` : '今日未提交'}</div></div>
              {c.last_passed === false && <Badge color="red">上次不合格</Badge>}
            </Link>
          ))}
        </div>
        {todo.length > 0 && <div className="mt-2 text-xs text-amber-700">还有 {todo.length} 项未提交——未提交时老板首页显示为「缺记录」，不会显示为正常。</div>}
      </div>
      <div className="card p-4">
        <div className="mb-3 flex items-center font-semibold">{t('task.mine')}（{data.tasks.length}）</div>
        <div className="space-y-2">{data.tasks.map((x: any) => <TaskCard key={x.id} task={x} />)}</div>
        {!data.tasks.length && <Empty />}
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="card p-4"><div className="mb-3 font-semibold">上传资料</div><IngestModule module={{ config: {} }} page={{}} /></div>
        <div className="card p-4"><div className="mb-3 font-semibold">扫码查批次</div><ScanButton onResult={onScan} /><p className="mt-2 text-xs text-slate-400">扫批次标签可查看是否已隔离/待放行</p></div>
      </div>
      <Modal open={!!scan} onClose={() => setScan(null)} title={`扫码结果：${scan?.code}`}>
        {scan?.type === 'lot' && <div className="text-lg">批次 <b>{scan.lot_code}</b> <Badge color={LOT_COLOR[scan.quality_status]}>{LOT_LABEL[scan.quality_status]}</Badge>{scan.quality_status !== 'released' && <p className="mt-2 text-sm text-red-600">该批次禁止领用、分配或发货。</p>}</div>}
        {scan?.type === 'sku' && <div>SKU <b>{scan.code}</b> {scan.name}</div>}
        {scan?.type === 'transfer' && <div>调拨单 — 请在「库存与调拨」页处理</div>}
      </Modal>
    </div>
  )
}

export function EvidenceReview() {
  const { data, reload } = useLoad(() => get('/api/evidence-pending'), [])
  const [pv, setPv] = useState<any>(null)
  const linkTo = (l: any) => ({ record: `/r/${l.target_id}`, task: `/t/${l.target_id}`, po: `/purchasing?po=${l.target_id}`, so: `/orders?so=${l.target_id}` } as any)[l.target_type]
  return (
    <div className="mx-auto max-w-4xl">
      <h1 className="mb-3 text-xl font-bold">待核验证据（{data?.length ?? '…'}）</h1>
      <div className="card divide-y">
        {(data || []).map((l: any) => (
          <div key={l.link_id} className="flex flex-wrap items-center gap-2 p-3 text-sm">
            {l.mime?.startsWith('image/') ? <img src={l.url} className="h-12 w-12 cursor-pointer rounded object-cover" onClick={() => setPv(l)} alt="" /> : <button className="btn-secondary btn-sm" onClick={() => setPv(l)}>预览</button>}
            <div className="min-w-0 flex-1"><div className="truncate">{l.title}</div><div className="text-xs text-slate-500"><Badge color="blue">{l.purpose}</Badge> <Badge color={EV_COLOR[l.verify_status]}>{t(`ev.${l.verify_status}`)}</Badge> {l.uploaded_by} · {dt(l.uploaded_at)}</div></div>
            {linkTo(l) ? <Link className="btn-secondary btn-sm" to={linkTo(l)}>去核验</Link> : <span className="text-xs text-slate-400">{l.target_type}</span>}
          </div>
        ))}
        {data && !data.length && <Empty>没有待核验证据</Empty>}
      </div>
      <button className="btn-ghost mt-2" onClick={reload}>刷新</button>
      <Preview link={pv} onClose={() => setPv(null)} />
    </div>
  )
}
