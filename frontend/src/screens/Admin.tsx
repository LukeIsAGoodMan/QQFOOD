import { useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import { ApiError, del, get, patch, post, put } from '../lib/api'
import { useAuth } from '../lib/auth'
import { dt } from '../lib/fmt'
import { setLang } from '../lib/i18n'
import { Badge, Field, Modal, errMsg, useLoad, useToast } from '../components/ui'
import { FTYPE_LABEL } from '../components/FieldInput'
import { AgentSettings } from './Agent'
import { ResetDemo } from './Batches'
import { RepairInventory } from './RepairInventory'

// ---------- record types & fields ----------
function FieldRow({ f, onChanged, startEditing }: { f: any; onChanged: () => void; startEditing?: boolean }) {
  const toast = useToast()
  const [e, setE] = useState<any>(startEditing ? { ...f } : null)
  const save = async (allow = false) => {
    try { await patch(`/api/fields/${f.id}`, { ...e, allow_existing_empty: allow }); setE(null); onChanged() } catch (err: any) {
      if (err instanceof ApiError && err.detail?.need === 'allow_existing_empty') {
        if (confirm(`${err.detail.msg}\n\n确定 = 旧记录保留空值并在变更状态前要求补齐`)) save(true)
      } else toast(errMsg(err), 'err')
    }
  }
  const remove = async () => { if (!confirm('删除字段？有历史值的字段不能删除，只能停用。')) return; try { await del(`/api/fields/${f.id}`); onChanged() } catch (err) { toast(errMsg(err), 'err') } }
  if (e) return (
    <tr><td className="td" colSpan={5}>
      <div className="grid gap-2 lg:grid-cols-[1.5fr_1fr_2fr_auto_auto_auto]">
        <input className="input" value={e.label} onChange={(x) => setE({ ...e, label: x.target.value })} />
        <select className="input" value={e.ftype} onChange={(x) => setE({ ...e, ftype: x.target.value })}>{Object.entries(FTYPE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        <input className="input" placeholder="选项（逗号分隔）" value={e.options.join(',')} onChange={(x) => setE({ ...e, options: x.target.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean) })} />
        <label className="flex items-center gap-1 text-sm"><input type="checkbox" checked={e.required} onChange={(x) => setE({ ...e, required: x.target.checked })} />必填</label>
        <label className="flex items-center gap-1 text-sm"><input type="checkbox" checked={e.active} onChange={(x) => setE({ ...e, active: x.target.checked })} />启用</label>
        <div className="flex gap-1"><button className="btn-primary btn-sm" onClick={() => save()}>保存</button><button className="btn-ghost btn-sm" onClick={() => setE(null)}>取消</button></div>
      </div>
    </td></tr>
  )
  return (
    <tr className={f.active ? '' : 'text-slate-400'}>
      <td className="td">{f.label}{f.core && <Badge color="purple">核心</Badge>}</td>
      <td className="td">{FTYPE_LABEL[f.ftype]}</td>
      <td className="td text-xs">{f.options.join('、')}</td>
      <td className="td text-xs">{f.required ? '必填' : ''} {!f.active && '已停用'}</td>
      <td className="td whitespace-nowrap"><button className="btn-ghost btn-sm" onClick={() => setE({ ...f })}>编辑</button><button className="btn-ghost btn-sm" onClick={remove}><Trash2 size={12} /></button><span className="ml-1 font-mono text-[10px] text-slate-300">{f.id}</span></td>
    </tr>
  )
}

/** Phone layout for one field: tap to edit in a sheet (same rules as the desktop row). */
function FieldCard({ f, onChanged }: { f: any; onChanged: () => void }) {
  const [open, setOpen] = useState(false)
  return (
    <div className={`p-3 text-sm ${f.active ? '' : 'text-slate-400'}`}>
      <div className="flex items-center gap-2"><span className="font-medium">{f.label}</span><span className="text-xs text-slate-500">{FTYPE_LABEL[f.ftype]}</span>{f.required && <Badge>必填</Badge>}{!f.active && <Badge>已停用</Badge>}
        <button className="btn-ghost btn-sm ml-auto" onClick={() => setOpen(true)}>编辑</button></div>
      {f.options.length > 0 && <div className="text-xs text-slate-400">{f.options.join('、')}</div>}
      {open && <Modal open onClose={() => setOpen(false)} title={`字段：${f.label}`}><table className="w-full"><tbody><FieldRow f={f} onChanged={() => { setOpen(false); onChanged() }} startEditing /></tbody></table></Modal>}
    </div>
  )
}

function TypeEditor({ rt, onChanged }: { rt: any; onChanged: () => void }) {
  const toast = useToast()
  const [meta, setMeta] = useState<any>(null)
  const [nf, setNf] = useState<any>(null)
  const saveMeta = async () => {
    try {
      const rules: any = {}
      for (const [s, v] of Object.entries<any>(meta.rules)) if (v.purposes.trim()) rules[s] = { purposes: v.purposes.split(/[,，]/).map((x: string) => x.trim()).filter(Boolean), need_approved: v.need_approved }
      await patch(`/api/record-types/${rt.id}`, { name: meta.name, description: meta.description, statuses: meta.statuses.split(/[,，]/).map((s: string) => s.trim()).filter(Boolean), evidence_rules: rules, archived: meta.archived })
      setMeta(null); onChanged()
    } catch (e) { toast(errMsg(e), 'err') }
  }
  const addField = async (allow = false) => {
    try { await post(`/api/record-types/${rt.id}/fields`, { ...nf, allow_existing_empty: allow }); setNf(null); onChanged() } catch (e: any) {
      if (e instanceof ApiError && e.detail?.need) { if (confirm(`${e.detail.msg}\n\n确定 = 旧记录保留空值，后续变更状态前补齐`)) addField(true) } else toast(errMsg(e), 'err')
    }
  }
  const openMeta = () => setMeta({ name: rt.name, description: rt.description, statuses: rt.statuses.join(','), archived: rt.archived, rules: Object.fromEntries(rt.statuses.map((s: string) => [s, { purposes: (rt.evidence_rules[s]?.purposes || []).join(','), need_approved: !!rt.evidence_rules[s]?.need_approved }])) })
  return (
    <div className="card p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="font-semibold">{rt.name}</h2>{rt.archived && <Badge>已归档</Badge>}
        <span className="text-xs text-slate-400">{rt.record_count} 条记录 · ID {rt.id}</span>
        <button className="btn-secondary btn-sm sm:ml-auto" onClick={openMeta}>状态与证据规则</button>
        <button className="btn-secondary btn-sm" onClick={() => setNf({ label: '', ftype: 'text', options: [], required: false })}><Plus size={12} />字段</button>
      </div>
      <div className="mt-1 text-xs text-slate-500">状态：{rt.statuses.join(' → ')}</div>
      {Object.keys(rt.evidence_rules).length > 0 && <div className="text-xs text-slate-500">证据规则：{Object.entries<any>(rt.evidence_rules).map(([s, v]) => `${s} 需 ${v.purposes.join('、')}${v.need_approved ? '（已核验）' : ''}`).join('；')}</div>}
      <div className="mt-3 divide-y rounded-lg border sm:hidden">
        {rt.fields.map((f: any) => <FieldCard key={f.id} f={f} onChanged={onChanged} />)}
      </div>
      <div className="-mx-4 mt-3 hidden overflow-x-auto sm:block">
        <table className="min-w-full"><thead className="bg-slate-50"><tr><th className="th">字段</th><th className="th">类型</th><th className="th">选项</th><th className="th">规则</th><th className="th"></th></tr></thead>
          <tbody className="divide-y">{rt.fields.map((f: any) => <FieldRow key={f.id} f={f} onChanged={onChanged} />)}</tbody></table>
      </div>
      <Modal open={!!nf} onClose={() => setNf(null)} title="新增字段" footer={<button className="btn-primary" disabled={!nf?.label} onClick={() => addField()}>添加</button>}>
        {nf && <div className="space-y-3">
          <Field label="名称"><input className="input" value={nf.label} onChange={(e) => setNf({ ...nf, label: e.target.value })} /></Field>
          <Field label="类型"><select className="input" value={nf.ftype} onChange={(e) => setNf({ ...nf, ftype: e.target.value })}>{Object.entries(FTYPE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
          {nf.ftype === 'select' && <Field label="选项（逗号分隔）"><input className="input" onChange={(e) => setNf({ ...nf, options: e.target.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean) })} /></Field>}
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={nf.required} onChange={(e) => setNf({ ...nf, required: e.target.checked })} />必填（新字段默认可空）</label>
        </div>}
      </Modal>
      <Modal open={!!meta} onClose={() => setMeta(null)} title={`${rt.name}：状态与证据规则`} footer={<button className="btn-primary" onClick={saveMeta}>保存</button>}>
        {meta && <div className="space-y-3">
          <Field label="名称"><input className="input" value={meta.name} onChange={(e) => setMeta({ ...meta, name: e.target.value })} /></Field>
          <Field label="状态（按顺序，逗号分隔）" hint="仍有记录使用的状态不能删除"><input className="input" value={meta.statuses} onChange={(e) => setMeta({ ...meta, statuses: e.target.value })} /></Field>
          <div className="text-sm font-semibold">进入某状态需要的证据用途</div>
          {meta.statuses.split(/[,，]/).map((s: string) => s.trim()).filter(Boolean).map((s: string) => {
            const r = meta.rules[s] || { purposes: '', need_approved: false }
            return (
              <div key={s} className="grid grid-cols-[1fr_auto] items-center gap-2 sm:grid-cols-[100px_1fr_auto] [&>span]:col-span-2 sm:[&>span]:col-span-1">
                <span className="text-sm">{s}</span>
                <input className="input" placeholder="如 验收表,安装后照片" value={r.purposes} onChange={(e) => setMeta({ ...meta, rules: { ...meta.rules, [s]: { ...r, purposes: e.target.value } } })} />
                <label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={r.need_approved} onChange={(e) => setMeta({ ...meta, rules: { ...meta.rules, [s]: { ...r, need_approved: e.target.checked } } })} />需核验通过</label>
              </div>
            )
          })}
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={meta.archived} onChange={(e) => setMeta({ ...meta, archived: e.target.checked })} />归档（不能再新建记录）</label>
        </div>}
      </Modal>
    </div>
  )
}

export function Schema() {
  const toast = useToast()
  const { data, reload } = useLoad(() => get('/api/record-types'), [])
  const create = async () => {
    const name = prompt('记录类型名称（如：车辆与证件）')
    if (!name) return
    try { await post('/api/record-types', { name }); reload() } catch (e) { toast(errMsg(e), 'err') }
  }
  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <div className="flex items-center"><h1 className="text-xl font-bold">记录类型与字段</h1><button className="btn-primary ml-auto" onClick={create}><Plus size={16} />新建记录类型</button></div>
      <p className="text-sm text-slate-500">字段改名保留 ID；有历史值的字段不能删除或改类型，只能停用。库存数量、批次放行、付款等核心逻辑不在这里配置。</p>
      {(data || []).map((rt: any) => <TypeEditor key={rt.id} rt={rt} onChanged={reload} />)}
    </div>
  )
}

// ---------- users ----------
function TeamMode() {
  const toast = useToast()
  const { refresh } = useAuth()
  const { data, reload } = useLoad(() => get('/api/settings/org'), [])
  if (!data) return null
  const set = async (mode: string) => {
    if (mode === data.mode) return
    try { await put('/api/settings/org', { mode }); reload(); refresh() } catch (e) { toast(errMsg(e), 'err') }
  }
  return (
    <div className="card mb-4 p-4">
      <div className="mb-2 font-semibold">团队模式</div>
      <div className="grid gap-2 sm:grid-cols-2">
        {[['simple', '精简（适合 2–5 人）', '老板、主管拥有全部权限；可以本人核验、本人批准、本人入账，系统标注「本人」并留记录。现场员工只做当班检查、收货提交、上报异常、拍照。'],
          ['standard', '标准（人多以后）', '按岗位分权（采购、仓管、质量、财务…），核验、放行、入账等关键步骤要求分人复核。']].map(([k, title, desc]) => (
          <button key={k} disabled={!data.can_edit} onClick={() => set(k)} className={`rounded-lg border-2 p-3 text-left ${data.mode === k ? 'border-brand-500 bg-brand-50' : 'border-slate-200'}`}>
            <div className="text-sm font-semibold">{title}{data.mode === k && ' ✓'}</div><div className="mt-1 text-xs text-slate-600">{desc}</div>
          </button>
        ))}
      </div>
      {!data.can_edit && <p className="mt-2 text-xs text-slate-400">只有老板或系统管理员可以切换。</p>}
    </div>
  )
}

export function Users() {
  const toast = useToast()
  const { data, reload } = useLoad(() => get('/api/users'), [])
  const roles = useLoad(() => get('/api/roles'), [])
  const [f, setF] = useState<any>(null)
  const save = async () => {
    try {
      if (f.id) {
        const leaving = !f.active && data?.find((u: any) => u.id === f.id)?.active
        if (leaving && !confirm('停用后立即失去访问权限，并为其可见的共享密码生成轮换任务。继续？')) return
        await patch(`/api/users/${f.id}`, f)
      } else await post('/api/users', f)
      setF(null); reload()
    } catch (e) { toast(errMsg(e), 'err') }
  }
  return (
    <div className="mx-auto max-w-4xl">
      <TeamMode />
      <div className="mb-3 flex items-center"><h1 className="text-xl font-bold">用户与角色</h1><button className="btn-primary ml-auto" onClick={() => setF({ username: '', display_name: '', role: 'staff', password: '', lang: 'zh', tz: 'America/Los_Angeles', active: true })}><Plus size={16} />新用户</button></div>
      <div className="card divide-y">
        {(data || []).map((u: any) => (
          <div key={u.id} className={`flex flex-wrap items-center gap-2 p-3 text-sm ${u.active ? '' : 'text-slate-400'}`}>
            <span className="font-medium">{u.name}</span><span className="text-xs text-slate-400">{u.username}</span><Badge>{u.role_label}</Badge>{!u.active && <Badge color="red">已停用</Badge>}
            <span className="text-xs text-slate-400">{u.lang} · {u.tz}</span>
            <button className="btn-ghost btn-sm ml-auto" onClick={() => setF({ id: u.id, username: u.username, display_name: u.name, role: u.role, lang: u.lang, tz: u.tz, active: u.active, password: '' })}>编辑</button>
          </div>
        ))}
      </div>
      <RepairInventory />
      <ResetDemo />
      <Modal open={!!f} onClose={() => setF(null)} title={f?.id ? '编辑用户' : '新用户'} footer={<button className="btn-primary" onClick={save}>保存</button>}>
        {f && <div className="grid gap-3 sm:grid-cols-2">
          <Field label="用户名"><input className="input" disabled={!!f.id} value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} /></Field>
          <Field label="显示名"><input className="input" value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} /></Field>
          <Field label="角色"><select className="input" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>
            {(roles.data || []).filter((r: any) => r.common).map((r: any) => <option key={r.key} value={r.key}>{r.label}</option>)}
            {(roles.data || []).some((r: any) => !r.common) && <optgroup label="按岗位细分（标准模式用）">{(roles.data || []).filter((r: any) => !r.common).map((r: any) => <option key={r.key} value={r.key}>{r.label}</option>)}</optgroup>}
          </select></Field>
          <Field label={f.id ? '重置密码（留空不改）' : '初始密码（≥8 位）'}><input className="input" type="password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></Field>
          <Field label="语言"><select className="input" value={f.lang} onChange={(e) => setF({ ...f, lang: e.target.value })}><option value="zh">中文</option><option value="en">English</option><option value="es">Español</option></select></Field>
          <Field label="时区"><input className="input" value={f.tz} onChange={(e) => setF({ ...f, tz: e.target.value })} /></Field>
          {f.id && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} />启用</label>}
        </div>}
      </Modal>
    </div>
  )
}

