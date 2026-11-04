import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Copy, Eye, EyeOff, KeyRound, Mail, Merge, Phone, Pin, Plus } from 'lucide-react'
import { ApiError, get, patch, post } from '../lib/api'
import { useAuth } from '../lib/auth'
import { dt } from '../lib/fmt'
import { Badge, Empty, Field, Modal, Tabs, errMsg, useLoad, useToast } from '../components/ui'
import { EvidencePanel } from '../components/Evidence'
import { useUsers } from '../components/FieldInput'

const ACC_CATS = ['邮箱', '供应商采购', '租车', '政府注册', '水电网', '财务软件', '设备管理', '其他']
const CT_CATS = ['TT客服', '外库物流', '肉/香料供应商', '租车', '维修电工', '垃圾/pest control', '水电网', '检查协调', '人事财务', '其他']

// ---------- accounts ----------
function Secret({ acc }: { acc: any }) {
  const toast = useToast()
  const [val, setVal] = useState<string | null>(null)
  const timer = useRef<any>(null)
  useEffect(() => () => clearTimeout(timer.current), [])
  if (!acc.has_password) return <span className="text-xs text-slate-400">未保存密码</span>
  if (!acc.can_read_password) return <span className="text-xs text-slate-400">无权查看密码</span>
  const reveal = async () => {
    if (val) { setVal(null); return }
    try {
      const r = await post(`/api/nb/accounts/${acc.id}/reveal?purpose=view`)
      setVal(r.password)
      timer.current = setTimeout(() => setVal(null), 30000)
    } catch (e) { toast(errMsg(e), 'err') }
  }
  const copy = async () => {
    try {
      const r = await post(`/api/nb/accounts/${acc.id}/reveal?purpose=copy`)
      await navigator.clipboard.writeText(r.password)
      toast('已复制。剪贴板由你的设备管理，用完请自行清除')
    } catch (e) { toast(errMsg(e), 'err') }
  }
  return (
    <span className="inline-flex items-center gap-1">
      <code className="rounded bg-slate-100 px-2 py-1 text-sm">{val ?? '••••••••'}</code>
      <button className="btn-ghost btn-sm" onClick={reveal} title={val ? '隐藏' : '显示（30 秒后自动隐藏）'}>{val ? <EyeOff size={14} /> : <Eye size={14} />}</button>
      <button className="btn-ghost btn-sm" onClick={copy} title="复制密码"><Copy size={14} /></button>
    </span>
  )
}

