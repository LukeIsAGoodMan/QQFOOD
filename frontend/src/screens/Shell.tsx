import { useEffect, useState, type ReactNode } from 'react'
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom'
import {
  Bell, BookUser, Boxes, ClipboardCheck, Database, Factory, Gauge, Hammer, Home, Inbox, Kanban, LayoutDashboard, ListChecks,
  LogOut, Menu, ScrollText, Search, Settings, ShieldCheck, ShoppingCart, Table, Thermometer, Truck, Users as UsersIcon, Wrench, X, Car, UserPlus, FileText,
} from 'lucide-react'
import { get, post } from '../lib/api'
import { useAuth } from '../lib/auth'
import { ago } from '../lib/fmt'
import { t } from '../lib/i18n'
import { useLoad } from '../components/ui'

export const PAGE_ICONS: Record<string, any> = {
  layout: LayoutDashboard, gauge: Gauge, 'list-checks': ListChecks, 'shopping-cart': ShoppingCart, hammer: Hammer, truck: Truck,
  boxes: Boxes, factory: Factory, 'shield-check': ShieldCheck, 'clipboard-check': ClipboardCheck, table: Table, kanban: Kanban,
  thermometer: Thermometer, wrench: Wrench, car: Car, 'user-plus': UserPlus, 'file-text': FileText, users: UsersIcon,
}
export const pageIcon = (name: string, size = 18) => {
  const I = PAGE_ICONS[name] || LayoutDashboard
  return <I size={size} />
}
const Icons = { Database, ScrollText }

let pagesVersion = 0
const listeners = new Set<() => void>()
export const bumpPages = () => { pagesVersion++; listeners.forEach((l) => l()) }
function usePagesVersion() {
  const [v, setV] = useState(pagesVersion)
  useEffect(() => { const l = () => setV(pagesVersion); listeners.add(l); return () => { listeners.delete(l) } }, [])
  return v
}

function Nav({ onNavigate }: { onNavigate?: () => void }) {
  const { can } = useAuth()
  const v = usePagesVersion()
  const { data: pages } = useLoad(() => get('/api/pages'), [v])
  const groups: Record<string, any[]> = {}
  for (const p of pages || []) (groups[p.nav_group] ||= []).push(p)
  const link = (to: string, icon: ReactNode, label: ReactNode, badge?: ReactNode) => (
    <NavLink key={to} to={to} onClick={onNavigate} className={({ isActive }) => `flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm ${isActive ? 'bg-brand-50 font-semibold text-brand-700' : 'text-slate-600 hover:bg-slate-100'}`}>
      {icon}<span className="flex-1 truncate">{label}</span>{badge}
    </NavLink>
  )
  return (
    <nav className="space-y-5 p-3">
      <div className="space-y-0.5">
        {link('/', <Home size={18} />, t('nav.home'))}
        {link('/ingest', <Inbox size={18} />, t('nav.ingest'))}
        {link('/notebook', <BookUser size={18} />, t('nav.notebook'))}
      </div>
      {Object.entries(groups).map(([g, ps]) => (
        <div key={g}>
          <div className="mb-1 px-3 text-[11px] font-semibold uppercase tracking-wide text-slate-400">{g}</div>
          <div className="space-y-0.5">{ps.map((p) => link(`/p/${p.id}`, pageIcon(p.icon), p.name, p.has_unpublished && can('page.edit') ? <span className="h-1.5 w-1.5 rounded-full bg-amber-500" title="有未发布草稿" /> : null))}</div>
        </div>
      ))}
      {(can('page.edit') || can('users.manage') || can('audit.view') || can('schema.edit')) && (
        <div>
          <div className="mb-1 px-3 text-[11px] font-semibold uppercase tracking-wide text-slate-400">{t('nav.settings')}</div>
          <div className="space-y-0.5">
            {can('page.edit') && link('/admin/pages', <Settings size={18} />, t('nav.pagesAdmin'))}
            {can('schema.edit') && link('/admin/schema', <Icons.Database size={18} />, t('nav.schema'))}
            {can('users.manage') && link('/admin/users', <UsersIcon size={18} />, t('nav.users'))}
            {can('audit.view') && link('/admin/audit', <Icons.ScrollText size={18} />, t('nav.audit'))}
          </div>
        </div>
      )}
    </nav>
  )
}

