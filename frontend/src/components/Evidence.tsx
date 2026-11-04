import { useCallback, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Camera, Check, FileText, Library, Lock, Paperclip, RefreshCw, RotateCw, Unlink, X, ZoomIn, ZoomOut } from 'lucide-react'
import { get, post, upload } from '../lib/api'
import { useAuth } from '../lib/auth'
import { bytes, dt } from '../lib/fmt'
import { t } from '../lib/i18n'
import { Badge, EV_COLOR, Empty, Modal, errMsg, useLoad, useToast } from './ui'

const MAX = 50 * 1024 * 1024
const BATCH = 20
const ACCEPT = 'image/jpeg,image/png,image/heic,image/heif,image/webp,application/pdf,.heic,.docx,.xlsx,.csv'

export type QItem = { key: string; file: File; pct: number; state: 'queued' | 'uploading' | 'done' | 'failed'; error?: string; result?: any }

/** Per-file upload queue: progress, failure, retry only failed. Unfinished files never count as evidence. */
export function useUploader(extra: () => Record<string, string>, onDone?: (r: any) => void, path = '/api/evidence/upload') {
  const [queue, setQueue] = useState<QItem[]>([])
  const toast = useToast()
  const run = useCallback(async (items: QItem[]) => {
    for (const it of items) {
      setQueue((q) => q.map((x) => (x.key === it.key ? { ...x, state: 'uploading', pct: 0, error: undefined } : x)))
      const fd = new FormData()
      fd.append('file', it.file)
      Object.entries(extra()).forEach(([k, v]) => v != null && fd.append(k, v))
      try {
        const r = await upload(path, fd, (pct) => setQueue((q) => q.map((x) => (x.key === it.key ? { ...x, pct } : x))))
        setQueue((q) => q.map((x) => (x.key === it.key ? { ...x, state: 'done', pct: 100, result: r } : x)))
        if (r.dedup) toast('相同文件已存在，已直接引用原件')
        onDone?.(r)
      } catch (e) {
        setQueue((q) => q.map((x) => (x.key === it.key ? { ...x, state: 'failed', error: errMsg(e) } : x)))
      }
    }
  }, [extra, onDone, toast, path])
  const add = (files: FileList | File[]) => {
    const arr = Array.from(files)
    if (arr.length > BATCH) { toast(`每批最多 ${BATCH} 个文件`, 'err'); return }
    const items: QItem[] = []
    for (const f of arr) {
      const it: QItem = { key: `${f.name}-${f.size}-${Math.random()}`, file: f, pct: 0, state: 'queued' }
      if (f.size > MAX) { it.state = 'failed'; it.error = '超过 50MB 上限' }
      items.push(it)
    }
    setQueue((q) => [...q.filter((x) => x.state !== 'done'), ...items])
    run(items.filter((i) => i.state === 'queued'))
  }
  const retry = () => run(queue.filter((q) => q.state === 'failed' && q.file.size <= MAX))
  const clear = () => setQueue([])
  return { queue, add, retry, clear }
}

export function UploadQueue({ queue, onRetry }: { queue: QItem[]; onRetry: () => void }) {
  if (!queue.length) return null
  const failed = queue.filter((q) => q.state === 'failed').length
  return (
    <div className="mt-2 space-y-1 rounded-lg bg-slate-50 p-2">
      {queue.map((q) => (
        <div key={q.key} className="flex items-center gap-2 text-xs">
          <span className="w-24 truncate sm:w-56">{q.file.name}</span>
          <span className="text-slate-400">{bytes(q.file.size)}</span>
          <div className="h-1.5 flex-1 overflow-hidden rounded bg-slate-200">
            <div className={`h-full ${q.state === 'failed' ? 'bg-red-500' : 'bg-brand-500'}`} style={{ width: `${q.state === 'failed' ? 100 : q.pct}%` }} />
          </div>
          <span className={q.state === 'failed' ? 'text-red-600' : q.state === 'done' ? 'text-emerald-600' : 'text-slate-500'}>
            {q.state === 'failed' ? `${t('ev.failed')}：${q.error}` : q.state === 'done' ? t('ev.done') : `${t('ev.uploading')} ${q.pct}%`}
          </span>
        </div>
      ))}
      {failed > 0 && <button className="btn-secondary btn-sm" onClick={onRetry}><RefreshCw size={12} /> 重试失败的 {failed} 个</button>}
    </div>
  )
}

