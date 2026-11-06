import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { AlertTriangle, ArrowLeft } from 'lucide-react'
import { get, patch } from '../lib/api'
import { useAuth } from '../lib/auth'
import { dt, toLocalInput } from '../lib/fmt'
import { t } from '../lib/i18n'
import { Badge, EV_COLOR, ErrorBox, Field, Spinner, TASK_COLOR, Tabs, errMsg, useLoad, useToast } from '../components/ui'
import { EvidencePanel, Preview } from '../components/Evidence'
import { FieldInput, fieldDisplay, useUsers } from '../components/FieldInput'
import { TaskCard, Timeline, NewTaskButton } from '../modules/basic'

function HistoryList({ history }: { history: any[] }) {
  return (
    <div className="space-y-2 text-xs">
      {history.map((h) => (
        <div key={h.version} className="rounded bg-slate-50 p-2">
          <div className="text-slate-500">v{h.version} · {h.by} · {dt(h.at)}{h.via !== 'manual' && <Badge color="purple">{h.via.startsWith('ingest') ? '资料中心更新' : h.via}</Badge>}</div>
          <div className="mt-1 font-mono text-[11px] text-slate-700">
            {Object.entries(h.diff).map(([k, v]: any) => <div key={k}>{k}: {Array.isArray(v) ? `${JSON.stringify(v[0])} → ${JSON.stringify(v[1])}` : JSON.stringify(v)}</div>)}
          </div>
        </div>
      ))}
    </div>
  )
}

