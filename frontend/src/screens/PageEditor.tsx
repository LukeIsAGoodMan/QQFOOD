import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { DndContext, KeyboardSensor, PointerSensor, TouchSensor, closestCenter, useSensor, useSensors, type DragEndEvent } from '@dnd-kit/core'
import { SortableContext, arrayMove, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { ArrowDown, ArrowLeft, ArrowUp, Copy, Eye, EyeOff, GripVertical, History, Monitor, Plus, Rocket, Settings2, Smartphone, Trash2 } from 'lucide-react'
import { ApiError, get, patch, post, put } from '../lib/api'
import { dt } from '../lib/fmt'
import { Badge, ErrorBox, Field, Modal, Segmented, Spinner, errMsg, useLoad, useToast } from '../components/ui'
import { MODULES, ModuleCard, ModuleConfig } from '../modules/registry'
import { PAGE_ICONS, bumpPages } from './Shell'

const newId = () => `mod_${Math.random().toString(16).slice(2, 14)}`
const METRIC_NAMES: Record<string, string> = {
  overdue_tasks: '逾期任务', blocked_tasks: '受阻任务', review_tasks: '待核验任务', open_exceptions: '未关闭异常', quarantined_lots: '隔离/待放行批次',
  below_safety: '低于安全库存', unverified_evidence: '待核验证据', pending_ingest: '资料待处理', in_transit: '在途调拨', missing_checklists: '今日应填未提交',
}

function Row({ m, selected, onSelect, onUp, onDown, onHide, onDup, onRemove }: any) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: m.id })
  const def = MODULES[m.type]
  return (
    <div ref={setNodeRef} style={{ transform: CSS.Transform.toString(transform), transition }}
      className={`flex items-center gap-1 rounded-lg border bg-white p-2 text-sm ${selected ? 'border-brand-500 ring-2 ring-brand-100' : 'border-slate-200'} ${isDragging ? 'z-10 shadow-lg' : ''} ${m.hidden ? 'opacity-60' : ''}`}>
      <button className="-my-2 -ml-1 flex h-11 w-9 shrink-0 cursor-grab touch-none items-center justify-center text-slate-400" {...attributes} {...listeners} aria-label="拖动排序"><GripVertical size={18} /></button>
      <button className="min-w-0 flex-1 text-left" onClick={onSelect}>
        <div className="truncate font-medium">{m.title || (m.type === 'metric' ? `指标：${METRIC_NAMES[m.config?.metric] || m.config?.metric || '未选择'}` : def?.label || m.type)}</div>
        <div className="text-xs text-slate-400">{def?.label || `未知组件 ${m.type}`} · {({ full: '整行', half: '半行', third: '1/3' } as any)[m.width]}</div>
      </button>
      <button className="btn-ghost btn-sm px-1" onClick={onUp} aria-label="上移"><ArrowUp size={14} /></button>
      <button className="btn-ghost btn-sm px-1" onClick={onDown} aria-label="下移"><ArrowDown size={14} /></button>
      <button className="btn-ghost btn-sm px-1" onClick={onHide} title={m.hidden ? '显示' : '隐藏'}>{m.hidden ? <EyeOff size={14} /> : <Eye size={14} />}</button>
      <button className="btn-ghost btn-sm hidden px-1 sm:inline-flex" onClick={onDup} title="复制模块"><Copy size={14} /></button>
      <button className="btn-ghost btn-sm px-1 text-red-500" onClick={onRemove} title="移除"><Trash2 size={14} /></button>
    </div>
  )
}

function PageSettings({ page, onClose, onSaved }: any) {
  const toast = useToast()
  const roles = useLoad(() => get('/api/roles'), [])
  const [f, setF] = useState({ name: page.name, icon: page.icon, nav_group: page.nav_group, description: page.description, visible_roles: page.visible_roles, status: page.status })
  const save = async () => {
    try { await patch(`/api/pages/${page.id}`, f); onSaved(); onClose(); bumpPages() } catch (e) { toast(errMsg(e), 'err') }
  }
  return (
    <Modal open onClose={onClose} title="页面设置" footer={<button className="btn-primary" onClick={save}>保存</button>}>
      <div className="space-y-3">
        <Field label="名称" hint="改名不改变页面 ID 和已有链接"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="导航分组"><input className="input" value={f.nav_group} onChange={(e) => setF({ ...f, nav_group: e.target.value })} /></Field>
          <Field label="图标"><select className="input" value={f.icon} onChange={(e) => setF({ ...f, icon: e.target.value })}>{Object.keys(PAGE_ICONS).map((k) => <option key={k}>{k}</option>)}</select></Field>
        </div>
        <Field label="说明"><input className="input" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
        <Field label="可见角色（不选 = 全部）" hint="页面可见不等于数据权限：模块内的数据和操作仍由服务端按角色校验">
          <div className="flex flex-wrap gap-2">
            {(roles.data || []).map((r: any) => (
              <label key={r.key} className="flex items-center gap-1 text-sm"><input type="checkbox" checked={f.visible_roles.includes(r.key)} onChange={(e) => setF({ ...f, visible_roles: e.target.checked ? [...f.visible_roles, r.key] : f.visible_roles.filter((x: string) => x !== r.key) })} />{r.label}</label>
            ))}
          </div>
        </Field>
        <Field label="状态"><select className="input" value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}><option value="active">启用</option><option value="disabled">停用</option><option value="archived">归档</option></select></Field>
      </div>
    </Modal>
  )
}

