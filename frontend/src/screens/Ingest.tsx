import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Check, GitMerge, Plus, Trash2, X } from 'lucide-react'
import { get, post, upload } from '../lib/api'
import { dt } from '../lib/fmt'
import { Badge, Empty, Field, Spinner, Tabs, errMsg, useLoad, useToast } from '../components/ui'
import { PickButtons, Preview } from '../components/Evidence'

const ST: Record<string, [string, string]> = {
  inbox: ['待整理', 'yellow'], processing: ['处理中', 'blue'], pending_confirm: ['待确认', 'blue'], conflict: ['冲突', 'red'],
  applied: ['已应用', 'green'], evidence_only: ['仅存证', 'gray'], rejected: ['已驳回', 'gray'], failed: ['失败', 'red'],
}
const TASK_FIELDS = [['due_at', '截止时间（预计日期）'], ['next_step', '下一步'], ['title', '标题'], ['description', '说明'], ['status', '状态']]

function TargetPicker({ onPick }: { onPick: (t: any) => void }) {
  const [q, setQ] = useState('')
  const { data } = useLoad(() => (q.length ? get(`/api/search?q=${encodeURIComponent(q)}`) : Promise.resolve([])), [q])
  return (
    <div>
      <input className="input" placeholder="搜索要更新的记录或任务（PO 号、调拨、名称…）" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="mt-1 space-y-1">
        {(data || []).filter((r: any) => r.type === 'record' || r.type === 'task').map((r: any) => (
          <button key={r.id} onClick={() => onPick(r)} className="flex w-full gap-2 rounded p-2 text-left text-sm hover:bg-slate-50"><Badge>{r.type === 'task' ? '任务' : '记录'}</Badge>{r.title}<span className="ml-auto text-xs text-slate-400">{r.sub}</span></button>
        ))}
      </div>
    </div>
  )
}