function Notifications() {
  const [open, setOpen] = useState(false)
  const { data, reload } = useLoad(() => get('/api/notifications'), [])
  useEffect(() => { const i = setInterval(reload, 60000); return () => clearInterval(i) }, [reload])
  const unread = (data || []).filter((n: any) => !n.read).length
  return (
    <div className="relative">
      <button className="btn-ghost relative" onClick={() => setOpen(!open)} aria-label="通知">
        <Bell size={18} />{unread > 0 && <span className="absolute right-1 top-1 rounded-full bg-red-500 px-1 text-[10px] text-white">{unread}</span>}
      </button>
      {open && (
        <div className="fixed inset-x-2 top-14 z-40 rounded-xl sm:absolute sm:inset-x-auto sm:right-0 sm:top-11 sm:w-80 bg-white p-2 shadow-xl ring-1 ring-slate-200">
          <div className="flex items-center justify-between px-2 py-1 text-sm font-semibold">通知
            {unread > 0 && <button className="text-xs font-normal text-brand-700" onClick={async () => { await post('/api/notifications/read-all'); reload() }}>全部已读</button>}
          </div>
          <div className="max-h-96 overflow-y-auto">
            {(data || []).map((n: any) => (
              <Link key={n.id} to={n.link || '#'} onClick={() => setOpen(false)} className={`block min-h-[44px] rounded px-2 py-2 text-sm hover:bg-slate-50 ${n.read ? 'text-slate-400' : ''}`}>
                {n.title}<div className="text-xs text-slate-400">{ago(n.created_at)}</div>
              </Link>
            ))}
            {data && !data.length && <div className="p-4 text-center text-xs text-slate-400">暂无通知</div>}
          </div>
        </div>
      )}
    </div>
  )
}

function GlobalSearch({ mobile, onDone }: { mobile?: boolean; onDone?: () => void }) {
  const [q, setQ] = useState('')
  const [res, setRes] = useState<any[] | null>(null)
  const nav = useNavigate()
  useEffect(() => {
    if (q.trim().length < 1) { setRes(null); return }
    const h = setTimeout(() => get(`/api/search?q=${encodeURIComponent(q)}`).then(setRes).catch(() => setRes([])), 250)
    return () => clearTimeout(h)
  }, [q])
  const typeLabel: any = { record: '记录', task: '任务', contact: '联系人', account: '账号', evidence: '证据' }
  return (
    <div className={mobile ? 'relative' : 'relative hidden flex-1 md:block md:max-w-md'}>
      <Search size={16} className="absolute left-3 top-3.5 text-slate-400" />
      <input className="input pl-9" autoFocus={mobile} type="search" enterKeyHint="search" placeholder="搜索记录、任务、联系人、证据…" value={q} onChange={(e) => setQ(e.target.value)} onBlur={() => !mobile && setTimeout(() => setRes(null), 200)} />
      {res && (
        <div className={mobile ? 'mt-2 rounded-xl bg-white p-1 ring-1 ring-slate-200' : 'absolute top-11 z-40 w-full rounded-xl bg-white p-1 shadow-xl ring-1 ring-slate-200'}>
          {res.slice(0, mobile ? 30 : 12).map((r) => (
            <button key={r.type + r.id} onMouseDown={() => { nav(r.link); setQ(''); onDone?.() }} onClick={() => { nav(r.link); setQ(''); onDone?.() }} className="flex min-h-[44px] w-full items-center gap-2 rounded px-3 py-2 text-left text-sm hover:bg-slate-50">
              <span className="chip bg-slate-100 text-slate-500">{typeLabel[r.type]}</span><span className="flex-1 truncate">{r.title}</span><span className="text-xs text-slate-400">{r.sub}</span>
            </button>
          ))}
          {!res.length && <div className="p-3 text-center text-xs text-slate-400">无结果（密码不在搜索范围内）</div>}
        </div>
      )}
    </div>
  )
}