export function PickButtons({ onFiles, onLibrary, compact }: { onFiles: (f: FileList) => void; onLibrary?: () => void; compact?: boolean }) {
  const cam = useRef<HTMLInputElement>(null)
  const file = useRef<HTMLInputElement>(null)
  const cls = compact ? 'btn-secondary btn-sm' : 'btn-secondary'
  return (
    <div className="flex flex-wrap gap-2">
      <button type="button" className={cls} onClick={() => cam.current?.click()}><Camera size={16} />{t('ev.camera')}</button>
      <button type="button" className={cls} onClick={() => file.current?.click()}><Paperclip size={16} />{t('ev.upload')}</button>
      {onLibrary && <button type="button" className={cls} onClick={onLibrary}><Library size={16} />{t('ev.pick')}</button>}
      <input ref={cam} type="file" accept="image/*" capture="environment" multiple hidden onChange={(e) => { e.target.files && onFiles(e.target.files); e.target.value = '' }} />
      <input ref={file} type="file" accept={ACCEPT} multiple hidden onChange={(e) => { e.target.files && onFiles(e.target.files); e.target.value = '' }} />
    </div>
  )
}

function Thumb({ l, onClick }: { l: any; onClick: () => void }) {
  if (!l.readable) return <div className="flex h-14 w-14 items-center justify-center rounded bg-slate-100"><Lock size={18} className="text-slate-400" /></div>
  if (l.mime?.startsWith('image/') && l.mime !== 'image/heic') return <img src={l.url} onClick={onClick} loading="lazy" className="h-14 w-14 cursor-pointer rounded object-cover ring-1 ring-slate-200" alt={l.title} />
  return <button onClick={onClick} className="flex h-14 w-14 items-center justify-center rounded bg-slate-100 text-[10px] text-slate-500"><FileText size={20} /></button>
}

export function Preview({ link, onClose }: { link: any; onClose: () => void }) {
  const [zoom, setZoom] = useState(1)
  const [rot, setRot] = useState(0)
  const [failed, setFailed] = useState(false)
  if (!link) return null
  const isImg = link.mime?.startsWith('image/')
  const isPdf = link.mime === 'application/pdf'
  return (
    <Modal open onClose={onClose} title={link.title} wide>
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
        <span>v{link.version_no}</span><span>{link.filename}</span><span>sha {link.sha256}</span>
        <span>上传 {link.uploaded_by} · {dt(link.uploaded_at)}</span>
        {link.occurred_at && <span>发生时间 {dt(link.occurred_at)}</span>}
        <a className="text-brand-700 underline" href={`${link.url}&download=1`}>下载原件</a>
        <Link className="text-brand-700 underline" to={`/evidence/${link.evidence_id}`} onClick={onClose}>证据详情</Link>
      </div>
      {isImg && !failed && (
        <>
          <div className="mb-2 flex gap-2">
            <button className="btn-secondary btn-sm" onClick={() => setZoom((z) => Math.min(z + 0.25, 4))}><ZoomIn size={14} /></button>
            <button className="btn-secondary btn-sm" onClick={() => setZoom((z) => Math.max(z - 0.25, 0.25))}><ZoomOut size={14} /></button>
            <button className="btn-secondary btn-sm" onClick={() => setRot((r) => (r + 90) % 360)}><RotateCw size={14} /></button>
          </div>
          <div className="flex max-h-[70dvh] justify-center overflow-auto rounded bg-slate-100 p-2">
            <img src={link.url} onError={() => setFailed(true)} style={{ transform: `scale(${zoom}) rotate(${rot}deg)`, transformOrigin: 'center top' }} className="max-w-full transition" alt="" />
          </div>
        </>
      )}
      {isPdf && <>
        <iframe title="pdf" src={link.url} className="hidden h-[70vh] w-full rounded border sm:block" />
        <a className="btn-primary w-full sm:hidden" href={link.url} target="_blank" rel="noreferrer"><FileText size={16} />打开 PDF（可翻页、缩放）</a>
      </>}
      {(failed || (!isImg && !isPdf)) && (
        <div className="rounded bg-slate-50 p-6 text-center text-sm text-slate-500">
          {failed ? '此浏览器无法预览该格式（如 HEIC），原件已保留。' : '该类型暂不支持在线预览。'}
          <a className="ml-2 text-brand-700 underline" href={`${link.url}&download=1`}>下载原件</a>
        </div>
      )}
    </Modal>
  )
}

