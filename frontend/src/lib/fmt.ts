// Store UTC, show the user's timezone (and the site's when it differs).
let userTz = 'America/Los_Angeles'
export const SITE_TZ = 'America/Los_Angeles'
export const setUserTz = (tz: string) => { userTz = tz || SITE_TZ }

function toDate(v: any): Date | null {
  if (!v) return null
  const s = typeof v === 'string' && !/[zZ]|[+-]\d\d:?\d\d$/.test(v) ? v + 'Z' : v
  const d = new Date(s)
  return isNaN(d.getTime()) ? null : d
}

export function dt(v: any, opts: { site?: boolean; dateOnly?: boolean } = {}) {
  const d = toDate(v)
  if (!d) return '—'
  const f = (tz: string) => d.toLocaleString('zh-CN', {
    timeZone: tz, month: '2-digit', day: '2-digit', ...(opts.dateOnly ? {} : { hour: '2-digit', minute: '2-digit', hour12: false }),
  })
  const main = f(userTz)
  if (opts.site && userTz !== SITE_TZ) return `${main}（场地 ${f(SITE_TZ)}）`
  return main
}

export function ago(v: any) {
  const d = toDate(v)
  if (!d) return '—'
  const s = (Date.now() - d.getTime()) / 1000
  if (s < 60) return '刚刚'
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`
  return `${Math.floor(s / 86400)} 天前`
}

export function toLocalInput(v: any) {
  const d = toDate(v)
  if (!d) return ''
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

export const num = (n: any) => (n === null || n === undefined ? '—' : Number(n).toLocaleString('en-US', { maximumFractionDigits: 2 }))
export const bytes = (n: number) => (n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.ceil(n / 1024)} KB`)

/** 商品以品名为主，大统华 Item# 作补充。 */
export const skuLabel = (s: any) => (s ? `${s.name}${s.tnt_item_no ? `（T&T ${s.tnt_item_no}）` : ''}` : '')
export const poLabel = (po?: string) => (po ? `PO ${po}` : 'PO 待补')
