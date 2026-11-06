import { Component, type ReactNode } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import { get } from '../lib/api'
import { Field, useLoad } from '../components/ui'
import { ChecklistModule } from './checklist'
import { EvidenceModule, FormModule, IngestModule, KanbanModule, MetricModule, TableModule, TextModule, TimelineModule, type ModProps } from './basic'
import { LotsModule, ProductionModule, StockModule, TraceModule, TransfersModule } from './inventory'
import { PurchasingModule, RequisitionsModule } from './purchasing'
import { BoxesModule, OrdersModule } from './orders'

type Def = { label: string; group: string; description: string; Render: (p: ModProps) => ReactNode; defaults: () => any; width?: string }

export const MODULES: Record<string, Def> = {
  metric: { label: '指标卡', group: '展示', description: '数值、口径、更新时间、下钻', Render: MetricModule, defaults: () => ({ metric: 'overdue_tasks' }), width: 'third' },
  table: { label: '数据表格', group: '展示', description: '绑定记录类型：筛选、搜索、列配置', Render: TableModule, defaults: () => ({ source: 'records', record_type_id: null, columns: ['title', 'status', 'owner', 'updated_at'] }) },
  kanban: { label: '看板/任务列表', group: '展示', description: '按状态分组的任务或记录', Render: KanbanModule, defaults: () => ({ source: 'tasks', task_filter: {} }) },
  form: { label: '记录表单', group: '录入', description: '新建某类记录，含校验', Render: FormModule, defaults: () => ({ source: 'records', record_type_id: null }), width: 'half' },
  checklist: { label: '检查表', group: '录入', description: '合格/不合格/实测值/不适用理由；不合格自动生成整改', Render: ChecklistModule, defaults: () => ({ daily_required: false, create_task_on_fail: true, items: [{ key: 'item1', label: '检查项 1', kind: 'pass_fail' }] }) },
  evidence: { label: '图片/PDF 证据区', group: '录入', description: '拍照、上传、核验', Render: EvidenceModule, defaults: () => ({ purposes: ['现场照片', '附件'] }), width: 'half' },
  timeline: { label: '时间线/评论', group: '录入', description: '沟通与 @提及', Render: TimelineModule, defaults: () => ({}), width: 'half' },
  text: { label: '说明文字', group: '展示', description: '页面说明或操作提示', Render: TextModule, defaults: () => ({ body: '' }) },
  ingest: { label: '上传资料', group: '录入', description: '送入资料中心：识别建议或仅存证', Render: IngestModule, defaults: () => ({}), width: 'half' },
  stock: { label: '多仓库存', group: '业务（专用）', description: '实物/隔离/预留/可用/在途/未核数，收货盘点出库', Render: StockModule, defaults: () => ({}) },
  transfers: { label: '调拨', group: '业务（专用）', description: '申请→发运→在途→签收→差异', Render: TransfersModule, defaults: () => ({}) },
  lots: { label: '批次隔离与放行', group: '业务（专用）', description: '质量状态、电子签名、标签打印', Render: LotsModule, defaults: () => ({}) },
  production: { label: '生产批次', group: '业务（专用）', description: '原料批次投入→成品批次（待放行）', Render: ProductionModule, defaults: () => ({}) },
  purchasing: { label: '采购单与收货', group: '业务（专用）', description: '审批、下单、按行收货入账、发票与付款核对', Render: PurchasingModule, defaults: () => ({}) },
  requisitions: { label: '需求与报价对比', group: '业务（专用）', description: '需求版本（余量）、报价对比、生成采购单', Render: RequisitionsModule, defaults: () => ({}) },
  orders: { label: '客户订单与发货', group: '业务（专用）', description: 'PO 录入、配对 SKU、拣货/扫箱发货、签收与拒收', Render: OrdersModule, defaults: () => ({}) },
  boxes: { label: '称重打标签', group: '业务（专用）', description: '每箱称重、打印标签（磅+箱）', Render: BoxesModule, defaults: () => ({}) },
  trace: { label: '追溯查询', group: '业务（专用）', description: '正向/反向追溯，导出 CSV', Render: TraceModule, defaults: () => ({}) },
}

class Boundary extends Component<{ children: ReactNode }, { err: any }> {
  state = { err: null as any }
  static getDerivedStateFromError(err: any) { return { err } }
  render() { return this.state.err ? <div className="rounded bg-red-50 p-3 text-sm text-red-700">模块加载失败：{String(this.state.err?.message || this.state.err)}</div> : this.props.children }
}

// Phones: metric cards two per row, everything else full width. Tablets/desktop follow the configured width.
const WIDTH: Record<string, string> = { full: 'col-span-6', half: 'col-span-6 lg:col-span-3', third: 'col-span-6 sm:col-span-3 lg:col-span-2' }
const METRIC_WIDTH: Record<string, string> = { full: 'col-span-6', half: 'col-span-3', third: 'col-span-3 lg:col-span-2' }

