import { useEffect, useRef, useState } from 'react'
import QRCode from 'qrcode'
import { Printer, ScanLine } from 'lucide-react'
import { t } from '../lib/i18n'
import { Modal } from './ui'

/** Camera scan via BarcodeDetector (Android Chrome etc.); always offers manual entry / USB scanner input. */
export function ScanModal({ open, onClose, onResult }: { open: boolean; onClose: () => void; onResult: (code: string) => void }) {
  const video = useRef<HTMLVideoElement>(null)
  const [manual, setManual] = useState('')
  const supported = typeof window !== 'undefined' && 'BarcodeDetector' in window
  useEffect(() => {
    if (!open || !supported) return
    let stream: MediaStream | null = null
    let alive = true
    const Detector = (window as any).BarcodeDetector
    const det = new Detector({ formats: ['qr_code', 'code_128', 'ean_13', 'upc_a', 'code_39'] })
    navigator.mediaDevices?.getUserMedia({ video: { facingMode: 'environment' } }).then(async (s) => {
      stream = s
      if (!video.current) return
      video.current.srcObject = s
      await video.current.play()
      const tick = async () => {
        if (!alive || !video.current) return
        try {
          const codes = await det.detect(video.current)
          if (codes.length) { onResult(codes[0].rawValue); return }
        } catch { /* frame not ready */ }
        requestAnimationFrame(tick)
      }
      tick()
    }).catch(() => {})
    return () => { alive = false; stream?.getTracks().forEach((x) => x.stop()) }
  }, [open, supported, onResult])
  return (
    <Modal open={open} onClose={onClose} title={t('scan.title')}>
      {supported ? <video ref={video} className="mb-3 aspect-video w-full rounded bg-black" muted playsInline /> : <p className="mb-3 text-sm text-slate-500">{t('scan.noCamera')}</p>}
      <form onSubmit={(e) => { e.preventDefault(); if (manual.trim()) onResult(manual.trim()) }} className="flex gap-2">
        <input autoFocus className="input" placeholder={t('scan.manual')} value={manual} onChange={(e) => setManual(e.target.value)} />
        <button className="btn-primary">{t('common.confirm')}</button>
      </form>
    </Modal>
  )
}

export function ScanButton({ onResult, label }: { onResult: (code: string) => void; label?: string }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" className="btn-secondary" onClick={() => setOpen(true)}><ScanLine size={16} />{label || t('scan.title')}</button>
      <ScanModal open={open} onClose={() => setOpen(false)} onResult={(c) => { setOpen(false); onResult(c) }} />
    </>
  )
}

/** Printable lot label with QR (LOT:<sku>:<lot>). */
export function LotLabel({ onClose, sku, lot }: { onClose: () => void; sku: any; lot: any }) {
  const [src, setSrc] = useState('')
  const code = sku && lot ? `LOT:${sku.code}:${lot.lot_code}` : ''
  useEffect(() => { if (code) QRCode.toDataURL(code, { margin: 1, width: 240 }).then(setSrc) }, [code])
  if (!sku || !lot) return null
  return (
    <Modal open onClose={onClose} title="批次标签" footer={<button className="btn-primary" onClick={() => window.print()}><Printer size={16} />打印</button>}>
      <div className="print-area mx-auto w-[4in] max-w-full rounded border-2 border-black p-3 text-black">
        <div className="flex gap-3">
          {src && <img src={src} className="h-28 w-28" alt={code} />}
          <div className="text-sm leading-6">
            <div className="text-lg font-bold">{sku.name}</div>
            <div>SKU：{sku.code}</div>
            <div>LOT：<b>{lot.lot_code}</b></div>
            {lot.expiry && <div>EXP：{lot.expiry}</div>}
            <div>状态：{lot.quality_status === 'released' ? 'RELEASED' : 'HOLD — 禁止使用'}</div>
          </div>
        </div>
        <div className="mt-1 font-mono text-[10px]">{code}</div>
      </div>
    </Modal>
  )
}
