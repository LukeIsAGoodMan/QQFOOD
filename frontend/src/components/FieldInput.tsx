import { useEffect, useState } from 'react'
import { get } from '../lib/api'

let usersCache: Promise<any[]> | null = null
export const loadUsers = () => (usersCache ??= get('/api/users'))

export function useUsers() {
  const [users, setUsers] = useState<any[]>([])
  useEffect(() => { loadUsers().then(setUsers).catch(() => {}) }, [])
  return users
}

export function FieldInput({ f, value, onChange }: { f: any; value: any; onChange: (v: any) => void }) {
  const users = useUsers()
  const v = value ?? ''
  switch (f.ftype) {
    case 'longtext':
      return <textarea className="input h-20" value={v} onChange={(e) => onChange(e.target.value)} />
    case 'number':
    case 'money':
      return <input className="input" type="number" inputMode="decimal" step="any" value={v} onChange={(e) => onChange(e.target.value === '' ? null : e.target.value)} />
    case 'date':
      return <input className="input" type="date" value={v} onChange={(e) => onChange(e.target.value || null)} />
    case 'select':
      return (
        <select className="input" value={v} onChange={(e) => onChange(e.target.value || null)}>
          <option value="">—</option>
          {f.options.map((o: string) => <option key={o}>{o}</option>)}
        </select>
      )
    case 'person':
      return (
        <select className="input" value={v} onChange={(e) => onChange(e.target.value || null)}>
          <option value="">—</option>
          {users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
        </select>
      )
    case 'checkbox':
      return <input type="checkbox" className="h-5 w-5" checked={!!value} onChange={(e) => onChange(e.target.checked)} />
    default:
      return <input className="input" value={v} onChange={(e) => onChange(e.target.value)} />
  }
}

export function fieldDisplay(f: any, value: any, users: any[] = []) {
  if (value === null || value === undefined || value === '') return '—'
  if (f.ftype === 'money') return `$${Number(value).toLocaleString('en-US', { minimumFractionDigits: 2 })}`
  if (f.ftype === 'person') return users.find((u) => u.id === value)?.name || value
  if (f.ftype === 'checkbox') return value ? '是' : '否'
  return String(value)
}

export const FTYPE_LABEL: Record<string, string> = {
  text: '文本', longtext: '长文本', number: '数值', money: '金额', date: '日期', select: '选择', person: '人员', checkbox: '勾选',
}
