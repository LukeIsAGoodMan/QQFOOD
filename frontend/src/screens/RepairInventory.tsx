import { useState } from 'react'
import { get, post } from '../lib/api'
import { useAuth } from '../lib/auth'
import { dt, num } from '../lib/fmt'
import { Badge, Empty, Field, Modal, Spinner, errMsg, useToast } from '../components/ui'

/** 历史补录送货的库存修复：先预览（只读），选中后输入「确认修复」才执行（先备份，再记冲销流水，不删除任何记录）。只有老板 / 系统管理员可见。 */
export function RepairInventory() {
  const { me } = useAuth()
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [data, setData] = useState<any>(null)
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [reason, setReason] = useState('')
  const [confirm, setConfirm] = useState('')
  const [done, setDone] = useState<any>(null)
  if (!me || !['boss', 'sys_admin'].includes(me.role)) return null
  const load = async () => {
    setOpen(true); setData(null); setDone(null)
    try { const d = await get('/api/admin/repair/historical-shipments'); setData(d); setSel(new Set(d.shipments.filter((x: any) => x.suspect).map((x: any) => x.shipment_id))) } catch (e) { toast(errMsg(e), 'err') }
  }
  const run = async () => {
    try { const r = await post('/api/admin/repair/historical-shipments', { shipment_ids: [...sel], reason, confirm }); setDone(r); setConfirm(''); toast(`已修复 ${r.fixed.length} 张`) } catch (e) { toast(errMsg(e), 'err') }
  }
  return (
    <div className="card mt-6 p-4">
      <div className="font-semibold">核对历史补录送货的库存</div>
      <p className="mt-1 text-xs text-slate-600">期初 / 盘点录的是扣掉某次送货后的净结存，之后又把那次送货补录成发货、系统又扣了一次——在这里找出来并冲销。先预览，不会改任何数据。</p>
      <button className="btn-secondary mt-2" onClick={load}>预览</button>
      <Modal open={open} onClose={() => setOpen(false)} wide title="历史补录送货 · 库存核对"
        footer={data && !done && <button className="btn-danger" disabled={!sel.size || !reason.trim() || confirm.trim() !== data.confirm_phrase} onClick={run}>执行修复（{sel.size} 张）</button>}>
        {!data ? <Spinner /> : done ? (
          <div className="space-y-2 text-sm">
            <div className="rounded bg-emerald-50 p-3 text-emerald-800">已修复：{done.fixed.join('、') || '无'}{done.skipped.length > 0 && `；跳过 ${done.skipped.length} 张（${done.skipped.map((x: any) => x.why).join('、')}）`}</div>
            <div className="text-xs text-slate-500">修复前已备份数据库：{done.backup || '（非 SQLite，未备份）'}。每一笔都记了冲销流水和审计，可在库存流水里查到。</div>
          </div>
        ) : (
          <div className="space-y-3">
            <p className="text-xs text-slate-500">{data.note} 疑似重复 <b>{data.suspects}</b> 张，已默认勾选。</p>
            {data.shipments.map((x: any) => (
              <label key={x.shipment_id} className={`flex gap-2 rounded-lg border p-3 text-sm ${x.suspect ? 'border-amber-300 bg-amber-50/50' : 'border-slate-200'}`}>
                <input type="checkbox" className="mt-1" checked={sel.has(x.shipment_id)} onChange={() => { const n = new Set(sel); n.has(x.shipment_id) ? n.delete(x.shipment_id) : n.add(x.shipment_id); setSel(n) }} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2"><b className="font-mono">{x.delivery_no || x.code}</b><span className="text-xs text-slate-500">{x.order} · PO {x.po_no || '待补'}</span>{x.suspect && <Badge color="yellow">疑似重复扣减</Badge>}</div>
                  <div className="text-xs text-slate-500">业务日期 {x.business_date} · 录入 {dt(x.recorded_at)}</div>
                  {x.lines.map((l: any, i: number) => <div key={i} className="text-xs">{l.sku} @ {l.warehouse}：扣了 {num(l.deducted)} {l.unit} · 现在 {num(l.on_hand_now)} → 修复后 <b>{num(l.on_hand_after_fix)}</b></div>)}
                  {x.reasons.map((r: string, i: number) => <div key={i} className="mt-1 text-xs text-amber-800">{r}</div>)}
                </div>
              </label>
            ))}
            {!data.shipments.length && <Empty>没有需要核对的送货单</Empty>}
            {data.shipments.length > 0 && <>
              <Field label="修复原因 *"><input className="input" placeholder="如：11/5 期初 81 / 89 箱已是扣掉 11/4 送货后的净结存" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
              <Field label={`输入「${data.confirm_phrase}」确认（会先备份数据库，再记冲销流水）`}><input className="input" value={confirm} onChange={(e) => setConfirm(e.target.value)} /></Field>
            </>}
          </div>
        )}
      </Modal>
    </div>
  )
}