// ---------- audit ----------
export function Audit() {
  const [q, setQ] = useState('')
  const { data } = useLoad(() => get(`/api/audit${q ? `?target_id=${encodeURIComponent(q)}` : ''}`), [q])
  return (
    <div className="mx-auto max-w-6xl">
      <div className="mb-3 flex flex-wrap items-center gap-2"><h1 className="text-xl font-bold">审计日志</h1><input className="input sm:ml-auto sm:max-w-xs" placeholder="按对象 ID 过滤" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      <p className="mb-2 text-xs text-slate-500">只追加、不可修改。密码查看/复制只记录事件，不记录值。</p>
      <div className="space-y-2 sm:hidden">
        {(data || []).map((a: any) => (
          <div key={a.id} className="card p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2"><span className="font-mono text-xs font-semibold">{a.action}</span><span className="text-xs text-slate-500">{a.user || '—'}</span><span className="ml-auto text-xs text-slate-400">{dt(a.at)}</span></div>
            <div className="mt-1 break-all font-mono text-[11px] text-slate-500">{a.target_type} {a.target_id}</div>
            {Object.keys(a.detail || {}).length > 0 && <div className="mt-1 break-all font-mono text-[11px] text-slate-400">{JSON.stringify(a.detail)}</div>}
          </div>
        ))}
      </div>
      <div className="card -mx-3 hidden overflow-x-auto sm:mx-0 sm:block">
        <table className="min-w-full"><thead className="bg-slate-50"><tr><th className="th">时间</th><th className="th">人员</th><th className="th">动作</th><th className="th">对象</th><th className="th">详情</th></tr></thead>
          <tbody className="divide-y">{(data || []).map((a: any) => (
            <tr key={a.id}><td className="td whitespace-nowrap text-xs">{dt(a.at, { site: true })}</td><td className="td text-xs">{a.user || '—'}</td><td className="td font-mono text-xs">{a.action}</td><td className="td font-mono text-xs">{a.target_type} {a.target_id}</td><td className="td max-w-md truncate font-mono text-[11px] text-slate-500" title={JSON.stringify(a.detail)}>{JSON.stringify(a.detail)}</td></tr>
          ))}</tbody></table>
      </div>
    </div>
  )
}