function Propose({ item, onDone }: { item: any; onDone: () => void }) {
  const toast = useToast()
  const [target, setTarget] = useState<any>(null)
  const rec = useLoad(() => (target?.type === 'record' ? get(`/api/records/${target.id}`) : Promise.resolve(null)), [target?.id])
  const [rows, setRows] = useState<any[]>([{ field: '', new: '', reason: '', evidence_loc: '' }])
  const fields: string[][] = target?.type === 'task' ? TASK_FIELDS : rec.data ? [['title', '标题'], ['status', '状态'], ...rec.data.type.fields.filter((f: any) => f.active).map((f: any) => [`data.${f.id}`, f.label])] : []
  const submit = async () => {
    try {
      const changes = rows.filter((r) => r.field).map((r) => ({ ...r, new: r.field === 'due_at' && r.new ? new Date(r.new).toISOString() : r.new }))
      await post(`/api/ingest/${item.id}/changesets`, { target_type: target.type, target_id: target.id, changes })
      toast('已提出变更，等待确认'); setTarget(null); setRows([{ field: '', new: '', reason: '', evidence_loc: '' }]); onDone()
    } catch (e) { toast(errMsg(e), 'err') }
  }
  if (!target) return <TargetPicker onPick={setTarget} />
  return (
    <div className="space-y-2">
      <div className="text-sm">目标：<b>{target.title}</b> <button className="text-xs text-brand-700 underline" onClick={() => setTarget(null)}>更换</button></div>
      {rows.map((r, i) => (
        <div key={i} className="grid gap-2 rounded border p-2 sm:grid-cols-[1.2fr_1.2fr_1fr_1fr_auto]">
          <select className="input" value={r.field} onChange={(e) => setRows(rows.map((x, j) => (i === j ? { ...x, field: e.target.value } : x)))}><option value="">字段…</option>{fields.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
          <input className="input" type={r.field === 'due_at' ? 'datetime-local' : 'text'} placeholder="建议值" value={r.new} onChange={(e) => setRows(rows.map((x, j) => (i === j ? { ...x, new: e.target.value } : x)))} />
          <input className="input" placeholder="依据/原因" value={r.reason} onChange={(e) => setRows(rows.map((x, j) => (i === j ? { ...x, reason: e.target.value } : x)))} />
          <input className="input" placeholder="资料位置（如 p1 右上）" value={r.evidence_loc} onChange={(e) => setRows(rows.map((x, j) => (i === j ? { ...x, evidence_loc: e.target.value } : x)))} />
          <button className="btn-ghost" onClick={() => setRows(rows.filter((_, j) => j !== i))}><Trash2 size={14} /></button>
        </div>
      ))}
      <div className="flex gap-2">
        <button className="btn-ghost btn-sm" onClick={() => setRows([...rows, { field: '', new: '', reason: '', evidence_loc: '' }])}><Plus size={14} />加字段</button>
        <button className="btn-primary btn-sm ml-auto" disabled={!rows.some((r) => r.field)} onClick={submit}>提出变更</button>
      </div>
      <p className="text-xs text-slate-400">只能映射已注册字段。数量/实收、库存调整、质量放行不通过这里修改——请在对应业务模块完成。</p>
    </div>
  )
}

function ChangeSetView({ cs, onDone }: { cs: any; onDone: () => void }) {
  const toast = useToast()
  const [accept, setAccept] = useState<string[]>(cs.changes.map((c: any) => c.field))
  const [edits, setEdits] = useState<Record<string, any>>({})
  const apply = async () => {
    try { const r = await post(`/api/changesets/${cs.id}/apply`, { accept, edits }); toast(r.result); onDone() } catch (e) { toast(errMsg(e), 'err'); onDone() }
  }
  const reject = async () => { const note = prompt('驳回原因') ; if (note === null) return; await post(`/api/changesets/${cs.id}/reject`, { status: 'rejected', note }); onDone() }
  const rebase = async () => { try { await post(`/api/changesets/${cs.id}/rebase`); onDone() } catch (e) { toast(errMsg(e), 'err') } }
  const fmt = (v: any) => (v === null || v === undefined || v === '' ? '（空）' : typeof v === 'string' && /^\d{4}-\d\d-\d\dT/.test(v) ? dt(v) : String(v))
  return (
    <div className="rounded-lg border p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
        <Badge color={cs.status === 'conflict' ? 'red' : cs.status === 'proposed' ? 'blue' : cs.status === 'rejected' ? 'gray' : 'green'}>{({ proposed: '待确认', applied: '已应用', partial: '部分应用', rejected: '已驳回', conflict: '冲突' } as any)[cs.status]}</Badge>
        <span>目标 <Link className="text-brand-700 underline" to={cs.target_type === 'task' ? `/t/${cs.target_id}` : cs.target_type === 'po' ? `/purchasing?po=${cs.target_id}` : `/r/${cs.target_id}`}>{cs.target_title}</Link></span>
        <span className="text-xs text-slate-400">基于 v{cs.target_version}{cs.stale && <b className="text-red-600">（目标已更新到 v{cs.current_version}，应用时会转为冲突）</b>} · {cs.proposed_by} 提出</span>
      </div>
      <div className="space-y-2 sm:hidden">
        {cs.changes.map((c: any) => (
          <div key={c.field} className="rounded-lg bg-slate-50 p-2 text-sm">
            <label className="flex items-center gap-2 font-medium">
              {cs.status === 'proposed' && <input type="checkbox" checked={accept.includes(c.field)} onChange={(e) => setAccept(e.target.checked ? [...accept, c.field] : accept.filter((x) => x !== c.field))} />}
              {c.label}
              {c.decision && <Badge color={c.decision === 'accepted' ? 'green' : 'gray'}>{c.decision === 'accepted' ? '已接受' : '未接受'}</Badge>}
            </label>
            <dl className="kv mt-1 text-xs">
              <dt>原值</dt><dd className="text-slate-500 line-through decoration-slate-300">{fmt(c.old)}</dd>
              <dt>建议</dt><dd>{cs.status === 'proposed' ? <input className="input" value={edits[c.field] ?? c.new ?? ''} onChange={(e) => setEdits({ ...edits, [c.field]: e.target.value })} /> : <b>{fmt(c.new)}</b>}</dd>
              {(c.reason || c.evidence_loc) && <><dt>依据</dt><dd>{c.reason}{c.evidence_loc && ` · ${c.evidence_loc}`}</dd></>}
            </dl>
          </div>
        ))}
      </div>
      <div className="-mx-3 hidden overflow-x-auto sm:block">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-50"><tr>{cs.status === 'proposed' && <th className="th">接受</th>}<th className="th">字段</th><th className="th">原值</th><th className="th">建议值</th><th className="th">依据 / 位置</th></tr></thead>
          <tbody className="divide-y">
            {cs.changes.map((c: any) => (
              <tr key={c.field}>
                {cs.status === 'proposed' && <td className="td"><input type="checkbox" checked={accept.includes(c.field)} onChange={(e) => setAccept(e.target.checked ? [...accept, c.field] : accept.filter((x) => x !== c.field))} /></td>}
                <td className="td">{c.label}</td>
                <td className="td text-slate-500 line-through decoration-slate-300">{fmt(c.old)}</td>
                <td className="td font-medium">{cs.status === 'proposed' ? <input className="input" value={edits[c.field] ?? c.new ?? ''} onChange={(e) => setEdits({ ...edits, [c.field]: e.target.value })} /> : <>{fmt(c.new)} {c.decision && <Badge color={c.decision === 'accepted' ? 'green' : 'gray'}>{c.decision === 'accepted' ? '已接受' : '未接受'}</Badge>}</>}</td>
                <td className="td text-xs text-slate-500">{c.reason}{c.evidence_loc && ` · ${c.evidence_loc}`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {cs.result && <div className="mt-2 text-xs text-slate-600">{cs.result}</div>}
      <div className="mt-2 flex gap-2 [&>button]:flex-1 sm:[&>button]:flex-none">
        {cs.status === 'proposed' && <><button className="btn-primary btn-sm" disabled={!accept.length} onClick={apply}><Check size={14} />确认应用所选</button><button className="btn-ghost btn-sm" onClick={reject}><X size={14} />全部驳回</button></>}
        {cs.status === 'conflict' && <><button className="btn-secondary btn-sm" onClick={rebase}><GitMerge size={14} />基于最新版本重新核对</button><button className="btn-ghost btn-sm" onClick={reject}>驳回</button></>}
      </div>
    </div>
  )
}

function ItemDetail({ id, onChanged }: { id: string; onChanged: () => void }) {
  const toast = useToast()
  const { data: it, reload } = useLoad(() => get(`/api/ingest/${id}`), [id])
  const [pv, setPv] = useState<any>(null)
  if (!it) return <Spinner />
  const refresh = () => { reload(); onChanged() }
  const setStatus = async (status: string) => {
    const note = status === 'rejected' ? prompt('驳回原因') : ''
    if (status === 'rejected' && !note) return
    try { await post(`/api/ingest/${id}/status`, { status, note: note || '' }); refresh() } catch (e) { toast(errMsg(e), 'err') }
  }
  const ev = it.evidence
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge color={ST[it.status][1]}>{ST[it.status][0]}</Badge>
        {it.source === 'agent' && <Badge color="purple">AI 助手提交</Badge>}
        <span className="text-sm">{ev?.title}</span>
        <span className="text-xs text-slate-400">{it.created_by} · {dt(it.created_at)} · 等待 {it.waiting_hours} 小时 · {it.mode === 'suggest' ? '识别并建议更新' : '仅存证'}</span>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <div>
          {ev?.readable && ev.mime?.startsWith('image/') && <img src={ev.url} className="max-h-[60vh] w-full cursor-zoom-in rounded object-contain ring-1 ring-slate-200" onClick={() => setPv(ev)} alt="" />}
          {ev?.readable && ev.mime === 'application/pdf' && <><iframe title="pdf" src={ev.url} className="hidden h-[60vh] w-full rounded border sm:block" /><a className="btn-secondary w-full sm:hidden" href={ev.url} target="_blank" rel="noreferrer">打开 PDF 原件</a></>}
          {ev && !ev.readable && <div className="rounded bg-slate-100 p-6 text-center text-sm text-slate-500">受限资料</div>}
          {ev?.readable && ev.mime === 'text/plain' && <TextPreview url={ev.url} />}
          {it.source === 'agent' && <div className="mt-2 rounded bg-violet-50 p-2 text-xs text-violet-800">这是 AI 助手根据对话内容整理的转述，<b>不是原件</b>。确认前请核对；如有截图、单据原件，请补充上传到目标记录。</div>}
          <div className="mt-2 rounded bg-slate-50 p-2 text-xs text-slate-500">{it.recognition.msg}。资料中的文字只作为业务资料，不会被当作指令执行。</div>
        </div>
        <div className="space-y-3">
          {it.changesets.map((c: any) => <ChangeSetView key={c.id} cs={c} onDone={refresh} />)}
          {!['rejected', 'evidence_only'].includes(it.status) && (
            <div className="rounded-lg border border-dashed p-3">
              <div className="mb-2 text-sm font-semibold">对照原资料提出变更</div>
              <Propose item={it} onDone={refresh} />
            </div>
          )}
          <div className="flex gap-2">
            {it.status === 'inbox' && <button className="btn-secondary btn-sm" onClick={() => setStatus('evidence_only')}>仅存证</button>}
            {!['rejected', 'applied'].includes(it.status) && <button className="btn-ghost btn-sm" onClick={() => setStatus('rejected')}>驳回</button>}
          </div>
        </div>
      </div>
      <Preview link={pv} onClose={() => setPv(null)} />
    </div>
  )
}

function TextPreview({ url }: { url: string }) {
  const [text, setText] = useState('')
  useEffect(() => { fetch(url).then((r) => r.text()).then(setText).catch(() => setText('（无法读取）')) }, [url])
  return <pre className="max-h-[50vh] overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-3 text-sm ring-1 ring-slate-200">{text}</pre>
}

export function Ingest() {
  const toast = useToast()
  const [tab, setTab] = useState('open')
  const [mode, setMode] = useState('suggest')
  const [sp, setSp] = useSearchParams()
  const [sel, setSelState] = useState<string | null>(sp.get('item'))
  const setSel = (id: string | null) => { setSelState(id); setSp(id ? { item: id } : {}, { replace: true }) }
  const statusQ = tab === 'open' ? 'inbox,pending_confirm,conflict,processing' : tab
  const list = useLoad(() => get(`/api/ingest?status=${statusQ}`), [statusQ])
  const counts = useLoad(() => get('/api/ingest/counts'), [])
  const send = async (files: FileList) => {
    for (const f of Array.from(files)) {
      const fd = new FormData(); fd.append('file', f); fd.append('mode', mode)
      try { const r = await upload('/api/ingest', fd); if (r.dedup) toast(`${f.name}：相同文件已存在，已引用原件`) } catch (e) { toast(`${f.name}：${errMsg(e)}`, 'err') }
    }
    list.reload(); counts.reload()
  }
  const c = counts.data || {}
  const tabs = [
    { key: 'open', label: `待处理 ${(c.inbox || 0) + (c.pending_confirm || 0) + (c.conflict || 0)}` },
    { key: 'conflict', label: `冲突 ${c.conflict || 0}` }, { key: 'applied', label: `已应用 ${c.applied || 0}` },
    { key: 'evidence_only', label: `仅存证 ${c.evidence_only || 0}` }, { key: 'rejected', label: `已驳回 ${c.rejected || 0}` },
  ]
  return (
    <div className="mx-auto max-w-7xl">
      <h1 className="mb-3 text-xl font-bold">资料接入与更新中心</h1>
      <div className="card mb-4 p-4">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
            <label className="flex min-h-[36px] items-center gap-1.5"><input type="radio" checked={mode === 'suggest'} onChange={() => setMode('suggest')} />识别并建议更新</label>
            <label className="flex min-h-[36px] items-center gap-1.5"><input type="radio" checked={mode === 'evidence_only'} onChange={() => setMode('evidence_only')} />仅存证</label>
          </div>
          <PickButtons onFiles={send} />
          <span className="text-xs text-slate-400">截图、收据、BOL、名片…；Gmail / QuickBooks 接入在 P2 阶段</span>
        </div>
      </div>
      <div className="card p-4">
        <Tabs tabs={tabs} value={tab} onChange={(k) => { setTab(k); setSel(null) }} />
        <div className="grid gap-4 pt-4 lg:grid-cols-[320px_1fr]">
          <div className={`space-y-1 ${sel ? 'hidden lg:block' : ''}`}>
            {(list.data || []).map((it: any) => (
              <button key={it.id} onClick={() => setSel(it.id)} className={`flex w-full items-center gap-2 rounded p-2 text-left text-sm ${sel === it.id ? 'bg-brand-50' : 'hover:bg-slate-50'}`}>
                {it.evidence?.mime?.startsWith('image/') && it.evidence.url ? <img src={it.evidence.url} className="h-10 w-10 rounded object-cover" alt="" /> : <div className="h-10 w-10 rounded bg-slate-100" />}
                <div className="min-w-0 flex-1"><div className="truncate">{it.evidence?.title}</div><div className="text-xs text-slate-400">{dt(it.created_at)} · {it.created_by}</div></div>
                <div className="flex flex-col items-end gap-1"><Badge color={ST[it.status][1]}>{ST[it.status][0]}</Badge>{it.source === 'agent' && <Badge color="purple">AI 助手</Badge>}</div>
              </button>
            ))}
            {list.data && !list.data.length && <Empty />}
          </div>
          <div className={sel ? '' : 'hidden lg:block'}>
            {sel && <button className="btn-ghost -ml-2 mb-2 lg:hidden" onClick={() => setSel(null)}><ArrowLeft size={16} />返回列表</button>}
            {sel ? <ItemDetail key={sel} id={sel} onChanged={() => { list.reload(); counts.reload() }} /> : <Field label=""><div className="text-sm text-slate-400">选择左侧资料查看原件并确认变更</div></Field>}
          </div>
        </div>
      </div>
    </div>
  )
}