function AccountForm({ acc, onClose, onSaved }: any) {
  const toast = useToast()
  const users = useUsers()
  const { can } = useAuth()
  const roles = useLoad(() => get('/api/roles'), [])
  const [f, setF] = useState<any>(acc ? { ...acc, password: undefined } : { service: '', category: '其他', url: '', username: '', account_no: '', purpose: '', mfa_info: '', notes: '', status: 'active', share_roles: ['boss', 'ops_manager'], share_users: [] })
  const [pw, setPw] = useState('')
  const save = async () => {
    const body: any = { ...f }
    ;['id', 'owner', 'backup_owner', 'has_password', 'can_read_password', 'can_edit', 'share_user_names', 'updated_at'].forEach((k) => delete body[k])
    if (pw) body.password = pw
    else delete body.password
    try { acc ? await patch(`/api/nb/accounts/${acc.id}`, body) : await post('/api/nb/accounts', body); onSaved(); onClose() } catch (e) { toast(errMsg(e), 'err') }
  }
  const manageShare = !acc || can('notebook.manage') || acc.can_edit
  return (
    <Modal open onClose={onClose} wide title={acc ? `编辑账号：${acc.service}` : '新增账号'} footer={<button className="btn-primary" disabled={!f.service} onClick={save}>保存</button>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="服务名称 *"><input className="input" value={f.service} onChange={(e) => setF({ ...f, service: e.target.value })} /></Field>
        <Field label="分类"><select className="input" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>{ACC_CATS.map((c) => <option key={c}>{c}</option>)}</select></Field>
        <Field label="登录网址"><input className="input" value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} /></Field>
        <Field label="用户名 / 登录邮箱"><input className="input" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} /></Field>
        <Field label="客户/账户编号"><input className="input" value={f.account_no} onChange={(e) => setF({ ...f, account_no: e.target.value })} /></Field>
        <Field label={acc?.has_password ? '新密码（留空不修改）' : '密码'} hint="加密存储；不进入搜索、导出、通知或 AI 处理"><input className="input" type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} /></Field>
        <Field label="负责人"><select className="input" value={f.owner_id || ''} onChange={(e) => setF({ ...f, owner_id: e.target.value || null })}><option value="">—</option>{users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</select></Field>
        <Field label="备份负责人"><select className="input" value={f.backup_owner_id || ''} onChange={(e) => setF({ ...f, backup_owner_id: e.target.value || null })}><option value="">—</option>{users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</select></Field>
        <Field label="两步验证（谁的手机/邮箱收验证码）"><input className="input" value={f.mfa_info} onChange={(e) => setF({ ...f, mfa_info: e.target.value })} /></Field>
        <Field label="续费日期"><input className="input" type="date" value={f.renewal_date || ''} onChange={(e) => setF({ ...f, renewal_date: e.target.value || null })} /></Field>
        <Field label="用途"><input className="input" value={f.purpose} onChange={(e) => setF({ ...f, purpose: e.target.value })} /></Field>
        <Field label="最后核验日期"><input className="input" type="date" value={f.last_verified_at || ''} onChange={(e) => setF({ ...f, last_verified_at: e.target.value || null })} /></Field>
        <div className="sm:col-span-2"><Field label="备注（不要把密码写在这里）"><textarea className="input h-16" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field></div>
        {manageShare && (
          <div className="sm:col-span-2">
            <Field label="谁可以查看密码（角色）">
              <div className="flex flex-wrap gap-2">{(roles.data || []).map((r: any) => <label key={r.key} className="flex items-center gap-1 text-sm"><input type="checkbox" checked={f.share_roles.includes(r.key)} onChange={(e) => setF({ ...f, share_roles: e.target.checked ? [...f.share_roles, r.key] : f.share_roles.filter((x: string) => x !== r.key) })} />{r.label}</label>)}</div>
            </Field>
            <div className="mt-2"><Field label="另外指定人员">
              <div className="flex flex-wrap gap-2">{users.map((u) => <label key={u.id} className="flex items-center gap-1 text-sm"><input type="checkbox" checked={f.share_users.includes(u.id)} onChange={(e) => setF({ ...f, share_users: e.target.checked ? [...f.share_users, u.id] : f.share_users.filter((x: string) => x !== u.id) })} />{u.name}</label>)}</div>
            </Field></div>
          </div>
        )}
      </div>
    </Modal>
  )
}

