import { useCallback, useState } from 'react'
import { Link } from 'react-router-dom'
import { Check, X, Minus } from 'lucide-react'
import { get, post } from '../lib/api'
import { dt } from '../lib/fmt'
import { t } from '../lib/i18n'
import { Badge, errMsg, useLoad, useToast } from '../components/ui'
import { PickButtons, UploadQueue, useUploader } from '../components/Evidence'
import type { ModProps } from './basic'

type R = { result?: 'pass' | 'fail' | 'na'; value?: string; note: string; ev: string[] }

function ItemPhotos({ onAdded, required }: { onAdded: (vid: string) => void; required?: boolean }) {
  const extra = useCallback(() => ({ purpose: '检查项证据' }), [])
  const up = useUploader(extra, (r) => onAdded(r.version_id))
  return (
    <div className="mt-2">
      <div className="flex items-center gap-2">
        <PickButtons compact onFiles={up.add} />
        {required && <span className="text-xs text-amber-600">{t('ck.photoRequired')}</span>}
      </div>
      <UploadQueue queue={up.queue} onRetry={up.retry} />
    </div>
  )
}

export function ChecklistModule({ module, page, preview }: ModProps) {
  const items: any[] = module.config.items || []
  const toast = useToast()
  const [res, setRes] = useState<Record<string, R>>({})
  const [busy, setBusy] = useState(false)
  const [formKey, setFormKey] = useState(0)
  const runs = useLoad(() => (preview ? Promise.resolve([]) : get(`/api/checklists/${page.id}/${module.id}/runs?limit=5`)), [module.id, preview])
  const set = (k: string, patch: Partial<R>) => setRes((s) => ({ ...s, [k]: { ...(s[k] || { note: '', ev: [] }), ...patch } }))

  const submit = async () => {
    if (preview) { toast('预览模式不会提交'); return }
    setBusy(true)
    try {
      const body = {
        results: items.map((it) => {
          const r = res[it.key] || { note: '', ev: [] }
          const result = it.kind === 'number' ? (r.result === 'na' ? 'na' : 'pass') : r.result
          return { key: it.key, result, value: r.value === undefined || r.value === '' ? null : Number(r.value), note: r.note, evidence_version_ids: r.ev }
        }),
      }
      const missing = items.filter((it) => it.kind !== 'number' && !res[it.key]?.result)
      if (missing.length) throw new Error(`未填写：${missing.map((m) => m.label).join('、')}`)
      const r = await post(`/api/checklists/${page.id}/${module.id}/runs`, body)
      toast(r.passed ? '已提交：全部合格' : `已提交：${r.failed.join('、')} 不合格，已生成整改任务`, r.passed ? 'ok' : 'err')
      setRes({}); setFormKey((k) => k + 1); runs.reload()
    } catch (e) { toast(errMsg(e), 'err') } finally { setBusy(false) }
  }

  return (
    <div>
      <div key={formKey} className="divide-y">
        {items.map((it) => {
          const r = res[it.key] || { note: '', ev: [] }
          const btn = (val: 'pass' | 'fail' | 'na', label: string, Icon: any, on: string) => (
            <button type="button" onClick={() => set(it.key, { result: val })}
              className={`btn flex-1 ring-1 sm:flex-none ${r.result === val ? on : 'bg-white text-slate-600 ring-slate-300'}`}><Icon size={16} />{label}</button>
          )
          const outOfRange = it.kind === 'number' && r.value !== undefined && r.value !== '' &&
            ((it.min != null && Number(r.value) < it.min) || (it.max != null && Number(r.value) > it.max))
          return (
            <div key={it.key} className="py-3">
              <div className="mb-2 text-sm font-medium">{it.label}{it.kind === 'number' && it.min != null && <span className="ml-1 text-xs text-slate-400">（{it.min} ~ {it.max}）</span>}</div>
              {it.kind === 'number' ? (
                <div className="flex gap-2">
                  <input className={`input max-w-[160px] text-lg ${outOfRange ? 'border-red-500 text-red-600' : ''}`} type="number" inputMode="decimal" step="any"
                    placeholder={t('ck.value')} value={r.value ?? ''} onChange={(e) => set(it.key, { value: e.target.value, result: undefined })} />
                  {btn('na', t('ck.na'), Minus, 'bg-slate-600 text-white ring-slate-600')}
                </div>
              ) : (
                <div className="flex gap-2">
                  {btn('pass', t('ck.pass'), Check, 'bg-emerald-600 text-white ring-emerald-600')}
                  {btn('fail', t('ck.fail'), X, 'bg-red-600 text-white ring-red-600')}
                  {btn('na', t('ck.na'), Minus, 'bg-slate-600 text-white ring-slate-600')}
                </div>
              )}
              {outOfRange && <div className="mt-1 text-xs text-red-600">超出范围，提交后会生成整改任务</div>}
              {(r.result === 'na' || r.result === 'fail' || outOfRange) && (
                <input className="input mt-2" placeholder={r.result === 'na' ? t('ck.naReason') + ' *' : '说明'} value={r.note} onChange={(e) => set(it.key, { note: e.target.value })} />
              )}
              {!preview && <ItemPhotos required={it.requires_evidence} onAdded={(vid) => set(it.key, { ev: [...(res[it.key]?.ev || []), vid] })} />}
              {r.ev.length > 0 && <div className="mt-1 text-xs text-emerald-600">已附 {r.ev.length} 张</div>}
            </div>
          )
        })}
      </div>
      <button className="btn-primary mt-2 w-full py-3 text-base" disabled={busy} onClick={submit}>{t('ck.submit')}</button>
      {!!runs.data?.length && (
        <div className="mt-4">
          <div className="mb-1 text-xs font-semibold text-slate-500">{t('ck.history')}</div>
          <div className="space-y-1">
            {runs.data.map((r: any) => (
              <div key={r.id} className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
                <Badge color={r.passed ? 'green' : 'red'}>{r.passed ? '合格' : '不合格'}</Badge>
                <span>{dt(r.at, { site: true })}</span><span>{r.by}</span>
                <span className="text-slate-400">{r.results.filter((x: any) => x.value != null).map((x: any) => `${x.label} ${x.value}`).join('，')}</span>
                {r.evidence > 0 && <span>📎{r.evidence}</span>}
              </div>
            ))}
          </div>
          <Link to="/tasks?f=exceptions" className="mt-1 inline-block text-xs text-brand-700 underline">查看整改任务</Link>
        </div>
      )}
    </div>
  )
}