export function ModuleCard({ module, page, preview, toolbar, phone }: { module: any; page: any; preview?: boolean; toolbar?: ReactNode; phone?: boolean }) {
  const def = MODULES[module.type]
  const isMetric = module.type === 'metric'
  return (
    <section className={`${phone ? (isMetric && module.width !== 'full' ? 'col-span-3' : 'col-span-6') : (isMetric ? METRIC_WIDTH : WIDTH)[module.width] || WIDTH.full} card min-w-0 ${isMetric ? 'p-3 sm:p-4' : 'p-3 sm:p-4'} ${module.hidden ? 'opacity-50' : ''}`}>
      {(toolbar || (module.title && !isMetric)) && (
        <div className="mb-3 flex items-center gap-2">
          {module.title && !isMetric && <h2 className="font-semibold">{module.title}</h2>}
          {toolbar && <div className="ml-auto flex items-center gap-1">{toolbar}</div>}
        </div>
      )}
      <Boundary>
        {def ? <def.Render module={module} page={page} preview={preview} /> : (
          <div className="rounded bg-slate-50 p-3 text-sm text-slate-500">此组件类型「{module.type}」（v{module.version}）当前版本不支持，显示为只读占位；页面其他部分不受影响。</div>
        )}
      </Boundary>
    </section>
  )
}

// ---------- config forms (used by the page editor) ----------
function RecordTypeSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { data } = useLoad(() => get('/api/record-types'), [])
  return (
    <select className="input" value={value || ''} onChange={(e) => onChange(e.target.value)}>
      <option value="">选择记录类型…</option>
      {(data || []).filter((r: any) => !r.archived).map((r: any) => <option key={r.id} value={r.id}>{r.name}</option>)}
    </select>
  )
}

function ColumnsPicker({ rtId, value, onChange }: { rtId: string; value: string[]; onChange: (v: string[]) => void }) {
  const { data } = useLoad(() => (rtId ? get(`/api/record-types/${rtId}`) : Promise.resolve(null)), [rtId])
  if (!data) return null
  const opts = [{ id: 'title', label: '标题' }, { id: 'status', label: '状态' }, { id: 'owner', label: '负责人' }, { id: 'updated_at', label: '更新时间' }, ...data.fields.map((f: any) => ({ id: f.id, label: f.label + (f.active ? '' : '（已停用）') }))]
  return (
    <div className="flex flex-wrap gap-2">
      {opts.map((o) => (
        <label key={o.id} className="flex items-center gap-1 text-sm">
          <input type="checkbox" checked={value.includes(o.id)} onChange={(e) => onChange(e.target.checked ? [...value, o.id] : value.filter((x) => x !== o.id))} />{o.label}
        </label>
      ))}
    </div>
  )
}