// ---------- record ----------
export function RecordDetail() {
  const { id } = useParams()
  const toast = useToast()
  const users = useUsers()
  const { data, error, reload } = useLoad(() => get(`/api/records/${id}`), [id])
  const [edit, setEdit] = useState<any>(null)
  const [tab, setTab] = useState('evidence')
  if (error) return <ErrorBox error={error} />
  if (!data) return <Spinner />
  const { record: r, type: rt } = data
  const fields = rt.fields
  const rulePurposes = [...new Set(Object.values(rt.evidence_rules || {}).flatMap((x: any) => x.purposes))] as string[]
  const save = async (extra: any = {}) => {
    try {
      await patch(`/api/records/${id}`, { version: r.version, ...(edit || {}), ...extra })
      setEdit(null); reload(); toast('已保存')
    } catch (e: any) { toast(errMsg(e), 'err'); if (e.status === 409) reload() }
  }
  return (
    <div className="mx-auto max-w-5xl">
      <button onClick={() => history.back()} className="btn-ghost mb-2"><ArrowLeft size={16} />返回</button>
      <div className="card p-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-slate-400">{rt.name}</span>
          <h1 className="w-full text-lg font-bold sm:w-auto sm:text-xl">{r.title}</h1>
          <select className="input w-full sm:w-auto" value={r.status} disabled={!data.can_edit} onChange={(e) => save({ status: e.target.value })}>
            {rt.statuses.map((s: string) => <option key={s}>{s}</option>)}
          </select>
          {rt.evidence_rules?.[r.status] && <Badge color="green">满足「{r.status}」证据要求</Badge>}
          <div className="w-full text-xs text-slate-400 sm:ml-auto sm:w-auto">ID {r.id} · v{r.version} · 更新 {dt(r.updated_at)}</div>
        </div>
        {Object.keys(rt.evidence_rules || {}).length > 0 && (
          <div className="mt-2 text-xs text-slate-500">证据规则：{Object.entries(rt.evidence_rules).map(([s, x]: any) => `${s} → ${x.purposes.join('、')}${x.need_approved ? '（需核验通过）' : ''}`).join('；')}</div>
        )}
        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <Field label={t('common.owner')}>
            {edit ? <select className="input" value={edit.owner_id ?? r.owner_id ?? ''} onChange={(e) => setEdit({ ...edit, owner_id: e.target.value })}>{users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</select> : <div className="text-sm">{r.owner || '—'}</div>}
          </Field>
          {fields.map((f: any) => (
            <Field key={f.id} label={<>{f.label}{!f.active && <span className="ml-1 text-slate-400">（已停用）</span>}</>}>
              {edit && f.active ? <FieldInput f={f} value={edit.data?.[f.id] ?? r.data[f.id]} onChange={(v) => setEdit({ ...edit, data: { ...(edit.data || {}), [f.id]: v } })} /> : <div className="text-sm">{fieldDisplay(f, r.data[f.id], users)}</div>}
            </Field>
          ))}
        </div>
        {data.can_edit && (
          <div className="mt-4 flex gap-2">
            {edit ? <><button className="btn-primary" onClick={() => save()}>{t('common.save')}</button><button className="btn-ghost" onClick={() => setEdit(null)}>{t('common.cancel')}</button></>
              : <button className="btn-secondary" onClick={() => setEdit({})}>{t('common.edit')}</button>}
          </div>
        )}
      </div>
      <div className="card mt-4 p-4">
        <Tabs value={tab} onChange={setTab} tabs={[{ key: 'evidence', label: t('ev.title') }, { key: 'tasks', label: `任务（${data.tasks.length}）` }, { key: 'timeline', label: '沟通' }, { key: 'history', label: '修改历史' }]} />
        <div className="pt-4">
          {tab === 'evidence' && <EvidencePanel targetType="record" targetId={r.id} purposes={[...rulePurposes, '附件', '报价', '发票', '合同']} onChange={reload} />}
          {tab === 'tasks' && (
            <div className="space-y-2">
              <NewTaskButton onCreated={reload} defaults={{ target_type: 'record', target_id: r.id }} />
              {data.tasks.map((x: any) => <Link key={x.id} to={`/t/${x.id}`} className="block rounded border p-2 text-sm">{x.title} <Badge color={TASK_COLOR[x.status]}>{t(`task.${x.status}`)}</Badge></Link>)}
            </div>
          )}
          {tab === 'timeline' && <Timeline targetType="record" targetId={r.id} />}
          {tab === 'history' && <HistoryList history={data.history} />}
        </div>
      </div>
    </div>
  )
}

// ---------- task ----------
export function TaskDetail() {
  const { id } = useParams()
  const toast = useToast()
  const users = useUsers()
  const { me } = useAuth()
  const { data, error, reload } = useLoad(() => get(`/api/tasks/${id}`), [id])
  const [f, setF] = useState<any>(null)
  useEffect(() => { if (data) setF(null) }, [data])
  if (error) return <ErrorBox error={error} />
  if (!data) return <Spinner />
  const tk = data.task
  const go = async (body: any) => {
    try { await patch(`/api/tasks/${id}`, { version: tk.version, ...body }); reload() } catch (e: any) { toast(errMsg(e), 'err'); if (e.status === 409 && String(e.message).includes('修改')) reload() }
  }
  const override = async () => {
    const reason = prompt('例外放行原因（将保留「例外放行」标签并记入审计）：')
    if (reason) go({ status: 'done', override_reason: reason })
  }
  const canEdit = data.can_verify || tk.owner_id === me?.id || me?.perms.includes('task.manage')
  return (
    <div className="mx-auto max-w-4xl pb-16 sm:pb-0">
      <button onClick={() => history.back()} className="btn-ghost mb-2"><ArrowLeft size={16} />返回</button>
      <div className="card p-4">
        <div className="flex flex-wrap items-center gap-2">
          {tk.kind === 'exception' && <Badge color="red"><AlertTriangle size={12} />异常</Badge>}
          <Badge color={TASK_COLOR[tk.status]}>{t(`task.${tk.status}`)}</Badge>
          {tk.flags.map((x: string) => <Badge key={x} color={x === '例外放行' ? 'purple' : 'red'}>{x}</Badge>)}
          {tk.requires_evidence && <Badge color="blue">{t('task.needEvidence')}</Badge>}
        </div>
        <h1 className="mt-2 text-lg font-bold sm:text-xl">{tk.title}</h1>
        {tk.description && <p className="mt-1 whitespace-pre-wrap text-sm text-slate-600">{tk.description}</p>}
        {tk.exception_flag && <p className="mt-1 text-sm text-violet-700">例外放行原因：{tk.exception_flag}</p>}
        {f ? (
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <Field label={t('common.owner')}><select className="input" value={f.owner_id ?? tk.owner_id ?? ''} onChange={(e) => setF({ ...f, owner_id: e.target.value })}>{users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</select></Field>
            <Field label={t('common.due')}><input className="input" type="datetime-local" value={f.due ?? toLocalInput(tk.due_at)} onChange={(e) => setF({ ...f, due: e.target.value })} /></Field>
            <Field label={t('task.nextStep')}><input className="input" value={f.next_step ?? tk.next_step} onChange={(e) => setF({ ...f, next_step: e.target.value })} /></Field>
            <Field label={t('task.criteria')}><input className="input" value={f.done_criteria ?? tk.done_criteria} onChange={(e) => setF({ ...f, done_criteria: e.target.value })} /></Field>
            <div className="flex gap-2 sm:col-span-2">
              <button className="btn-primary" onClick={() => { const { due, ...rest } = f; go({ ...rest, ...(due !== undefined ? { due_at: due ? new Date(due).toISOString() : null } : {}) }) }}>{t('common.save')}</button>
              <button className="btn-ghost" onClick={() => setF(null)}>{t('common.cancel')}</button>
            </div>
          </div>
        ) : (
          <div className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
            <div><span className="label">{t('common.owner')}</span>{tk.owner || '—'}</div>
            <div><span className="label">{t('common.due')}</span>{dt(tk.due_at, { site: true })}</div>
            <div><span className="label">{t('task.nextStep')}</span>{tk.next_step || '—'}</div>
            <div><span className="label">{t('task.criteria')}</span>{tk.done_criteria || '—'}</div>
            {tk.target_type === 'record' && <div><span className="label">关联</span><Link className="text-brand-700 underline" to={`/r/${tk.target_id}`}>查看记录</Link></div>}
            {tk.target_type === 'so' && <div><span className="label">关联</span><Link className="text-brand-700 underline" to={`/orders?so=${tk.target_id}`}>查看订单</Link></div>}
            {tk.target_type === 'po' && <div><span className="label">关联</span><Link className="text-brand-700 underline" to={`/purchasing?po=${tk.target_id}`}>查看采购单</Link></div>}
            <div><span className="label">证据</span>{tk.evidence.count} 份，已核验 {tk.evidence.approved}</div>
          </div>
        )}
        {canEdit && !f && !['done', 'cancelled'].includes(tk.status) && (
          <div className="fixed inset-x-0 bottom-[calc(56px+env(safe-area-inset-bottom))] z-20 flex gap-2 overflow-x-auto border-t bg-white/95 px-3 py-2 backdrop-blur sm:static sm:mt-4 sm:flex-wrap sm:border-0 sm:bg-transparent sm:p-0 lg:bottom-0 [&>button]:shrink-0">
            {tk.status === 'todo' && <button className="btn-secondary" onClick={() => go({ status: 'doing' })}>{t('task.start')}</button>}
            {tk.status !== 'blocked' && <button className="btn-secondary" onClick={() => go({ status: 'blocked' })}>{t('task.block')}</button>}
            {tk.status === 'blocked' && <button className="btn-secondary" onClick={() => go({ status: 'doing' })}>解除受阻</button>}
            {tk.status !== 'review' && <button className="btn-primary" onClick={() => go({ status: 'review' })}>{t('task.reportDone')}</button>}
            {data.can_verify && <button className="btn-primary" onClick={() => go({ status: 'done' })}>核验通过并完成</button>}
            {data.can_override && tk.requires_evidence && tk.evidence.approved === 0 && <button className="btn-ghost text-violet-700" onClick={override}>例外放行…</button>}
            <button className="btn-ghost" onClick={() => setF({})}>{t('common.edit')}</button>
          </div>
        )}
        {tk.status === 'review' && tk.requires_evidence && tk.evidence.approved === 0 && <p className="mt-2 text-xs text-amber-700">待核验：需要至少一份证据核验通过后才能完成。现场“已报告完成”不等于已核验完成。</p>}
      </div>
      <div className="card mt-4 p-4">
        <EvidencePanel title={t('ev.title')} targetType="task" targetId={tk.id} purposes={tk.kind === 'exception' ? ['整改证明', '复查照片', '附件'] : ['完成证明', '附件']} onChange={reload} />
      </div>
      {tk.target_type === 'checklist_run' && (
        <div className="card mt-4 p-4"><div className="mb-2 text-sm font-semibold">原检查记录的证据</div><EvidencePanel targetType="checklist_run" targetId={tk.target_id} compact /></div>
      )}
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <div className="card p-4"><div className="mb-2 text-sm font-semibold">沟通</div><Timeline targetType="task" targetId={tk.id} /></div>
        <div className="card p-4"><div className="mb-2 text-sm font-semibold">修改历史</div><HistoryList history={data.history} /></div>
      </div>
    </div>
  )
}

// ---------- all tasks with drill filter ----------
export function TasksList() {
  const [sp] = useSearchParams()
  const f = sp.get('f') || ''
  const q = ({ overdue: 'open_only=true', blocked: 'status=blocked', review: 'status=review', exceptions: 'kind=exception&open_only=true' } as any)[f] || 'open_only=true'
  const { data, reload } = useLoad(() => get(`/api/tasks?${q}`), [q])
  const title = ({ overdue: '逾期任务', blocked: '受阻任务', review: '待核验任务', exceptions: '未关闭异常' } as any)[f] || '未完成任务'
  const items = (data || []).filter((x: any) => f !== 'overdue' || x.flags.includes('逾期'))
  return (
    <div className="mx-auto max-w-4xl">
      <div className="mb-3 flex items-center"><h1 className="text-xl font-bold">{title}（{items.length}）</h1><div className="ml-auto"><NewTaskButton onCreated={reload} /></div></div>
      <div className="grid gap-2 sm:grid-cols-2">{items.map((x: any) => <TaskCard key={x.id} task={x} />)}</div>
    </div>
  )
}

// ---------- evidence ----------
export function EvidenceDetail() {
  const { id } = useParams()
  const { data, error } = useLoad(() => get(`/api/evidence/${id}`), [id])
  const [pv, setPv] = useState<any>(null)
  if (error) return <ErrorBox error={error} />
  if (!data) return <Spinner />
  const linkTo = (l: any) => ({ record: `/r/${l.target_id}`, task: `/t/${l.target_id}`, po: `/purchasing?po=${l.target_id}`, so: `/orders?so=${l.target_id}` } as any)[l.target_type]
  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <button onClick={() => history.back()} className="btn-ghost"><ArrowLeft size={16} />返回</button>
      <div className="card p-4">
        <h1 className="text-xl font-bold">{data.title}</h1>
        <div className="text-xs text-slate-500">ID {data.id} · 敏感级别 {data.sensitivity}</div>
        <div className="mt-4 text-sm font-semibold">版本</div>
        <div className="mt-2 space-y-2">
          {data.versions.map((v: any) => (
            <div key={v.id} className="flex flex-wrap items-center gap-2 rounded border p-2 text-sm">
              <b>v{v.version_no}</b>{v.id === data.current_version_id && <Badge color="green">当前</Badge>}
              <span>{v.filename}</span><span className="font-mono text-xs text-slate-400">sha256 {v.sha256.slice(0, 16)}…</span>
              <span className="text-xs text-slate-500">{v.uploaded_by} · {dt(v.uploaded_at)} · 来源 {v.source}</span>
              <button className="btn-secondary btn-sm sm:ml-auto" onClick={() => setPv({ ...v, title: data.title, evidence_id: data.id, sha256: v.sha256.slice(0, 12) })}>预览</button>
            </div>
          ))}
        </div>
        <div className="mt-4 text-sm font-semibold">业务关联（每处单独核验）</div>
        <div className="mt-2 space-y-1">
          {data.links.map((l: any) => (
            <div key={l.link_id} className={`flex flex-wrap items-center gap-2 text-sm ${l.active ? '' : 'text-slate-400 line-through'}`}>
              <Badge color="blue">{l.purpose}</Badge><Badge color={EV_COLOR[l.verify_status]}>{t(`ev.${l.verify_status}`)}</Badge>
              <span>{l.target_type}</span>{linkTo(l) ? <Link className="text-brand-700 underline" to={linkTo(l)}>{l.target_id}</Link> : <span className="text-xs">{l.target_id}</span>}
              <span className="text-xs text-slate-400">v{l.version_no} · {l.linked_by} {dt(l.linked_at)}</span>
            </div>
          ))}
        </div>
      </div>
      <Preview link={pv} onClose={() => setPv(null)} />
    </div>
  )
}
