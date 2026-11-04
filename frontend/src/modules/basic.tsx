import { Fragment, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { AlertTriangle, CircleHelp, Paperclip, Plus, Upload } from 'lucide-react'
import { get, patch, post } from '../lib/api'
import { useAuth } from '../lib/auth'
import { ago, dt } from '../lib/fmt'
import { t } from '../lib/i18n'
import { Badge, Empty, ErrorBox, Field, Modal, Segmented, Spinner, TASK_COLOR, errMsg, useLoad, useToast } from '../components/ui'
import { EvidencePanel, PickButtons } from '../components/Evidence'
import { FieldInput, fieldDisplay, useUsers } from '../components/FieldInput'
import { upload } from '../lib/api'

export type ModProps = { module: any; page: any; preview?: boolean }

// ---------- metric ----------
export function MetricModule({ module }: ModProps) {
  const key = module.config.metric
  const { data, error } = useLoad(() => get(`/api/metrics?keys=${encodeURIComponent(key)}`), [key])
  const [show, setShow] = useState(false)
  if (error) return <ErrorBox error={error} />
  const m = data?.[0]
  if (!m) return <Spinner />
  const color = m.status === 'unknown' ? 'text-slate-400' : m.status === 'warn' ? 'text-amber-600' : 'text-emerald-600'
  const body = (
    <div>
      <div className="flex items-start justify-between gap-1">
        <div className="text-xs leading-tight text-slate-500 sm:text-sm">{module.title || m.label}</div>
        <button className="-m-3 flex h-11 w-11 shrink-0 items-center justify-center text-slate-300 hover:text-slate-500" onClick={(e) => { e.preventDefault(); e.stopPropagation(); setShow(!show) }} aria-label="统计口径"><CircleHelp size={16} /></button>
      </div>
      <div className={`mt-1 text-2xl font-bold sm:text-3xl ${color}`}>{m.value === null ? t('common.unknown') : m.value}</div>
      <div className="mt-0.5 text-[11px] text-slate-400 sm:text-xs">{m.status === 'unknown' ? '数据不足，不能判断为正常' : `更新于 ${ago(m.computed_at)}`}{m.drill && <span className="ml-1 text-brand-700">›</span>}</div>
      {show && <div className="mt-2 rounded bg-slate-50 p-2 text-xs text-slate-600">口径：{m.definition}</div>}
    </div>
  )
  return m.drill ? <Link to={m.drill} className="block">{body}</Link> : body
}

// ---------- records table ----------
export function TableModule({ module }: ModProps) {
  const cfg = module.config
  const nav = useNavigate()
  const users = useUsers()
  const [q, setQ] = useState('')
  const [status, setStatus] = useState(cfg.status_filter || '')
  const rt = useLoad(() => get(`/api/record-types/${cfg.record_type_id}`), [cfg.record_type_id])
  const recs = useLoad(() => get(`/api/records?type=${cfg.record_type_id}${status ? `&status=${encodeURIComponent(status)}` : ''}${q ? `&q=${encodeURIComponent(q)}` : ''}`), [cfg.record_type_id, status, q])
  if (rt.error) return <ErrorBox error={rt.error} />
  if (!rt.data) return <Spinner />
  const fields = Object.fromEntries(rt.data.fields.map((f: any) => [f.id, f]))
  const cols: string[] = cfg.columns?.length ? cfg.columns : ['title', 'status', 'owner', 'updated_at']
  const head = (c: string) => ({ title: '标题', status: t('common.status'), owner: t('common.owner'), updated_at: t('common.updated') } as any)[c] || fields[c]?.label || c
  const cell = (r: any, c: string) => {
    if (c === 'title') return <span className="font-medium text-slate-800">{r.title}</span>
    if (c === 'status') return <Badge color="blue">{r.status}</Badge>
    if (c === 'owner') return r.owner || '—'
    if (c === 'updated_at') return ago(r.updated_at)
    return fields[c] ? fieldDisplay(fields[c], r.data[c], users) : '—'
  }
  return (
    <div>
      <div className="mb-2 grid grid-cols-[1fr_auto] gap-2 sm:flex">
        <input className="input sm:max-w-xs" type="search" placeholder={t('common.search')} value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="input w-auto" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">全部状态</option>
          {rt.data.statuses.map((s: string) => <option key={s}>{s}</option>)}
        </select>
      </div>
      {/* phones: card list; desktop: table */}
      <div className="space-y-2 sm:hidden">
        {(recs.data || []).map((r: any) => (
          <button key={r.id} onClick={() => nav(`/r/${r.id}`)} className="block w-full rounded-lg border border-slate-200 p-3 text-left active:bg-slate-50">
            <div className="flex items-start gap-2"><span className="min-w-0 flex-1 font-medium">{r.title}</span><Badge color="blue">{r.status}</Badge></div>
            <dl className="kv mt-2 text-xs">
              {cols.filter((c) => !['title', 'status'].includes(c)).slice(0, 4).map((c) => <Fragment key={c}><dt>{head(c)}</dt><dd>{cell(r, c)}</dd></Fragment>)}
            </dl>
            {r.evidence_count > 0 && <div className="mt-1 text-xs text-slate-400"><Paperclip size={10} className="inline" /> {r.evidence_count}</div>}
          </button>
        ))}
      </div>
      <div className="-mx-4 hidden overflow-x-auto sm:block">
        <table className="min-w-full">
          <thead className="bg-slate-50"><tr>{cols.map((c) => <th key={c} className="th">{head(c)}</th>)}<th className="th"><Paperclip size={12} /></th></tr></thead>
          <tbody className="divide-y">
            {(recs.data || []).map((r: any) => (
              <tr key={r.id} className="cursor-pointer hover:bg-slate-50" onClick={() => nav(`/r/${r.id}`)}>
                {cols.map((c) => <td key={c} className="td">{cell(r, c)}</td>)}
                <td className="td text-xs text-slate-400">{r.evidence_count || ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {recs.data && !recs.data.length && <Empty />}
    </div>
  )
}

// ---------- form ----------
export function FormModule({ module, preview }: ModProps) {
  const cfg = module.config
  const toast = useToast()
  const nav = useNavigate()
  const rt = useLoad(() => get(`/api/record-types/${cfg.record_type_id}`), [cfg.record_type_id])
  const [title, setTitle] = useState('')
  const [data, setData] = useState<any>({})
  const [busy, setBusy] = useState(false)
  if (rt.error) return <ErrorBox error={rt.error} />
  if (!rt.data) return <Spinner />
  const submit = async (e: any) => {
    e.preventDefault()
    if (preview) { toast('预览模式不会创建记录'); return }
    setBusy(true)
    try {
      const r = await post('/api/records', { record_type_id: cfg.record_type_id, title, data })
      toast('已创建')
      setTitle(''); setData({})
      nav(`/r/${r.id}`)
    } catch (err) { toast(errMsg(err), 'err') } finally { setBusy(false) }
  }
  return (
    <form onSubmit={submit} className="space-y-3">
      <Field label="标题 *"><input className="input" required value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
      {rt.data.fields.filter((f: any) => f.active).map((f: any) => (
        <Field key={f.id} label={`${f.label}${f.required ? ' *' : ''}`}>
          <FieldInput f={f} value={data[f.id]} onChange={(v) => setData({ ...data, [f.id]: v })} />
        </Field>
      ))}
      <button className="btn-primary w-full" disabled={busy}><Plus size={16} />{t('common.add')}{rt.data.name}</button>
    </form>
  )
}

// ---------- kanban (tasks or records) ----------
const TASK_COLS = ['todo', 'doing', 'blocked', 'review', 'done']

export function TaskCard({ task }: { task: any }) {
  return (
    <Link to={`/t/${task.id}`} className="block rounded-lg bg-white p-3 text-sm shadow-sm ring-1 ring-slate-200 hover:ring-brand-500">
      <div className="flex items-start gap-1.5">
        {task.kind === 'exception' && <AlertTriangle size={14} className="mt-0.5 shrink-0 text-red-500" />}
        <span className="font-medium">{task.title}</span>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-1 text-xs text-slate-500">
        <span>{task.owner || '未指派'}</span>
        {task.due_at && <span>· {dt(task.due_at, { dateOnly: true })}</span>}
        {task.evidence.count > 0 && <span className="flex items-center gap-0.5">· <Paperclip size={10} />{task.evidence.approved}/{task.evidence.count}</span>}
        {task.flags.map((f: string) => <Badge key={f} color={f === '逾期' || f === '缺证据' ? 'red' : 'purple'}>{f}</Badge>)}
      </div>
      {task.next_step && <div className="mt-1 truncate text-xs text-slate-400">{t('task.nextStep')}：{task.next_step}</div>}
    </Link>
  )
}

export function NewTaskButton({ onCreated, defaults = {}, label }: { onCreated: () => void; defaults?: any; label?: string }) {
  const { can } = useAuth()
  const users = useUsers()
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [f, setF] = useState<any>({ title: '', kind: can('task.manage') ? 'task' : 'exception', requires_evidence: false, ...defaults })
  const save = async () => {
    try {
      await post('/api/tasks', { ...f, due_at: f.due_at ? new Date(f.due_at).toISOString() : null })
      setOpen(false); setF({ title: '', kind: f.kind, requires_evidence: false, ...defaults }); onCreated()
    } catch (e) { toast(errMsg(e), 'err') }
  }
  return (
    <>
      <button className="btn-secondary btn-sm" onClick={() => setOpen(true)}><Plus size={14} />{label || (can('task.manage') ? '新任务' : '上报异常')}</button>
      <Modal open={open} onClose={() => setOpen(false)} title={f.kind === 'exception' ? '上报异常' : '新任务'} footer={<button className="btn-primary" onClick={save} disabled={!f.title}>{t('common.save')}</button>}>
        <div className="space-y-3">
          <Field label="标题"><input className="input" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
          {can('task.manage') && (
            <div className="grid grid-cols-2 gap-3">
              <Field label="类型"><select className="input" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}><option value="task">任务</option><option value="exception">异常</option><option value="approval">审批</option></select></Field>
              <Field label={t('common.owner')}><select className="input" value={f.owner_id || ''} onChange={(e) => setF({ ...f, owner_id: e.target.value || null })}><option value="">我</option>{users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</select></Field>
              <Field label={t('common.due')}><input type="datetime-local" className="input" value={f.due_at || ''} onChange={(e) => setF({ ...f, due_at: e.target.value })} /></Field>
              <Field label={t('task.needEvidence')}><input type="checkbox" className="h-5 w-5" checked={f.requires_evidence} onChange={(e) => setF({ ...f, requires_evidence: e.target.checked })} /></Field>
            </div>
          )}
          <Field label={t('task.nextStep')}><input className="input" value={f.next_step || ''} onChange={(e) => setF({ ...f, next_step: e.target.value })} /></Field>
          <Field label={t('task.criteria')}><input className="input" value={f.done_criteria || ''} onChange={(e) => setF({ ...f, done_criteria: e.target.value })} /></Field>
          <Field label="说明"><textarea className="input h-20" value={f.description || ''} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
        </div>
      </Modal>
    </>
  )
}

/** Phones: pick a status, see one vertical list (no sideways scrolling). Defaults to the first non-empty status. */
function MobileColumns({ cols, render }: { cols: { key: string; label: string; items: any[] }[]; render: (x: any) => any }) {
  const [sel, setSel] = useState<string | null>(null)
  const active = sel ?? (cols.find((c) => c.items.length)?.key || cols[0]?.key)
  const cur = cols.find((c) => c.key === active)
  return (
    <div className="sm:hidden">
      <Segmented items={cols.map((c) => ({ key: c.key, label: <>{c.label} <span className="text-xs text-slate-400">{c.items.length}</span></> }))} value={active} onChange={setSel} />
      <div className="mt-2 space-y-2">{cur?.items.map(render)}{cur && !cur.items.length && <Empty />}</div>
    </div>
  )
}

export function KanbanModule({ module, page }: ModProps) {
  const cfg = module.config
  if (cfg.source === 'records') return <RecordKanban module={module} page={page} />
  const tf = cfg.task_filter || {}
  const qs = new URLSearchParams()
  if (tf.kind) qs.set('kind', tf.kind)
  if (tf.status) qs.set('status', tf.status)
  if (tf.owner) qs.set('owner', tf.owner)
  if (tf.source_module) qs.set('source_module', tf.source_module)
  const { data, reload, error } = useLoad(() => get(`/api/tasks?${qs}`), [qs.toString()])
  if (error) return <ErrorBox error={error} />
  const cols: string[] = tf.status ? tf.status.split(',') : TASK_COLS
  return (
    <div>
      <div className="mb-2 flex justify-end"><NewTaskButton onCreated={reload} defaults={tf.kind === 'exception' ? { kind: 'exception', requires_evidence: true } : {}} /></div>
      <MobileColumns cols={cols.map((s) => ({ key: s, label: t(`task.${s}`), items: (data || []).filter((x: any) => x.status === s) }))} render={(x: any) => <TaskCard key={x.id} task={x} />} />
      <div className="-mx-1 hidden gap-3 overflow-x-auto pb-2 sm:flex">
        {cols.map((s: string) => {
          const items = (data || []).filter((x: any) => x.status === s)
          return (
            <div key={s} className="w-64 shrink-0 rounded-lg bg-slate-100 p-2 sm:w-72">
              <div className="mb-2 flex items-center justify-between px-1 text-xs font-semibold text-slate-600"><span>{t(`task.${s}`)}</span><span>{items.length}</span></div>
              <div className="space-y-2">{items.map((x: any) => <TaskCard key={x.id} task={x} />)}</div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function RecordKanban({ module }: ModProps) {
  const cfg = module.config
  const toast = useToast()
  const rt = useLoad(() => get(`/api/record-types/${cfg.record_type_id}`), [cfg.record_type_id])
  const recs = useLoad(() => get(`/api/records?type=${cfg.record_type_id}`), [cfg.record_type_id])
  if (rt.error) return <ErrorBox error={rt.error} />
  if (!rt.data) return <Spinner />
  const move = async (r: any, status: string) => {
    try { await patch(`/api/records/${r.id}`, { version: r.version, status }); recs.reload() } catch (e) { toast(errMsg(e), 'err') }
  }
  const card = (r: any) => (
    <div key={r.id} className="rounded-lg bg-white p-3 text-sm shadow-sm ring-1 ring-slate-200">
      <Link to={`/r/${r.id}`} className="block font-medium hover:text-brand-700">{r.title}</Link>
      <div className="mt-1 flex items-center justify-between gap-2 text-xs text-slate-500">
        <span>{r.owner} · <Paperclip size={10} className="inline" />{r.evidence_count}</span>
        <select className="input w-auto min-h-[36px] py-1 text-base sm:text-xs" aria-label="变更状态" value={r.status} onChange={(e) => move(r, e.target.value)}>
          {rt.data.statuses.map((x: string) => <option key={x}>{x}</option>)}
        </select>
      </div>
    </div>
  )
  return (
    <>
    <MobileColumns cols={rt.data.statuses.map((s: string) => ({ key: s, label: s, items: (recs.data || []).filter((x: any) => x.status === s) }))} render={card} />
    <div className="-mx-1 hidden gap-3 overflow-x-auto pb-2 sm:flex">
      {rt.data.statuses.map((s: string) => {
        const items = (recs.data || []).filter((x: any) => x.status === s)
        const rule = rt.data.evidence_rules?.[s]
        return (
          <div key={s} className="w-60 shrink-0 rounded-lg bg-slate-100 p-2">
            <div className="mb-1 flex justify-between px-1 text-xs font-semibold text-slate-600"><span>{s}</span><span>{items.length}</span></div>
            {rule && <div className="mb-2 px-1 text-[11px] text-slate-400">需证据：{rule.purposes.join('、')}{rule.need_approved && '（已核验）'}</div>}
            <div className="space-y-2">{items.map(card)}</div>
          </div>
        )
      })}
    </div>
    </>
  )
}

// ---------- text ----------
export function TextModule({ module }: ModProps) {
  const lines = String(module.config.body || '').split('\n')
  return (
    <div className="space-y-1 text-sm leading-6 text-slate-600">
      {lines.map((l, i) => (l.startsWith('- ') ? <div key={i} className="pl-3">• {l.slice(2)}</div> : <p key={i}>{l}</p>))}
    </div>
  )
}

// ---------- evidence area ----------
export function EvidenceModule({ module, preview }: ModProps) {
  if (preview) return <div className="text-sm text-slate-400">证据区（预览模式不上传）</div>
  return <EvidencePanel targetType="module" targetId={module.id} purposes={module.config.purposes?.length ? module.config.purposes : ['现场照片', '附件']} />
}

// ---------- timeline / comments ----------
export function Timeline({ targetType, targetId }: { targetType: string; targetId: string }) {
  const { data, reload } = useLoad(() => get(`/api/comments?target_type=${targetType}&target_id=${targetId}`), [targetType, targetId])
  const [body, setBody] = useState('')
  const toast = useToast()
  const send = async (e: any) => {
    e.preventDefault()
    try { await post('/api/comments', { target_type: targetType, target_id: targetId, body }); setBody(''); reload() } catch (err) { toast(errMsg(err), 'err') }
  }
  return (
    <div>
      <div className="space-y-3">
        {(data || []).map((c: any) => (
          <div key={c.id} className="text-sm"><span className="font-medium">{c.user}</span> <span className="text-xs text-slate-400">{dt(c.created_at)}</span><div className="whitespace-pre-wrap text-slate-600">{c.body}</div></div>
        ))}
        {data && !data.length && <div className="text-xs text-slate-400">暂无沟通记录</div>}
      </div>
      <form onSubmit={send} className="mt-3 flex gap-2">
        <input className="input" placeholder="写评论，@姓名 提及同事" value={body} onChange={(e) => setBody(e.target.value)} />
        <button className="btn-secondary" disabled={!body.trim()}>发送</button>
      </form>
    </div>
  )
}

export function TimelineModule({ module, preview }: ModProps) {
  if (preview) return <div className="text-sm text-slate-400">评论区（预览）</div>
  return <Timeline targetType="module" targetId={module.id} />
}

// ---------- quick ingest ----------
export function IngestModule({ preview }: ModProps) {
  const toast = useToast()
  const [mode, setMode] = useState('suggest')
  const [busy, setBusy] = useState(0)
  const send = async (files: FileList) => {
    if (preview) return
    setBusy(files.length)
    for (const f of Array.from(files)) {
      const fd = new FormData()
      fd.append('file', f); fd.append('mode', mode)
      try { await upload('/api/ingest', fd) } catch (e) { toast(`${f.name}：${errMsg(e)}`, 'err') }
      setBusy((n) => n - 1)
    }
    toast('已送入资料中心')
  }
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-x-4 gap-y-2 text-sm">
        <label className="flex min-h-[36px] items-center gap-1.5"><input type="radio" checked={mode === 'suggest'} onChange={() => setMode('suggest')} />识别并建议更新</label>
        <label className="flex min-h-[36px] items-center gap-1.5"><input type="radio" checked={mode === 'evidence_only'} onChange={() => setMode('evidence_only')} />仅存证</label>
      </div>
      <PickButtons onFiles={send} />
      {busy > 0 && <div className="text-xs text-slate-500"><Upload size={12} className="inline" /> 上传中 {busy}…</div>}
      <Link to="/ingest" className="text-xs text-brand-700 underline">打开资料中心</Link>
    </div>
  )
}

export const taskStatusBadge = (s: string) => <Badge color={TASK_COLOR[s]}>{t(`task.${s}`)}</Badge>