function LibraryPicker({ open, onClose, onPick }: { open: boolean; onClose: () => void; onPick: (id: string) => void }) {
  const [q, setQ] = useState('')
  const { data } = useLoad(() => (open ? get(`/api/evidence/library?q=${encodeURIComponent(q)}`) : Promise.resolve([])), [open, q])
  return (
    <Modal open={open} onClose={onClose} title={t('ev.pick')}>
      <input className="input mb-3" placeholder={t('common.search')} value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="space-y-1">
        {(data || []).map((e: any) => (
          <button key={e.evidence_id} onClick={() => onPick(e.evidence_id)} className="flex w-full items-center gap-3 rounded p-2 text-left hover:bg-slate-50">
            {e.url ? <img src={e.url} className="h-10 w-10 rounded object-cover" alt="" /> : <FileText className="h-10 w-10 p-2 text-slate-400" />}
            <div className="min-w-0 flex-1"><div className="truncate text-sm">{e.title}</div><div className="text-xs text-slate-400">{dt(e.uploaded_at)} {e.sensitivity !== 'normal' && `· ${e.sensitivity}`}</div></div>
          </button>
        ))}
        {data && !data.length && <Empty />}
      </div>
    </Modal>
  )
}

/** Unified evidence area for any business object. */
export function EvidencePanel({ targetType, targetId, purposes = ['附件'], title, compact, onChange }: {
  targetType: string; targetId: string; purposes?: string[]; title?: string; compact?: boolean; onChange?: () => void
}) {
  const { can } = useAuth()
  const toast = useToast()
  const [purpose, setPurpose] = useState(purposes[0])
  const [sensitivity, setSensitivity] = useState('normal')
  const [preview, setPreview] = useState<any>(null)
  const [lib, setLib] = useState(false)
  const [drag, setDrag] = useState(false)
  const [verifying, setVerifying] = useState<any>(null)
  const [note, setNote] = useState('')
  const { data: links, reload } = useLoad(() => get(`/api/evidence?target_type=${targetType}&target_id=${targetId}`), [targetType, targetId])
  const extra = useCallback(() => ({ target_type: targetType, target_id: targetId, purpose, sensitivity }), [targetType, targetId, purpose, sensitivity])
  const done = useCallback(() => { reload(); onChange?.() }, [reload, onChange])
  const up = useUploader(extra, done)

  const verify = async (status: string) => {
    try {
      await post(`/api/evidence/links/${verifying.link_id}/verify`, { status, note })
      setVerifying(null); setNote(''); reload(); onChange?.()
    } catch (e) { toast(errMsg(e), 'err') }
  }
  const unlink = async (l: any) => {
    if (!confirm('只移除此处引用，不删除原件。继续？')) return
    try { await post(`/api/evidence/links/${l.link_id}/unlink`); reload(); onChange?.() } catch (e) { toast(errMsg(e), 'err') }
  }
  const pick = async (eid: string) => {
    try { await post('/api/evidence/link', { evidence_id: eid, target_type: targetType, target_id: targetId, purpose }); setLib(false); reload(); onChange?.() } catch (e) { toast(errMsg(e), 'err') }
  }

  return (
    <div
      onPaste={(e) => { if (e.clipboardData.files.length) up.add(e.clipboardData.files) }}
      onDragOver={(e) => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)}
      onDrop={(e) => { e.preventDefault(); setDrag(false); if (e.dataTransfer.files.length) up.add(e.dataTransfer.files) }}
      className={`rounded-lg ${drag ? 'ring-2 ring-brand-500' : ''}`}
    >
      {title && <div className="mb-2 text-sm font-semibold">{title}</div>}
      <div className="flex flex-wrap items-end gap-2">
        {purposes.length > 1 || purposes[0] !== '附件' ? (
          <select className="input w-auto" value={purpose} onChange={(e) => setPurpose(e.target.value)} aria-label={t('ev.purpose')}>
            {[...new Set([...purposes, '附件'])].map((p) => <option key={p}>{p}</option>)}
          </select>
        ) : null}
        {!compact && (
          <select className="input w-auto" value={sensitivity} onChange={(e) => setSensitivity(e.target.value)} title="敏感级别">
            <option value="normal">普通</option><option value="finance">财务</option><option value="hr">人事</option>
          </select>
        )}
        <PickButtons onFiles={up.add} onLibrary={() => setLib(true)} compact={compact} />
      </div>
      {!compact && <div className="mt-1 text-xs text-slate-400">支持拖拽或粘贴图片；JPG/PNG/HEIC/PDF 可预览，单文件 ≤50MB，每批 ≤20 个</div>}
      <UploadQueue queue={up.queue} onRetry={up.retry} />
      <div className="mt-3 space-y-2">
        {(links || []).map((l: any) => (
          <div key={l.link_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-slate-100 p-2">
            <Thumb l={l} onClick={() => l.readable && setPreview(l)} />
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm">{l.readable ? l.title : t('ev.restricted')}</div>
              <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-500">
                <Badge color="blue">{l.purpose}</Badge>
                <Badge color={EV_COLOR[l.verify_status]}>{t(`ev.${l.verify_status}`)}</Badge>
                <span>v{l.version_no}{!l.is_latest_version && '（旧版本）'}</span>
                {l.readable && <span>{l.uploaded_by} · {dt(l.uploaded_at)}</span>}
                {l.verified_by && <span>核验 {l.verified_by}{l.verify_note && `：${l.verify_note}`}</span>}
              </div>
            </div>
            <div className="ml-auto flex gap-1">
              {l.readable && can('evidence.verify') && l.verify_status === 'unverified' && (
                <button className="btn-secondary btn-sm" onClick={() => setVerifying(l)}><Check size={14} />核验</button>
              )}
              <button className="btn-ghost btn-sm" title="解除关联" aria-label="解除关联" onClick={() => unlink(l)}><Unlink size={14} /></button>
            </div>
          </div>
        ))}
        {links && !links.length && !compact && <div className="text-xs text-slate-400">尚无证据</div>}
      </div>
      <Preview link={preview} onClose={() => setPreview(null)} />
      <LibraryPicker open={lib} onClose={() => setLib(false)} onPick={pick} />
      <Modal open={!!verifying} onClose={() => setVerifying(null)} title={`核验证据：${verifying?.title}`}
        footer={<><button className="btn-danger" onClick={() => verify('rejected')}><X size={14} />驳回</button><button className="btn-primary" onClick={() => verify('approved')}><Check size={14} />通过</button></>}>
        <p className="mb-2 text-sm text-slate-600">用途：<b>{verifying?.purpose}</b>。核验只代表该证据与用途相符，不自动代表整个事项完成。</p>
        <textarea className="input h-20" placeholder="核验意见（驳回必填）" value={note} onChange={(e) => setNote(e.target.value)} />
      </Modal>
    </div>
  )
}
