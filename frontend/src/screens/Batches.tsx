import { useEffect, useState } from 'react'
import { Bot, Check, X } from 'lucide-react'
import { ApiError, get, post } from '../lib/api'
import { dt, skuLabel } from '../lib/fmt'
import { Badge, Empty, Field, Spinner, errMsg, useLoad, useToast } from '../components/ui'

const ACTION: Record<string, [string, string]> = {
  new: ['新增', 'green'], update: ['修改', 'blue'], set: ['设定数量', 'blue'], unchanged: ['无变化', 'gray'],
  duplicate: ['疑似重复', 'yellow'], error: ['有错误', 'red'], mapped: ['已配对', 'green'], unmapped: ['未配对 SKU', 'yellow'],
}
const ST: Record<string, [string, string]> = { proposed: ['待确认', 'yellow'], applied: ['已全部应用', 'green'], partial: ['部分应用', 'green'], rejected: ['已驳回', 'gray'] }
const selectable = (a: string) => ['new', 'update', 'set', 'duplicate', 'mapped', 'unmapped'].includes(a)
const FIELD: Record<string, string> = { name: '名称', category: '分类', base_unit: '基本单位', conversions: '单位换算', barcode: '条码' }
const fmtV = (v: any) => (v === null || v === undefined || v === '' ? '（空）' : typeof v === 'object' ? JSON.stringify(v) : String(v))