export function Shell({ children }: { children: ReactNode }) {
  const { me, logout } = useAuth()
  const [drawer, setDrawer] = useState(false)
  const [search, setSearch] = useState(false)
  const [online, setOnline] = useState(navigator.onLine)
  const loc = useLocation()
  useEffect(() => { setDrawer(false); setSearch(false) }, [loc.pathname, loc.search])
  useEffect(() => {
    const on = () => setOnline(true), off = () => setOnline(false)
    window.addEventListener('online', on); window.addEventListener('offline', off)
    return () => { window.removeEventListener('online', on); window.removeEventListener('offline', off) }
  }, [])
  return (
    <div className="flex h-full">
      <aside className="hidden w-60 shrink-0 overflow-y-auto border-r bg-white lg:block">
        <div className="flex items-center gap-2 px-5 py-4"><div className="rounded-lg bg-brand-600 px-2 py-1 text-sm font-bold text-white">QQ</div><div className="text-sm font-semibold leading-tight">QQ Food USA<div className="text-xs font-normal text-slate-400">597 运营</div></div></div>
        <Nav />
      </aside>
      {drawer && (
        <div className="fixed inset-0 z-40 bg-slate-900/40 lg:hidden" onClick={() => setDrawer(false)}>
          <aside className="h-full w-[min(85vw,300px)] overflow-y-auto bg-white pt-[env(safe-area-inset-top)]" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between px-4 py-3"><span className="font-semibold">QQ Food 597</span><button className="btn-ghost" onClick={() => setDrawer(false)}><X size={18} /></button></div>
            <Nav onNavigate={() => setDrawer(false)} />
            <div className="border-t p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
              <div className="mb-2 px-3 text-sm">{me?.name} <span className="text-xs text-slate-400">{me?.role_label}</span></div>
              <Link to="/me" className="btn-ghost w-full justify-start">{t('nav.me')}</Link>
              <button className="btn-ghost w-full justify-start text-red-600" onClick={logout}><LogOut size={18} />{t('nav.logout')} / {t('nav.switch')}</button>
            </div>
          </aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex items-center gap-2 border-b bg-white/90 px-2 py-1.5 pt-[max(0.375rem,env(safe-area-inset-top))] backdrop-blur sm:px-5 sm:py-2">
          <button className="btn-ghost lg:hidden" onClick={() => setDrawer(true)} aria-label="菜单"><Menu size={20} /></button>
          <Link to="/" className="flex items-center gap-1.5 lg:hidden"><span className="rounded-md bg-brand-600 px-1.5 py-0.5 text-xs font-bold text-white">QQ</span><span className="text-sm font-semibold">597</span></Link>
          <GlobalSearch />
          <div className="ml-auto flex items-center gap-0.5">
            <button className="btn-ghost md:hidden" onClick={() => setSearch(true)} aria-label={t('common.search')}><Search size={20} /></button>
            <Notifications />
            <Link to="/me" className="btn-ghost text-sm"><span className="hidden sm:inline">{me?.name}</span><span className="chip bg-slate-100 text-slate-500">{me?.role_label}</span></Link>
            <button className="btn-ghost hidden sm:inline-flex" onClick={logout} title={t('nav.logout')}><LogOut size={18} /></button>
          </div>
        </header>
        {search && (
          <div className="fixed inset-0 z-50 overflow-y-auto bg-slate-50 p-3 pt-[max(0.75rem,env(safe-area-inset-top))] md:hidden">
            <div className="mb-2 flex items-center gap-2"><span className="font-semibold">{t('common.search')}</span><button className="btn-ghost ml-auto" onClick={() => setSearch(false)} aria-label={t('common.close')}><X size={20} /></button></div>
            <GlobalSearch mobile onDone={() => setSearch(false)} />
          </div>
        )}
        {!online && <div className="bg-amber-100 px-4 py-2 text-center text-sm text-amber-800">{t('offline')}</div>}
        <main className="flex-1 overflow-y-auto overflow-x-hidden px-3 py-4 pb-[calc(88px+env(safe-area-inset-bottom))] sm:px-5 lg:pb-6">{children}</main>
        <nav className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-4 border-t bg-white/95 pb-[env(safe-area-inset-bottom)] backdrop-blur lg:hidden">
          {[['/', <Home key="h" size={22} />, me?.role === 'boss' ? '总览' : t('nav.home')], ['/tasks', <ListChecks key="t" size={22} />, '任务'], ['/ingest', <Inbox key="i" size={22} />, '资料'], ['#menu', <Menu key="m" size={22} />, t('nav.pages')]].map(([to, icon, label]: any) => (
            to === '#menu'
              ? <button key={to} onClick={() => setDrawer(true)} className="flex min-h-[56px] flex-col items-center justify-center gap-0.5 text-xs text-slate-600">{icon}{label}</button>
              : <NavLink key={to} to={to} end className={({ isActive }) => `flex min-h-[56px] flex-col items-center justify-center gap-0.5 text-xs ${isActive ? 'text-brand-700' : 'text-slate-600'}`}>{icon}{label}</NavLink>
          ))}
        </nav>
      </div>
    </div>
  )
}