// ---------- me ----------
export function Me() {
  const { me, refresh } = useAuth()
  const toast = useToast()
  const [lang, setL] = useState(me?.lang || 'zh')
  const [tz, setTz] = useState(me?.tz || 'America/Los_Angeles')
  const [pin, setPin] = useState('')
  const [pw, setPw] = useState('')
  const save = async () => {
    try {
      await patch('/api/me', { lang, tz, ...(pin ? { pin } : {}), ...(pw ? { password: pw } : {}) })
      setLang(lang); await refresh(); setPin(''); setPw(''); toast('已保存')
    } catch (e) { toast(errMsg(e), 'err') }
  }
  return (
    <div className="mx-auto max-w-xl">
      <h1 className="mb-3 text-xl font-bold">我的设置</h1>
      <div className="card space-y-3 p-4">
        <div className="text-sm">{me?.name} · {me?.role_label}</div>
        <Field label="界面语言 / Language / Idioma"><select className="input" value={lang} onChange={(e) => setL(e.target.value)}><option value="zh">中文</option><option value="en">English</option><option value="es">Español</option></select></Field>
        <Field label="时区" hint="时间按 UTC 存储，按你的时区显示；与场地时区不同时同时显示场地时间">
          <select className="input" value={tz} onChange={(e) => setTz(e.target.value)}>{['America/Los_Angeles', 'Asia/Shanghai', 'America/New_York', 'UTC'].map((z) => <option key={z}>{z}</option>)}</select>
        </Field>
        <Field label={me?.has_pin ? '修改 PIN（共享设备快速切换）' : '设置 PIN（4-6 位，共享设备快速切换）'}><input className="input" inputMode="numeric" type="password" value={pin} onChange={(e) => setPin(e.target.value)} /></Field>
        <Field label="修改密码（≥8 位）"><input className="input" type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} /></Field>
        <button className="btn-primary w-full" onClick={save}>保存</button>
      </div>
      {(me?.role !== 'sys_admin' || me?.perms.includes('task.manage')) && <AgentSettings />}
    </div>
  )
}