function MetricPicker({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { data } = useLoad(() => get('/api/metrics/catalog'), [])
  const isRec = value?.startsWith('records:')
  const [, rt = '', st = ''] = isRec ? value.split(':') : []
  const rtd = useLoad(() => (rt ? get(`/api/record-types/${rt}`) : Promise.resolve(null)), [rt])
  return (
    <div className="space-y-2">
      <select className="input" value={isRec ? '__records' : value} onChange={(e) => onChange(e.target.value === '__records' ? 'records::' : e.target.value)}>
        {(data || []).map((m: any) => <option key={m.key} value={m.key}>{m.label}</option>)}
        <option value="__records">某类记录的数量…</option>
      </select>
      {data && !isRec && <div className="text-xs text-slate-500">口径：{data.find((m: any) => m.key === value)?.definition}</div>}
      {isRec && (
        <>
          <RecordTypeSelect value={rt} onChange={(v) => onChange(`records:${v}:`)} />
          {rtd.data && (
            <div className="flex flex-wrap gap-2">
              {rtd.data.statuses.map((s: string) => {
                const sel = st ? st.split('|') : []
                return <label key={s} className="flex items-center gap-1 text-sm"><input type="checkbox" checked={sel.includes(s)} onChange={(e) => onChange(`records:${rt}:${(e.target.checked ? [...sel, s] : sel.filter((x) => x !== s)).join('|')}`)} />{s}</label>
              })}
            </div>
          )}
        </>
      )}
    </div>
  )
}

function ChecklistItems({ items, onChange }: { items: any[]; onChange: (v: any[]) => void }) {
  const set = (i: number, p: any) => onChange(items.map((x, j) => (i === j ? { ...x, ...p } : x)))
  return (
    <div className="space-y-2">
      {items.map((it, i) => (
        <div key={i} className="rounded-lg border p-2">
          <div className="flex gap-2">
            <input className="input" value={it.label} onChange={(e) => set(i, { label: e.target.value })} placeholder="检查项" />
            <select className="input w-32" value={it.kind} onChange={(e) => set(i, { kind: e.target.value })}><option value="pass_fail">合格/不合格</option><option value="number">实测值</option></select>
            <button className="btn-ghost btn-sm" onClick={() => onChange(items.filter((_, j) => j !== i))}><Trash2 size={14} /></button>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-3 text-sm">
            {it.kind === 'number' && <>
              <label>最小 <input className="input inline w-20" type="number" value={it.min ?? ''} onChange={(e) => set(i, { min: e.target.value === '' ? null : Number(e.target.value) })} /></label>
              <label>最大 <input className="input inline w-20" type="number" value={it.max ?? ''} onChange={(e) => set(i, { max: e.target.value === '' ? null : Number(e.target.value) })} /></label>
            </>}
            <label className="flex items-center gap-1"><input type="checkbox" checked={!!it.requires_evidence} onChange={(e) => set(i, { requires_evidence: e.target.checked })} />必须拍照</label>
            <span className="text-xs text-slate-400">ID {it.key}</span>
          </div>
        </div>
      ))}
      <button className="btn-ghost btn-sm" onClick={() => onChange([...items, { key: `item${Date.now().toString(36)}`, label: '', kind: 'pass_fail' }])}><Plus size={14} />添加检查项</button>
    </div>
  )
}

export function ModuleConfig({ module, onChange }: { module: any; onChange: (m: any) => void }) {
  const cfg = module.config || {}
  const setCfg = (p: any) => onChange({ ...module, config: { ...cfg, ...p } })
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <Field label="标题"><input className="input" value={module.title} onChange={(e) => onChange({ ...module, title: e.target.value })} placeholder={module.type === 'metric' ? '留空使用指标名' : ''} /></Field>
        <Field label="宽度"><select className="input" value={module.width} onChange={(e) => onChange({ ...module, width: e.target.value })}><option value="full">整行</option><option value="half">半行</option><option value="third">三分之一</option></select></Field>
      </div>
      {module.type === 'metric' && <Field label="指标"><MetricPicker value={cfg.metric} onChange={(v) => setCfg({ metric: v })} /></Field>}
      {['table', 'form'].includes(module.type) && <Field label="数据来源（记录类型）"><RecordTypeSelect value={cfg.record_type_id} onChange={(v) => setCfg({ record_type_id: v, columns: ['title', 'status', 'owner', 'updated_at'] })} /></Field>}
      {module.type === 'table' && cfg.record_type_id && <Field label="显示列"><ColumnsPicker rtId={cfg.record_type_id} value={cfg.columns || []} onChange={(v) => setCfg({ columns: v })} /></Field>}
      {module.type === 'kanban' && (
        <>
          <Field label="数据来源"><select className="input" value={cfg.source} onChange={(e) => setCfg({ source: e.target.value })}><option value="tasks">任务与异常</option><option value="records">某类记录</option></select></Field>
          {cfg.source === 'records' ? <Field label="记录类型"><RecordTypeSelect value={cfg.record_type_id} onChange={(v) => setCfg({ record_type_id: v })} /></Field> : (
            <div className="grid grid-cols-3 gap-2">
              <Field label="类型"><select className="input" value={cfg.task_filter?.kind || ''} onChange={(e) => setCfg({ task_filter: { ...cfg.task_filter, kind: e.target.value || undefined } })}><option value="">全部</option><option value="task">任务</option><option value="exception">异常</option><option value="approval">审批</option></select></Field>
              <Field label="负责人"><select className="input" value={cfg.task_filter?.owner || ''} onChange={(e) => setCfg({ task_filter: { ...cfg.task_filter, owner: e.target.value || undefined } })}><option value="">全部</option><option value="me">当前用户</option></select></Field>
              <Field label="状态列"><input className="input" placeholder="如 blocked,review" value={cfg.task_filter?.status || ''} onChange={(e) => setCfg({ task_filter: { ...cfg.task_filter, status: e.target.value || undefined } })} /></Field>
            </div>
          )}
        </>
      )}
      {module.type === 'checklist' && (
        <>
          <div className="flex flex-wrap gap-4 text-sm">
            <label className="flex items-center gap-1"><input type="checkbox" checked={!!cfg.daily_required} onChange={(e) => setCfg({ daily_required: e.target.checked })} />每日必填（未提交时首页提示）</label>
            <label className="flex items-center gap-1"><input type="checkbox" checked={cfg.create_task_on_fail !== false} onChange={(e) => setCfg({ create_task_on_fail: e.target.checked })} />不合格/超范围 → 生成整改任务</label>
          </div>
          <ChecklistItems items={cfg.items || []} onChange={(items) => setCfg({ items })} />
        </>
      )}
      {module.type === 'text' && <Field label="内容（- 开头为列表）"><textarea className="input h-32" value={cfg.body || ''} onChange={(e) => setCfg({ body: e.target.value })} /></Field>}
      {module.type === 'evidence' && <Field label="用途选项（逗号分隔）"><input className="input" value={(cfg.purposes || []).join(',')} onChange={(e) => setCfg({ purposes: e.target.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean) })} /></Field>}
      <div className="text-xs text-slate-400">模块 ID {module.id} · 组件 {module.type} v{module.version}</div>
    </div>
  )
}
