// Thin fetch wrapper: bearer token, JSON, readable error messages.
export class ApiError extends Error {
  status: number
  detail: any
  constructor(status: number, detail: any) {
    super(typeof detail === 'string' ? detail : detail?.msg || JSON.stringify(detail))
    this.status = status
    this.detail = detail
  }
}

let token: string | null = null
try { token = localStorage.getItem('qq_token') } catch { /* private mode */ }

export function setToken(t: string | null) {
  token = t
  try { t ? localStorage.setItem('qq_token', t) : localStorage.removeItem('qq_token') } catch { /* ignore */ }
}
export const getToken = () => token

let onUnauthorized: () => void = () => {}
export const setUnauthorizedHandler = (fn: () => void) => { onUnauthorized = fn }

export async function api<T = any>(path: string, opts: { method?: string; body?: any; form?: FormData } = {}): Promise<T> {
  const headers: Record<string, string> = {}
  if (token) headers.Authorization = `Bearer ${token}`
  let body: any
  if (opts.form) body = opts.form
  else if (opts.body !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(opts.body) }
  const res = await fetch(path, { method: opts.method || (body ? 'POST' : 'GET'), headers, body })
  if (res.status === 401 && !path.startsWith('/api/auth')) onUnauthorized()
  const text = await res.text()
  const data = text ? JSON.parse(text) : null
  if (!res.ok) throw new ApiError(res.status, data?.detail ?? res.statusText)
  return data as T
}

export const get = <T = any>(p: string) => api<T>(p)
export const post = <T = any>(p: string, body: any = {}) => api<T>(p, { method: 'POST', body })
export const patch = <T = any>(p: string, body: any) => api<T>(p, { method: 'PATCH', body })
export const put = <T = any>(p: string, body: any) => api<T>(p, { method: 'PUT', body })
export const del = <T = any>(p: string) => api<T>(p, { method: 'DELETE' })

// Per-file upload with progress (XHR, since fetch has no upload progress).
export function upload(path: string, form: FormData, onProgress?: (pct: number) => void): Promise<any> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', path)
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100)) }
    xhr.onload = () => {
      let data: any = null
      try { data = JSON.parse(xhr.responseText) } catch { /* empty */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data)
      else reject(new ApiError(xhr.status, data?.detail ?? xhr.statusText))
    }
    xhr.onerror = () => reject(new ApiError(0, '网络错误，请重试'))
    xhr.send(form)
  })
}

export const uuid = () => (crypto as any).randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`