export function BatchDetail({ id, onChanged }: { id: string; onChanged: () => void }) {
  const toast = useToast()
  const { data: b, reload } = useLoad(() => get(`/api/batches/${id}`), [id])
  const [sel, setSel] = useState<Set<number>>(new Set())
  const [only, setOnly] = useState<'all' | 'issues'>('all')
  const [ov, setOv] = useState<Record<number, { sku_id: string; factor?: string }>>({})
  const [reason, setReason] = useState('')
  const [askReason, setAskReason] = useState(false)
  const [fill, setFill] = useState<string | null>(null)
  const pickable = ['order', 'inbound', 'stock_count'].includes(b?.kind)
  const master = useLoad(() => (pickable ? get('/api/inv/master') : Promise.resolve(null)), [pickable])
  useEffect(() => { setFill(b?.fill_candidates?.[0]?.id ?? null) }, [b?.fill_candidates])
  useEffect(() => { if (b?.rows) setSel(new Set(b.rows.filter((r: any) => ['new', 'update', 'set', 'mapped', 'unmapped'].includes(r.action)).map((r: any) => r.index))) }, [b])
  if (!b) return <Spinner />
  const open = b.status === 'proposed'
  const toggle = (i: number) => { const n = new Set(sel); n.has(i) ? n.delete(i) : n.add(i); setSel(n) }
  const apply = async () => {
    const overrides = Object.fromEntries(Object.entries(ov).filter(([, v]) => v.sku_id).map(([k, v]) => [k, { sku_id: v.sku_id, factor: v.factor ? Number(v.factor) : undefined }]))
    try {
      const r = await post(`/api/batches/${id}/apply`, { selected: [...sel], overrides, shortfall_reason: reason, fill_order_id: fill })
      toast(r.result.fill_notes ? `已补到 ${r.result.order_code}` : r.result.order_code ? `已建订单 ${r.result.order_code}` : r.result.transfer_code ? `已建调入单 ${r.result.transfer_code}` : `已应用 ${r.result.applied.length} 行`); reload(); onChanged()
    } catch (e) {
      if (e instanceof ApiError && e.detail?.code === 'source_short') setAskReason(true)
      toast(errMsg(e), 'err')
    }
  }
  const reject = async () => {
    const note = prompt('驳回原因（可空）')
    if (note === null) return
    try { await post(`/api/batches/${id}/reject`, { note }); reload(); onChanged() } catch (e) { toast(errMsg(e), 'err') }
  }
  const shortSel = (b.rows || []).filter((r: any) => r.short && sel.has(r.index))
  const rows = (b.rows || []).filter((r: any) => only === 'all' || r.action === 'error' || r.action === 'duplicate' || r.messages.length)
  return (
    <div className="space-y-3 pb-16 sm:pb-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono font-medium">{b.code}</span><Badge color="blue">{b.kind_label}</Badge>
        <Badge color={ST[b.status][1]}>{ST[b.status][0]}</Badge>
        {b.source.startsWith('agent') && <Badge color="purple"><Bot size={12} />{b.source.replace('agent:', '')} 提交</Badge>}
        <span className="text-xs text-slate-400">{b.created_by} · {dt(b.created_at)}</span>
      </div>
      {b.source_note && <div className="rounded bg-slate-50 p-2 text-sm text-slate-600">来源：{b.source_note}</div>}
      {b.kind === 'order' && (
        <dl className="kv rounded bg-slate-50 p-2">
          <dt>PO 号</dt><dd className="font-mono font-semibold">{b.meta.po_no || <span className="font-sans font-normal text-amber-700">待补（先按送货单录）</span>}</dd>
          <dt>客户 / 送货点</dt><dd>{b.meta.customer || '大统华'} · {b.meta.site || '（未填）'}</dd>
          <dt>下单 / 交货</dt><dd>{b.meta.po_date || '—'} → <b>{b.meta.required_date || '未填'}</b>{b.meta.category && ` · ${b.meta.category}`}</dd>
        </dl>
      )}
      {open && b.kind === 'order' && b.fill_candidates?.length > 0 && (
        <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm">
          <div className="font-medium text-blue-900">之前有没填 PO 的送货单，这张 PO 是给它补的吗？</div>
          <p className="mt-0.5 text-xs text-blue-800">选「补到这张」：PO 号、大统华 Item#、单价补到原单上，已发的货不重复；PO 多出来的行追加待发。</p>
          <select className="input mt-2" value={fill || ''} onChange={(e) => setFill(e.target.value || null)}>
            {b.fill_candidates.map((c: any) => <option key={c.id} value={c.id}>补到 {c.code}（{c.required_date || '无交期'} · {c.status_label}）：{c.lines}</option>)}
            <option value="">不是，新建一张订单</option>
          </select>
        </div>
      )}
      {b.kind === 'inbound' && <div className="text-sm">从 <b>{b.meta.from_code}</b> 到 <b>{b.meta.to_code || 'WH597'}</b> · 预计 {b.meta.expected_date || '未定'}{b.meta.note && ` · ${b.meta.note}`}</div>}
      {b.kind === 'alias_upsert' && <div className="rounded bg-slate-50 p-2 text-xs text-slate-600">把各方的编码 / 品名对到<b>我们自己的 SKU</b>，不新建 SKU。确认后，单据进来时自动认出。</div>}
      {b.kind === 'stock_count' && <div className="rounded bg-amber-50 p-2 text-xs text-amber-800">数量是<b>设定值</b>（盘点结果），不是增减。实盘写入实物；「估计」写入未核数，不算可用。{b.meta?.as_of && ` 数据日期 ${b.meta.as_of}`}</div>}
      <div className="flex flex-wrap gap-1.5">
        {Object.entries(b.summary || {}).map(([k, n]: any) => <Badge key={k} color={ACTION[k]?.[1]}>{ACTION[k]?.[0] || k} {n}</Badge>)}
      </div>
      <div className="seg w-fit" role="group">
        <button aria-pressed={only === 'all'} onClick={() => setOnly('all')}>全部 {b.rows.length}</button>
        <button aria-pressed={only === 'issues'} onClick={() => setOnly('issues')}>只看需注意</button>
      </div>
      <div className="space-y-2">
        {rows.map((r: any) => (
          <label key={r.index} className={`flex gap-3 rounded-lg border p-3 text-sm ${r.action === 'error' ? 'border-red-200 bg-red-50/40' : r.action === 'duplicate' ? 'border-amber-200 bg-amber-50/40' : 'border-slate-200'}`}>
            {open && <input type="checkbox" className="mt-1 shrink-0" disabled={!selectable(r.action)} checked={sel.has(r.index)} onChange={() => toggle(r.index)} />}
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2"><span className="text-xs text-slate-400">#{r.index + 1}</span><span className="font-medium">{r.key || '—'}</span>
                <Badge color={ACTION[r.action][1]}>{ACTION[r.action][0]}</Badge>{b.kind === 'sku_upsert' && r.code && <span className="text-xs text-slate-500">{r.code}</span>}</div>
              {Object.keys(r.changes || {}).length > 0 && (
                <div className="mt-1 space-y-0.5 text-xs">
                  {Object.entries(r.changes).map(([k, v]: any) => <div key={k}><span className="text-slate-400">{FIELD[k] || k}：</span>{v[0] !== null && <span className="text-slate-400 line-through">{fmtV(v[0])}</span>} → <b>{fmtV(v[1])}</b>{r.unit && ['实物', '未核数', '未核数（估计）', '在途'].includes(k) && ` ${r.unit}`}</div>)}
                </div>
              )}
              {r.messages.map((m: string, i: number) => <div key={i} className={`mt-1 text-xs ${r.action === 'error' ? 'text-red-600' : 'text-amber-700'}`}>{m}</div>)}
              {open && pickable && ['unmapped', 'new', 'mapped', 'set'].includes(r.action) && master.data && (r.action === 'unmapped' || ov[r.index]) && (
                <div className="mt-1 flex gap-1" onClick={(e) => e.preventDefault()}>
                  <select className="input min-w-0 flex-1" value={ov[r.index]?.sku_id || ''} onChange={(e) => setOv({ ...ov, [r.index]: { ...ov[r.index], sku_id: e.target.value } })}>
                    <option value="">{b.kind === 'order' ? '选我们的 SKU（可稍后在订单里配）' : '选我们的 SKU（确认后记住）'}</option>{master.data.skus.map((s: any) => <option key={s.id} value={s.id}>{skuLabel(s)}</option>)}</select>
                  <input className="input w-20 shrink-0" type="number" inputMode="decimal" placeholder="换算×" value={ov[r.index]?.factor || ''} onChange={(e) => setOv({ ...ov, [r.index]: { ...ov[r.index], factor: e.target.value } })} />
                </div>
              )}
            </div>
          </label>
        ))}
        {!rows.length && <Empty>没有需要注意的行</Empty>}
      </div>
      {open && (shortSel.length > 0 || askReason) && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm">
          <div className="font-medium text-amber-900">来源仓账面不够{shortSel.length > 0 && `（${shortSel.length} 行）`}</div>
          <p className="mt-0.5 text-xs text-amber-800">外库实际有货但账面没记全（比如期初漏录）时，写下原因可按单据补记，会标「例外」并留审计；否则取消勾选这些行。</p>
          <input className="input mt-2" placeholder="补记原因，如：Supreme 期初报表漏了 11/1 后入库的货" value={reason} onChange={(e) => setReason(e.target.value)} />
        </div>
      )}
      {open ? (
        <div className="fixed inset-x-0 bottom-[calc(56px+env(safe-area-inset-bottom))] z-20 flex gap-2 border-t bg-white/95 px-4 py-2 backdrop-blur sm:static sm:border-0 sm:bg-transparent sm:p-0 [&>button]:flex-1 sm:[&>button]:flex-none">
          <button className="btn-primary" disabled={!sel.size || (shortSel.length > 0 && !reason.trim())} onClick={apply}><Check size={16} />确认所选（{sel.size}）</button>
          <button className="btn-ghost" onClick={reject}><X size={16} />全部驳回</button>
        </div>
      ) : (
        <div className="rounded bg-slate-50 p-2 text-xs text-slate-600">{b.decided_by} · {dt(b.decided_at)} 处理{b.result?.applied && `：应用 ${b.result.applied.length} 行`}{b.result?.transfer_code && `，生成调入单 ${b.result.transfer_code}`}{b.result?.order_code && (b.result.fill_notes ? `，补到订单 ${b.result.order_code}` : `，生成订单 ${b.result.order_code}`)}{b.result?.fill_notes?.length > 0 && `（${b.result.fill_notes.join('；')}）`}{b.result?.note && ` · ${b.result.note}`}</div>
      )}
    </div>
  )
}

