import { useState } from 'react'
import { useAuth } from '../lib/auth'
import { get } from '../lib/api'
import { t } from '../lib/i18n'
import { Tabs, errMsg, useLoad } from '../components/ui'

export function Login() {
  const { login } = useAuth()
  const [tab, setTab] = useState('pw')
  const [u, setU] = useState('')
  const [p, setP] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const kiosk = useLoad(() => (tab === 'pin' ? get('/api/auth/kiosk-users') : Promise.resolve([])), [tab])
  const submit = async (e: any) => {
    e.preventDefault()
    setBusy(true); setErr('')
    try { await login(u, p, tab === 'pin') } catch (x) { setErr(errMsg(x)) } finally { setBusy(false) }
  }
  return (
    <div className="flex min-h-full items-center justify-center bg-gradient-to-br from-brand-50 to-slate-100 p-4">
      <div className="card w-full max-w-sm p-6">
        <div className="mb-5 flex items-center gap-2"><div className="rounded-lg bg-brand-600 px-2 py-1 font-bold text-white">QQ</div><div><div className="font-semibold">QQ Food USA · 597</div><div className="text-xs text-slate-500">运营管理系统 v0.1</div></div></div>
        <Tabs tabs={[{ key: 'pw', label: t('login.title') }, { key: 'pin', label: t('login.kiosk') }]} value={tab} onChange={(k) => { setTab(k); setP(''); setErr('') }} />
        <form onSubmit={submit} className="mt-4 space-y-3">
          {tab === 'pw' ? (
            <input className="input" autoComplete="username" placeholder={t('login.username')} value={u} onChange={(e) => setU(e.target.value)} />
          ) : (
            <div className="grid grid-cols-2 gap-2">
              {(kiosk.data || []).map((k: any) => (
                <button type="button" key={k.username} onClick={() => setU(k.username)} className={`btn ring-1 ${u === k.username ? 'bg-brand-600 text-white ring-brand-600' : 'ring-slate-300'}`}>{k.name}</button>
              ))}
            </div>
          )}
          <input className="input text-lg" type="password" inputMode={tab === 'pin' ? 'numeric' : undefined} autoComplete={tab === 'pin' ? 'off' : 'current-password'}
            placeholder={tab === 'pin' ? t('login.pin') : t('login.password')} value={p} onChange={(e) => setP(e.target.value)} />
          {err && <div className="text-sm text-red-600">{err}</div>}
          <button className="btn-primary w-full py-3" disabled={busy || !u || !p}>{t('login.title')}</button>
        </form>
        {['localhost', '127.0.0.1'].includes(location.hostname) && <p className="mt-4 text-xs text-slate-400">本机演示账号：boss / ops / buyer / wh / prod / qa / hrfin / staff1 / pageadmin / admin，密码 qq597demo；现场 PIN 1234</p>}
      </div>
    </div>
  )
}
