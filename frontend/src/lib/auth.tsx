import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { get, getToken, post, setToken, setUnauthorizedHandler } from './api'
import { setLang } from './i18n'
import { setUserTz } from './fmt'

export type Me = { id: string; username: string; name: string; role: string; role_label: string; lang: string; tz: string; perms: string[]; has_pin: boolean; agent_enabled: boolean; org_mode: string }
type Ctx = { me: Me | null; loading: boolean; login: (u: string, p: string, pin?: boolean) => Promise<void>; logout: () => void; refresh: () => Promise<void>; can: (p: string) => boolean }

const AuthCtx = createContext<Ctx>(null as any)
export const useAuth = () => useContext(AuthCtx)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null)
  const [loading, setLoading] = useState(true)
  const apply = (m: Me | null) => { if (m) { setLang(m.lang); setUserTz(m.tz) } setMe(m) }
  const logout = useCallback(() => { setToken(null); apply(null) }, [])
  const refresh = useCallback(async () => {
    if (!getToken()) { setLoading(false); return }
    try { apply(await get<Me>('/api/me')) } catch { setToken(null); apply(null) } finally { setLoading(false) }
  }, [])
  useEffect(() => { setUnauthorizedHandler(logout); refresh() }, [logout, refresh])
  const login = async (username: string, secret: string, pin = false) => {
    const r = await post(pin ? '/api/auth/pin' : '/api/auth/login', pin ? { username, pin: secret } : { username, password: secret })
    setToken(r.token)
    apply(r.user)
  }
  const can = (p: string) => !!me?.perms.includes(p)
  return <AuthCtx.Provider value={{ me, loading, login, logout, refresh, can }}>{children}</AuthCtx.Provider>
}