export function BatchList({ sel, onSelect, version }: { sel: string | null; onSelect: (id: string) => void; version: number }) {
  const { data } = useLoad(() => get('/api/batches'), [version])
  if (!data) return <Spinner />
  return (
    <div className="space-y-1">
      {data.map((b: any) => (
        <button key={b.id} onClick={() => onSelect(b.id)} className={`flex w-full items-center gap-2 rounded p-2 text-left text-sm ${sel === b.id ? 'bg-brand-50' : 'hover:bg-slate-50'}`}>
          <div className="min-w-0 flex-1"><div className="truncate">{b.kind_label} · {b.rows_count} 行</div><div className="text-xs text-slate-400">{b.code} · {dt(b.created_at)}{b.source.startsWith('agent') && ' · AI'}</div></div>
          <Badge color={ST[b.status][1]}>{ST[b.status][0]}</Badge>
        </button>
      ))}
      {!data.length && <Empty>还没有批量导入。让 ChatGPT 用 propose_skus / propose_stock_counts 提交即可。</Empty>}
    </div>
  )
}

/** 清空演示数据（老板/系统管理员）。 */
export function ResetDemo() {
  const toast = useToast()
  const { data, reload } = useLoad(() => get('/api/admin/status'), [])
  const [phrase, setPhrase] = useState('')
  const [deact, setDeact] = useState(true)
  const [res, setRes] = useState<any>(null)
  if (!data?.can_reset) return null
  const go = async () => {
    try { setRes(await post('/api/admin/reset-demo', { confirm: phrase, deactivate_demo_users: deact })); setPhrase(''); reload() } catch (e) { toast(errMsg(e), 'err') }
  }
  return (
    <div className="card mt-6 border-red-200 p-4 ring-red-200">
      <div className="mb-1 font-semibold text-red-700">清空演示数据</div>
      <p className="text-sm text-slate-600">导入真实数据前使用。会删除所有业务数据（SKU、库存流水、批次、调拨、采购、任务、证据文件、资料中心），保留页面配置、记录类型、用户、Notebook 联系人与笔记、多伦多/温哥华等来源地和审计日志。执行前会自动在数据盘备份一份数据库。</p>
      {data.demo_cleared && <p className="mt-1 text-xs text-slate-400">上次清空：{dt(data.demo_cleared.at)}，备份 {data.demo_cleared.backup}</p>}
      <label className="mt-2 flex items-center gap-2 text-sm"><input type="checkbox" checked={deact} onChange={(e) => setDeact(e.target.checked)} />同时停用演示账号（ops、buyer、wh、qa、staff1…，保留 admin 和 boss）</label>
      <Field label={`输入「${data.confirm_phrase}」确认`}><input className="input" value={phrase} onChange={(e) => setPhrase(e.target.value)} /></Field>
      <button className="btn-danger mt-2 w-full sm:w-auto" disabled={phrase !== data.confirm_phrase} onClick={go}>清空</button>
      {res && <div className="mt-2 rounded bg-emerald-50 p-2 text-xs text-emerald-800">已清空。备份：{res.backup}；删除文件 {res.files_removed} 个；停用账号 {res.deactivated_users.join('、') || '无'}</div>}
    </div>
  )
}