function Accounts() {
  const { can } = useAuth()
  const [q, setQ] = useState('')
  const { data, reload } = useLoad(() => get(`/api/nb/accounts?q=${encodeURIComponent(q)}`), [q])
  const [edit, setEdit] = useState<any>(undefined)
  const groups: Record<string, any[]> = {}
  for (const a of data || []) (groups[a.pinned ? '置顶' : a.category] ||= []).push(a)
  return (
    <div>
      <div className="mb-3 flex gap-2">
        <input className="input sm:max-w-xs" type="search" placeholder="搜索账号（不搜索密码）" value={q} onChange={(e) => setQ(e.target.value)} />
        {can('task.manage') && <button className="btn-primary shrink-0 sm:ml-auto" onClick={() => setEdit(null)} aria-label="新增账号"><Plus size={16} /><span className="hidden sm:inline">新增账号</span></button>}
      </div>
      {Object.entries(groups).map(([g, accs]) => (
        <div key={g} className="mb-4">
          <div className="mb-1 text-xs font-semibold text-slate-500">{g}</div>
          <div className="grid gap-2 md:grid-cols-2">
            {accs.map((a) => (
              <div key={a.id} className="rounded-lg border p-3 text-sm">
                <div className="flex items-center gap-2">
                  <KeyRound size={16} className="text-slate-400" /><span className="font-semibold">{a.service}</span>
                  {a.status !== 'active' && <Badge>{a.status}</Badge>}
                  {a.can_edit && <button className="btn-ghost btn-sm ml-auto" onClick={() => setEdit(a)}>编辑</button>}
                </div>
                {a.url && <a href={a.url} target="_blank" rel="noreferrer" className="block truncate text-xs text-brand-700 underline">{a.url}</a>}
                <div className="mt-2 grid grid-cols-[72px_1fr] gap-y-1 text-xs sm:grid-cols-[80px_1fr]">
                  <span className="text-slate-400">用户名</span><span className="flex items-center gap-1">{a.username || '—'}{a.username && <button className="btn-ghost btn-sm -my-2 text-slate-400" aria-label="复制用户名" onClick={() => navigator.clipboard.writeText(a.username)}><Copy size={14} /></button>}</span>
                  <span className="text-slate-400">密码</span><Secret acc={a} />
                  {a.mfa_info && <><span className="text-slate-400">两步验证</span><span>{a.mfa_info}</span></>}
                  <span className="text-slate-400">负责人</span><span>{a.owner || '—'}{a.backup_owner && ` / 备份 ${a.backup_owner}`}</span>
                  {a.renewal_date && <><span className="text-slate-400">续费</span><span>{a.renewal_date}</span></>}
                  <span className="text-slate-400">可见</span><span className="text-slate-500">{[...a.share_roles, ...a.share_user_names].join('、') || '仅创建人'}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
      {data && !data.length && <Empty />}
      {edit !== undefined && <AccountForm acc={edit} onClose={() => setEdit(undefined)} onSaved={reload} />}
    </div>
  )
}

// ---------- contacts ----------
function ContactForm({ ct, onClose, onSaved }: any) {
  const toast = useToast()
  const users = useUsers()
  const [f, setF] = useState<any>(ct || { name: '', company: '', title: '', category: '其他', phone: '', landline: '', email: '', wechat: '', language: '', hours: '', emergency: false, notes: '' })
  const [dupes, setDupes] = useState<any[] | null>(null)
  const save = async (force = false) => {
    const body = { ...f, force }
    delete body.id; delete body.internal_owner; delete body.updated_at
    try { ct ? await patch(`/api/nb/contacts/${ct.id}`, body) : await post('/api/nb/contacts', body); onSaved(); onClose() } catch (e: any) {
      if (e instanceof ApiError && e.status === 409 && e.detail?.candidates) setDupes(e.detail.candidates)
      else toast(errMsg(e), 'err')
    }
  }
  const inp = (k: string, label: string, type = 'text') => <Field label={label}><input className="input" type={type} value={f[k] || ''} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></Field>
  return (
    <Modal open onClose={onClose} wide title={ct ? `编辑：${ct.name}` : '新增联系人'} footer={<button className="btn-primary" disabled={!f.name} onClick={() => save()}>保存</button>}>
      {dupes && (
        <div className="mb-3 rounded bg-amber-50 p-3 text-sm">疑似重复：{dupes.map((d) => `${d.name}${d.company ? `（${d.company}）` : ''} ${d.phone}`).join('；')}
          <button className="btn-secondary btn-sm ml-2" onClick={() => save(true)}>仍然新建</button></div>
      )}
      <div className="grid gap-3 sm:grid-cols-3">
        {inp('name', '姓名 *')}{inp('company', '公司')}{inp('title', '职位/职责')}
        <Field label="分类"><select className="input" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>{CT_CATS.map((c) => <option key={c}>{c}</option>)}</select></Field>
        {inp('phone', '手机', 'tel')}{inp('landline', '座机及分机', 'tel')}{inp('email', '邮箱', 'email')}{inp('wechat', '微信/其他')}{inp('language', '语言')}{inp('hours', '时区/工作时间')}
        <Field label="内部对接人"><select className="input" value={f.internal_owner_id || ''} onChange={(e) => setF({ ...f, internal_owner_id: e.target.value || null })}><option value="">—</option>{users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</select></Field>
        <Field label="最后核验"><input className="input" type="date" value={f.last_verified_at || ''} onChange={(e) => setF({ ...f, last_verified_at: e.target.value || null })} /></Field>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.emergency} onChange={(e) => setF({ ...f, emergency: e.target.checked })} />紧急联系人</label>
        <div className="sm:col-span-3"><Field label="备注"><textarea className="input h-16" value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field></div>
      </div>
      {ct && <div className="mt-4"><EvidencePanel title="名片 / 附件" targetType="contact" targetId={ct.id} purposes={['名片', '附件']} compact /></div>}
    </Modal>
  )
}

function Contacts() {
  const toast = useToast()
  const [q, setQ] = useState('')
  const { data, reload } = useLoad(() => get(`/api/nb/contacts?q=${encodeURIComponent(q)}`), [q])
  const [edit, setEdit] = useState<any>(undefined)
  const [merge, setMerge] = useState<string[]>([])
  const doMerge = async () => {
    const [keep, other] = merge
    try {
      const pv = await post('/api/nb/contacts/merge', { keep_id: keep, merge_id: other, preview: true })
      if (!confirm(`合并预览：补齐字段 ${Object.keys(pv.fill).join('、') || '无'}；转移证据 ${pv.evidence_links_moved} 份。确认合并？`)) return
      await post('/api/nb/contacts/merge', { keep_id: keep, merge_id: other, preview: false })
      setMerge([]); reload(); toast('已合并')
    } catch (e) { toast(errMsg(e), 'err') }
  }
  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-2">
        <input className="input flex-1 sm:max-w-xs" type="search" placeholder="搜索姓名、公司、电话" value={q} onChange={(e) => setQ(e.target.value)} />
        {merge.length === 2 && <button className="btn-secondary" onClick={doMerge}><Merge size={16} />合并所选（保留第一个）</button>}
        <button className="btn-primary shrink-0 sm:ml-auto" onClick={() => setEdit(null)} aria-label="新增联系人"><Plus size={16} /><span className="hidden sm:inline">新增联系人</span></button>
      </div>
      <div className="grid gap-2 md:grid-cols-2">
        {(data || []).map((c: any) => (
          <div key={c.id} className="rounded-lg border p-3 text-sm">
            <div className="flex items-center gap-2">
              <input type="checkbox" checked={merge.includes(c.id)} onChange={(e) => setMerge(e.target.checked ? [...merge, c.id].slice(-2) : merge.filter((x) => x !== c.id))} title="选择以合并" />
              <span className="font-semibold">{c.name}</span>{c.emergency && <Badge color="red">紧急</Badge>}<Badge>{c.category}</Badge>
              <button className="btn-ghost btn-sm ml-auto" onClick={() => setEdit(c)}>编辑</button>
            </div>
            <div className="text-xs text-slate-500">{[c.company, c.title].filter(Boolean).join(' · ')}</div>
            <div className="mt-2 flex flex-wrap gap-2">
              {c.phone && <a className="btn-primary btn-sm" href={`tel:${c.phone}`}><Phone size={12} />{c.phone}</a>}
              {c.email && <a className="btn-secondary btn-sm" href={`mailto:${c.email}`}><Mail size={12} />写邮件</a>}
              {c.wechat && <span className="chip bg-slate-100">微信 {c.wechat}</span>}
            </div>
            {c.notes && <div className="mt-1 text-xs text-slate-500">{c.notes}</div>}
            <div className="mt-1 text-[11px] text-slate-400">对接人 {c.internal_owner || '—'} · 核验 {c.last_verified_at || '未核验'}</div>
          </div>
        ))}
      </div>
      {data && !data.length && <Empty />}
      {edit !== undefined && <ContactForm ct={edit} onClose={() => setEdit(undefined)} onSaved={reload} />}
    </div>
  )
}

// ---------- notes ----------
function Notes() {
  const toast = useToast()
  const [archived, setArchived] = useState(false)
  const { data, reload } = useLoad(() => get(`/api/nb/notes?archived=${archived}`), [archived])
  const [cur, setCur] = useState<any>(null)
  const area = useRef<HTMLTextAreaElement>(null)
  const save = async () => {
    try {
      const r = cur.id ? await patch(`/api/nb/notes/${cur.id}`, cur) : await post('/api/nb/notes', cur)
      setCur(r); reload(); toast('已保存')
    } catch (e) { toast(errMsg(e), 'err') }
  }
  const toTask = async () => {
    const el = area.current
    const sel = el ? el.value.slice(el.selectionStart, el.selectionEnd).trim() : ''
    const text = sel || prompt('任务内容')
    if (!text) return
    try { await post(`/api/nb/notes/${cur.id}/to-task`, { text }); toast('已转为任务') } catch (e) { toast(errMsg(e), 'err') }
  }
  const pwLike = cur && /(密码|password|pwd)\s*[:：=]/i.test(cur.body)
  return (
    <div className="grid gap-4 md:grid-cols-[280px_1fr]">
      <div className={cur ? 'hidden md:block' : ''}>
        <div className="mb-2 flex gap-2">
          <button className="btn-primary btn-sm" onClick={() => setCur({ title: '', category: '通用', body: '', pinned: false })}><Plus size={14} />新笔记</button>
          <label className="ml-auto flex items-center gap-1 text-xs"><input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} />已归档</label>
        </div>
        <div className="space-y-1">
          {(data || []).map((n: any) => (
            <button key={n.id} onClick={() => setCur(n)} className={`w-full rounded p-2 text-left text-sm ${cur?.id === n.id ? 'bg-brand-50' : 'hover:bg-slate-50'}`}>
              <div className="flex items-center gap-1 font-medium">{n.pinned && <Pin size={12} />}{n.title}</div>
              <div className="text-xs text-slate-400">{n.category} · {dt(n.updated_at)}</div>
            </button>
          ))}
        </div>
      </div>
      {cur ? (
        <div className="space-y-2">
          <button className="btn-ghost -ml-2 md:hidden" onClick={() => setCur(null)}>← 返回笔记列表</button>
          <div className="flex gap-2">
            <input className="input" placeholder="标题" value={cur.title} onChange={(e) => setCur({ ...cur, title: e.target.value })} />
            <input className="input w-32" placeholder="分类" value={cur.category} onChange={(e) => setCur({ ...cur, category: e.target.value })} />
          </div>
          <textarea ref={area} className="input h-[45dvh] font-mono sm:h-72" value={cur.body} onChange={(e) => setCur({ ...cur, body: e.target.value })} placeholder="支持 - 列表、链接。选中一段文字后点「转为任务」。" />
          {pwLike && <div className="rounded bg-amber-50 p-2 text-xs text-amber-800">看起来笔记里写了密码。请改用「账号与密码」保存凭据——普通笔记不是保险箱。</div>}
          <div className="flex flex-wrap gap-2">
            <button className="btn-primary" disabled={!cur.title} onClick={save}>保存</button>
            <label className="flex items-center gap-1 text-sm"><input type="checkbox" checked={cur.pinned} onChange={(e) => setCur({ ...cur, pinned: e.target.checked })} />置顶</label>
            {cur.id && <button className="btn-secondary" onClick={toTask}>转为任务</button>}
            {cur.id && <button className="btn-ghost" onClick={async () => { await patch(`/api/nb/notes/${cur.id}`, { ...cur, archived: !cur.archived }); setCur(null); reload() }}>{cur.archived ? '取消归档' : '归档'}</button>}
          </div>
          {cur.id && <EvidencePanel targetType="note" targetId={cur.id} compact />}
        </div>
      ) : <div className="hidden md:block"><Empty>选择或新建笔记</Empty></div>}
    </div>
  )
}

export function Notebook() {
  const [sp, setSp] = useSearchParams()
  const tab = sp.get('tab') || 'accounts'
  return (
    <div className="mx-auto max-w-6xl">
      <h1 className="mb-3 text-xl font-bold">公司 Notebook</h1>
      <div className="card p-4">
        <Tabs value={tab} onChange={(k) => setSp({ tab: k })} tabs={[{ key: 'accounts', label: '账号与密码' }, { key: 'contacts', label: '重要联系人' }, { key: 'notes', label: '公司笔记' }]} />
        <div className="pt-4">{tab === 'accounts' ? <Accounts /> : tab === 'contacts' ? <Contacts /> : <Notes />}</div>
      </div>
    </div>
  )
}