function Versions({ pageId, onClose, onRolledBack }: any) {
  const toast = useToast()
  const { data } = useLoad(() => get(`/api/pages/${pageId}/versions`), [pageId])
  const rollback = async (v: any) => {
    if (!confirm(`回滚到 v${v.version_no}？只改变页面配置，不影响业务数据和证据。`)) return
    try { await post(`/api/pages/${pageId}/rollback`, { version_id: v.id }); toast('已回滚并发布'); onRolledBack(); onClose() } catch (e) { toast(errMsg(e), 'err') }
  }
  return (
    <Modal open onClose={onClose} title="发布历史">
      <div className="space-y-2">
        {(data || []).map((v: any) => (
          <div key={v.id} className="flex items-center gap-2 rounded border p-2 text-sm">
            <b>v{v.version_no}</b>{v.current && <Badge color="green">线上</Badge>}
            <span className="text-slate-500">{v.note || '—'}</span>
            <span className="ml-auto text-xs text-slate-400">{v.published_by} · {dt(v.published_at)} · {v.modules} 模块</span>
            {!v.current && <button className="btn-secondary btn-sm" onClick={() => rollback(v)}>回滚</button>}
          </div>
        ))}
      </div>
    </Modal>
  )
}

export function PageEditor() {
  const { id } = useParams()
  const nav = useNavigate()
  const toast = useToast()
  const { data, error, reload } = useLoad(() => get(`/api/pages/${id}?draft=1`), [id])
  const [mods, setMods] = useState<any[]>([])
  const revRef = useRef(0)
  const [sel, setSel] = useState<string | null>(null)
  const [issues, setIssues] = useState<any[]>([])
  const [saving, setSaving] = useState<'idle' | 'saving' | 'saved' | 'conflict'>('idle')
  const [view, setView] = useState<'desktop' | 'mobile'>('desktop')
  const [pane, setPane] = useState<'modules' | 'config' | 'preview'>('modules')
  const [dlg, setDlg] = useState<string | null>(null)
  const [note, setNote] = useState('')
  const dirty = useRef(false)
  const timer = useRef<any>(null)
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }), useSensor(TouchSensor, { activationConstraint: { delay: 150, tolerance: 6 } }), useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }))

  useEffect(() => {
    if (data) { setMods(data.draft.modules); revRef.current = data.page.draft_rev; dirty.current = false }
  }, [data])
  useEffect(() => { if (id) get(`/api/pages/${id}/check`).then((r) => setIssues(r.issues)).catch(() => {}) }, [id])

  const saveChain = useRef<Promise<void>>(Promise.resolve())
  const save = useCallback((modules: any[]) => {
    // serialize saves so each uses the latest draft_rev
    saveChain.current = saveChain.current.then(() => doSave(modules))
    return saveChain.current
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])
  const doSave = async (modules: any[]) => {
    setSaving('saving')
    try {
      const res = await put(`/api/pages/${id}/draft`, { layout: { modules }, draft_rev: revRef.current })
      revRef.current = res.draft_rev; setIssues(res.issues); setSaving('saved'); dirty.current = false
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setSaving('conflict')
      else { toast(errMsg(e), 'err'); setSaving('idle') }
    }
  }

  const update = (next: any[]) => {
    setMods(next)
    dirty.current = true
    clearTimeout(timer.current)
    timer.current = setTimeout(() => save(next), 600)
  }
  useEffect(() => () => clearTimeout(timer.current), [])

  if (error) return <ErrorBox error={error} />
  if (!data) return <Spinner />
  const page = data.page
  const idx = (mid: string) => mods.findIndex((m) => m.id === mid)
  const move = (mid: string, d: number) => { const i = idx(mid), j = i + d; if (j < 0 || j >= mods.length) return; update(arrayMove(mods, i, j)) }
  const onDragEnd = (e: DragEndEvent) => { if (e.over && e.active.id !== e.over.id) update(arrayMove(mods, idx(String(e.active.id)), idx(String(e.over.id)))) }
  const selected = mods.find((m) => m.id === sel)

  const addModule = (type: string) => {
    const def = MODULES[type]
    const m = { id: newId(), type, title: type === 'metric' ? '' : def.label, width: def.width || 'full', hidden: false, version: 1, config: def.defaults() }
    update([...mods, m]); setSel(m.id); setPane('config'); setDlg(null)
  }
  const remove = async (m: any) => {
    try {
      const imp = await get(`/api/pages/${id}/modules/${m.id}/impact`)
      const linked = imp.evidence_links + imp.checklist_runs + imp.comments
      const msg = linked
        ? `该模块下有 ${imp.evidence_links} 份证据关联、${imp.checklist_runs} 次检查记录、${imp.comments} 条评论。\n${imp.note}\n\n确认仅移除展示？`
        : '移除此模块？（仅移除展示，可从历史版本恢复）'
      if (!confirm(msg)) return
      update(mods.filter((x) => x.id !== m.id)); if (sel === m.id) setSel(null)
    } catch (e) { toast(errMsg(e), 'err') }
  }
  const publish = async () => {
    clearTimeout(timer.current)
    if (dirty.current) await save(mods)
    else await saveChain.current
    try {
      const r = await post(`/api/pages/${id}/publish`, { note })
      toast(`已发布 v${r.version_no}`); setDlg(null); setNote(''); bumpPages(); reload()
    } catch (e: any) {
      if (e?.detail?.issues) setIssues(e.detail.issues)
      toast(errMsg(e), 'err')
    }
  }
  const copyPage = async () => {
    const name = prompt('新页面名称', `${page.name}（副本）`)
    if (!name) return
    const mode = confirm('复制只复制布局与配置，不复制业务数据、附件或人员信息。\n\n确定 = 继续引用原数据源\n取消 = 为新页面创建新的记录集合（仅字段结构）') ? 'reference' : 'new_collection'
    try { const r = await post(`/api/pages/${id}/copy`, { name, data_mode: mode }); bumpPages(); nav(`/p/${r.id}/edit`) } catch (e) { toast(errMsg(e), 'err') }
  }
  const groups: Record<string, string[]> = {}
  Object.entries(MODULES).forEach(([k, d]) => (groups[d.group] ||= []).push(k))
  const errors = issues.filter((i) => i.level === 'error')

  return (
    <div className="mx-auto max-w-[1500px]">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Link to={`/p/${id}`} className="btn-ghost" aria-label="返回"><ArrowLeft size={16} /></Link>
        <h1 className="min-w-0 flex-1 truncate text-lg font-bold sm:flex-none">编辑：{page.name}</h1>
        <Badge color={saving === 'conflict' ? 'red' : 'gray'}>{saving === 'saving' ? '保存草稿中…' : saving === 'saved' ? '草稿已保存' : saving === 'conflict' ? '草稿冲突：他人已修改，请刷新' : '草稿'}</Badge>
        {page.has_unpublished && saving !== 'saving' && <Badge color="yellow">有未发布修改</Badge>}
        <div className="flex w-full gap-2 sm:ml-auto sm:w-auto">
          <button className="btn-secondary" onClick={() => setDlg('settings')} aria-label="页面设置"><Settings2 size={16} /><span className="hidden sm:inline">页面设置</span></button>
          <button className="btn-secondary" onClick={() => setDlg('versions')} aria-label="历史"><History size={16} /><span className="hidden sm:inline">历史</span></button>
          <button className="btn-secondary" onClick={copyPage} aria-label="复制页面"><Copy size={16} /><span className="hidden sm:inline">复制页面</span></button>
          <button className="btn-primary flex-1 sm:flex-none" onClick={() => setDlg('publish')}><Rocket size={16} />发布</button>
        </div>
      </div>
      {saving === 'conflict' && <div className="mb-3 rounded bg-red-50 p-3 text-sm text-red-700">其他管理员已修改此草稿。<button className="ml-2 underline" onClick={() => { setSaving('idle'); reload() }}>载入最新草稿</button>（你未保存的修改会丢失）</div>}
      {/* phones/tablets: one pane at a time */}
      <div className="mb-3 lg:hidden">
        <Segmented value={pane} onChange={(k) => setPane(k as any)} items={[
          { key: 'modules', label: `模块 ${mods.length}` },
          { key: 'config', label: selected ? `配置：${selected.title || MODULES[selected.type]?.label || ''}`.slice(0, 14) : '配置' },
          { key: 'preview', label: '预览' },
        ]} />
      </div>
      <div className="grid gap-4 lg:grid-cols-[340px_1fr]">
        <div className={`space-y-3 ${pane === 'preview' ? 'hidden lg:block' : ''}`}>
          <div className={`card p-3 ${pane !== 'modules' ? 'hidden lg:block' : ''}`}>
            <div className="mb-2 flex items-center justify-between"><span className="text-sm font-semibold">模块（{mods.length}）</span><button className="btn-secondary btn-sm" onClick={() => setDlg('add')}><Plus size={14} />添加模块</button></div>
            <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
              <SortableContext items={mods.map((m) => m.id)} strategy={verticalListSortingStrategy}>
                <div className="space-y-1.5">
                  {mods.map((m) => (
                    <Row key={m.id} m={m} selected={sel === m.id} onSelect={() => { setSel(m.id); setPane('config') }} onUp={() => move(m.id, -1)} onDown={() => move(m.id, 1)}
                      onHide={() => update(mods.map((x) => (x.id === m.id ? { ...x, hidden: !x.hidden } : x)))}
                      onDup={() => { const c = { ...structuredClone(m), id: newId(), title: `${m.title}（副本）` }; const i = idx(m.id); update([...mods.slice(0, i + 1), c, ...mods.slice(i + 1)]) }}
                      onRemove={() => remove(m)} />
                  ))}
                </div>
              </SortableContext>
            </DndContext>
          </div>
          {issues.length > 0 && (
            <div className={`card space-y-1 p-3 text-sm ${pane !== 'modules' ? 'hidden lg:block' : ''}`}>
              <div className="font-semibold">发布前检查</div>
              {issues.map((i, k) => <div key={k} className={i.level === 'error' ? 'text-red-600' : 'text-amber-700'}>{i.level === 'error' ? '✗' : '!'} {i.msg}</div>)}
            </div>
          )}
          {pane === 'config' && !selected && <div className="card p-6 text-center text-sm text-slate-400 lg:hidden">在「模块」里点选一个模块进行配置</div>}
          {selected && (
            <div className={`card p-3 ${pane !== 'config' ? 'hidden lg:block' : ''}`}>
              <div className="mb-3 text-sm font-semibold">配置：{MODULES[selected.type]?.label || selected.type}</div>
              <ModuleConfig module={selected} onChange={(m) => update(mods.map((x) => (x.id === m.id ? m : x)))} />
            </div>
          )}
        </div>
        <div className={pane === 'preview' ? '' : 'hidden lg:block'}>
          <div className="mb-2 hidden items-center gap-2 text-sm text-slate-500 lg:flex">
            预览
            <button className={`btn-sm ${view === 'desktop' ? 'btn-secondary' : 'btn-ghost'}`} onClick={() => setView('desktop')}><Monitor size={14} />桌面</button>
            <button className={`btn-sm ${view === 'mobile' ? 'btn-secondary' : 'btn-ghost'}`} onClick={() => setView('mobile')}><Smartphone size={14} />手机</button>
            <span className="text-xs">预览不会写入业务数据</span>
          </div>
          <div className={`rounded-xl bg-slate-200/60 p-2 sm:p-3 ${view === 'mobile' ? 'lg:mx-auto lg:w-[390px]' : ''}`}>
            <div className="grid grid-cols-6 gap-3">
              {mods.map((m) => (
                <div key={m.id} className={`contents ${sel === m.id ? '[&>section]:ring-2 [&>section]:ring-brand-500' : ''}`} onClick={() => setSel(m.id)}>
                  <ModuleCard module={m} page={page} preview phone={view === 'mobile'} />
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      <Modal open={dlg === 'add'} onClose={() => setDlg(null)} title="添加模块" wide>
        {Object.entries(groups).map(([g, keys]) => (
          <div key={g} className="mb-4">
            <div className="mb-2 text-xs font-semibold text-slate-500">{g}</div>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {keys.map((k) => (
                <button key={k} onClick={() => addModule(k)} className="rounded-lg border p-3 text-left hover:border-brand-500 hover:bg-brand-50">
                  <div className="text-sm font-medium">{MODULES[k].label}</div><div className="text-xs text-slate-500">{MODULES[k].description}</div>
                </button>
              ))}
            </div>
          </div>
        ))}
        <p className="text-xs text-slate-400">「业务（专用）」组件调用库存、质量等业务服务，页面配置不能改变其过账和放行逻辑。</p>
      </Modal>
      <Modal open={dlg === 'publish'} onClose={() => setDlg(null)} title="发布页面" footer={<button className="btn-primary" disabled={errors.length > 0} onClick={publish}><Rocket size={16} />发布</button>}>
        {errors.length > 0 ? <div className="mb-3 text-sm text-red-600">有 {errors.length} 个错误需先修正；草稿已保留。</div> : <div className="mb-3 text-sm text-slate-600">发布后，有权限的用户立即看到新布局。可随时回滚到历史版本。</div>}
        <Field label="发布说明"><input className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="例如：新增安装验收模块" /></Field>
      </Modal>
      {dlg === 'settings' && <PageSettings page={page} onClose={() => setDlg(null)} onSaved={reload} />}
      {dlg === 'versions' && <Versions pageId={id} onClose={() => setDlg(null)} onRolledBack={() => { bumpPages(); reload() }} />}
    </div>
  )
}

export function PagesAdmin() {
  const nav = useNavigate()
  const toast = useToast()
  const { data, reload } = useLoad(() => get('/api/pages?all=1'), [])
  const tpls = useLoad(() => get('/api/pages/templates'), [])
  const [f, setF] = useState<any>(null)
  const create = async () => {
    try { const r = await post('/api/pages', f); bumpPages(); nav(`/p/${r.id}/edit`) } catch (e) { toast(errMsg(e), 'err') }
  }
  const status = async (p: any, s: string) => { try { await patch(`/api/pages/${p.id}`, { status: s }); reload(); bumpPages() } catch (e) { toast(errMsg(e), 'err') } }
  return (
    <div className="mx-auto max-w-5xl">
      <div className="mb-4 flex items-center"><h1 className="text-xl font-bold">页面管理</h1><button className="btn-primary ml-auto" onClick={() => setF({ name: '', template: 'periodic_check', nav_group: '业务', icon: 'layout', visible_roles: [] })}><Plus size={16} />新建页面</button></div>
      <div className="card divide-y">
        {(data || []).map((p: any) => (
          <div key={p.id} className="flex flex-wrap items-center gap-2 p-3 text-sm">
            <Link to={`/p/${p.id}`} className="font-medium hover:text-brand-700">{p.name}</Link>
            <span className="text-xs text-slate-400">{p.nav_group}</span>
            {p.status !== 'active' && <Badge color="gray">{p.status === 'disabled' ? '已停用' : '已归档'}</Badge>}
            {!p.published && <Badge color="yellow">未发布</Badge>}
            {p.has_unpublished && p.published && <Badge color="yellow">有草稿</Badge>}
            <div className="flex w-full gap-1 sm:ml-auto sm:w-auto">
              <Link to={`/p/${p.id}/edit`} className="btn-secondary btn-sm">编辑</Link>
              {p.status === 'active' ? <button className="btn-ghost btn-sm" onClick={() => status(p, 'disabled')}>停用</button> : <button className="btn-ghost btn-sm" onClick={() => status(p, 'active')}>恢复</button>}
              {p.status !== 'archived' && <button className="btn-ghost btn-sm" onClick={() => status(p, 'archived')}>归档</button>}
            </div>
          </div>
        ))}
      </div>
      <Modal open={!!f} onClose={() => setF(null)} title="新建页面" footer={<button className="btn-primary" disabled={!f?.name} onClick={create}>创建并编辑</button>}>
        {f && (
          <div className="space-y-3">
            <Field label="页面名称"><input className="input" autoFocus value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="例如：冷库巡检" /></Field>
            <Field label="从模板开始">
              <div className="grid gap-2">
                {(tpls.data || []).map((x: any) => (
                  <label key={x.key} className={`flex cursor-pointer gap-2 rounded-lg border p-2 ${f.template === x.key ? 'border-brand-500 bg-brand-50' : ''}`}>
                    <input type="radio" checked={f.template === x.key} onChange={() => setF({ ...f, template: x.key, icon: x.icon })} />
                    <div><div className="text-sm font-medium">{x.name}</div><div className="text-xs text-slate-500">{x.description}</div></div>
                  </label>
                ))}
              </div>
            </Field>
            <Field label="导航分组"><input className="input" value={f.nav_group} onChange={(e) => setF({ ...f, nav_group: e.target.value })} /></Field>
          </div>
        )}
      </Modal>
    </div>
  )
}
