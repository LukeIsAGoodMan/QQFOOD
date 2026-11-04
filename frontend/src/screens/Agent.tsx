import { useCallback, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Bot, Check, Copy, ShieldOff } from 'lucide-react'
import { get, post } from '../lib/api'
import { useAuth } from '../lib/auth'
import { ago, dt } from '../lib/fmt'
import { Badge, Field, Spinner, errMsg, useLoad, useToast } from '../components/ui'
import { PickButtons, UploadQueue, useUploader } from '../components/Evidence'

function CopyRow({ label, value }: { label: string; value: string }) {
  const [done, setDone] = useState(false)
  return (
    <Field label={label}>
      <div className="flex gap-2">
        <input className="input font-mono text-base sm:text-xs" readOnly value={value} onFocus={(e) => e.target.select()} />
        <button className="btn-secondary shrink-0" aria-label={`复制${label}`} onClick={async () => { await navigator.clipboard.writeText(value); setDone(true); setTimeout(() => setDone(false), 1500) }}>
          {done ? <Check size={16} /> : <Copy size={16} />}
        </button>
      </div>
    </Field>
  )
}

/** 我的设置 → AI 助手（PRD §9.11）。 */
export function AgentSettings() {
  const { refresh } = useAuth()
  const toast = useToast()
  const { data, reload } = useLoad(() => get('/api/agent/status'), [])
  const [tab, setTab] = useState<'mcp' | 'gpt'>('mcp')
  if (!data) return <Spinner />
  const toggle = async () => {
    if (data.enabled && !confirm('关闭后，所有已连接的 AI 助手立即失去访问权限。继续？')) return
    try { await post('/api/agent/enable', { enabled: !data.enabled }); reload(); refresh() } catch (e) { toast(errMsg(e), 'err') }
  }
  const revoke = async (c: any) => {
    if (!confirm(`撤销「${c.client}」的访问？`)) return
    await post(`/api/agent/connections/${c.client_id}/revoke`); reload()
  }
  const tiers: Record<string, string> = { read: '读取', write: '低风险写入', propose: '提出待确认变更' }
  return (
    <div className="card mt-4 space-y-4 p-4">
      <div className="flex items-center gap-2"><Bot size={20} className="text-brand-700" /><h2 className="font-semibold">AI 助手（ChatGPT / Claude）</h2>
        <Badge color={data.enabled ? 'green' : 'gray'}>{data.enabled ? '已开启' : '未开启'}</Badge></div>
      <p className="text-sm text-slate-600">在手机上的 ChatGPT 或 Claude 里直接问「今天有什么要我处理」「Penske 那件事到哪了」。助手只能做你本人有权限的事；它提出的修改要你在本系统确认后才生效。质量放行、证据核验、库存过账、密码和人事附件永远不开放。</p>
      <button className={data.enabled ? 'btn-secondary w-full sm:w-auto' : 'btn-primary w-full sm:w-auto'} onClick={toggle}>
        {data.enabled ? <><ShieldOff size={16} />关闭并撤销全部连接</> : '开启 AI 助手访问'}
      </button>
      {data.enabled && (
        <>
          {!data.public && <div className="rounded bg-amber-50 p-3 text-sm text-amber-800">当前系统地址是本机地址（{data.mcp_url.replace('/mcp', '')}），ChatGPT 无法访问。需要先部署到带 HTTPS 的公网地址，或设置环境变量 QQ_PUBLIC_URL。</div>}
          <div className="seg w-fit" role="group">
            <button type="button" aria-pressed={tab === 'mcp'} onClick={() => setTab('mcp')}>连接器（MCP）</button>
            <button type="button" aria-pressed={tab === 'gpt'} onClick={() => setTab('gpt')}>自定义 GPT（备用）</button>
          </div>
          {tab === 'mcp' ? (
            <div className="space-y-3 text-sm">
              <ol className="list-decimal space-y-1 pl-5 text-slate-600">
                <li>在电脑浏览器打开 ChatGPT → 设置 → 应用与连接器（Apps &amp; Connectors）→ 高级设置，开启「开发者模式」。</li>
                <li>点「创建」，名称填「QQ 597」，服务器地址填下方链接，认证方式选 OAuth。</li>
                <li>按提示用你在本系统的账号登录授权。之后在对话里选择该连接器即可；手机端是否可直接使用以 ChatGPT 当前版本为准，不行就用「自定义 GPT」方式。</li>
              </ol>
              <CopyRow label="MCP 服务器地址" value={data.mcp_url} />
            </div>
          ) : (
            <div className="space-y-3 text-sm">
              <ol className="list-decimal space-y-1 pl-5 text-slate-600">
                <li>请系统管理员运行 <code className="rounded bg-slate-100 px-1">python -m scripts.create_gpt_client "老板的 GPT"</code> 生成 Client ID / Secret。</li>
                <li>ChatGPT → 我的 GPT → 创建 → 配置 → 操作（Actions）→ 从 URL 导入下方 Schema。</li>
                <li>认证选 OAuth：填入 Client ID / Secret、授权 URL、令牌 URL，Scope 填 <code className="rounded bg-slate-100 px-1">agent.read agent.write</code>。</li>
                <li>把下方「说明」粘贴到 GPT 的 Instructions；保存为「仅我自己」。手机 App 里打开这个 GPT 即可使用。</li>
              </ol>
              <CopyRow label="Schema（OpenAPI）" value={data.openapi_url} />
              <CopyRow label="授权 URL" value={data.authorize_url} />
              <CopyRow label="令牌 URL" value={data.token_url} />
              <Field label="GPT 说明（Instructions）"><textarea className="input h-28 text-base sm:text-xs" readOnly value={data.instructions} /></Field>
            </div>
          )}
          <div>
            <div className="mb-2 text-sm font-semibold">已连接（{data.connections.length}）</div>
            {data.connections.map((c: any) => (
              <div key={c.client_id} className="flex flex-wrap items-center gap-2 rounded-lg border p-2 text-sm">
                <span className="font-medium">{c.client}</span>
                <Badge color={c.scope.includes('write') ? 'yellow' : 'gray'}>{c.scope.includes('write') ? '读取 + 写入' : '只读'}</Badge>
                <span className="text-xs text-slate-400">授权 {dt(c.connected_at)} · 最近使用 {c.last_used_at ? ago(c.last_used_at) : '—'}</span>
                <button className="btn-ghost btn-sm text-red-600 sm:ml-auto" onClick={() => revoke(c)}>撤销</button>
              </div>
            ))}
            {!data.connections.length && <div className="text-xs text-slate-400">还没有连接</div>}
          </div>
          <details className="text-sm">
            <summary className="cursor-pointer text-slate-500">助手可用的 {data.tools.length} 个工具</summary>
            <div className="mt-2 flex flex-wrap gap-1">{data.tools.map((t: any) => <span key={t.name} className="chip bg-slate-100 text-slate-600">{t.title} · {tiers[t.tier]}</span>)}</div>
          </details>
        </>
      )}
    </div>
  )
}

/** /u/:token — opened from the assistant on the phone to upload the ORIGINAL photo/PDF. No login needed; the link is scoped to one target and expires in 30 minutes. */
export function UploadByLink() {
  const { token } = useParams()
  const { data, error } = useLoad(() => get(`/api/upload-link/${token}`), [token])
  const [done, setDone] = useState(0)
  const extra = useCallback(() => ({}), [])
  const up = useUploader(extra, () => setDone((n) => n + 1), `/api/upload-link/${token}`)
  return (
    <div className="min-h-full bg-slate-50 p-4 pt-[max(1rem,env(safe-area-inset-top))]">
      <div className="card mx-auto max-w-md space-y-4 p-5">
        <div className="flex items-center gap-2"><div className="rounded-lg bg-brand-600 px-2 py-1 text-sm font-bold text-white">QQ</div><div className="font-semibold">上传原件</div></div>
        {error && <div className="rounded bg-red-50 p-3 text-sm text-red-700">{errMsg(error)}</div>}
        {data && (
          <>
            <dl className="kv text-sm">
              <dt>上传到</dt><dd className="font-medium">{data.target_title}</dd>
              <dt>用途</dt><dd>{data.purpose}</dd>
              <dt>上传人</dt><dd>{data.user}</dd>
              <dt>有效至</dt><dd>{dt(data.expires_at)}</dd>
            </dl>
            <PickButtons onFiles={up.add} />
            <UploadQueue queue={up.queue} onRetry={up.retry} />
            {done > 0 && <div className="rounded bg-emerald-50 p-3 text-sm text-emerald-700">已上传 {done} 个文件。它们是「未核验」证据，需由他人核验。可以回到 ChatGPT 继续对话。</div>}
            <p className="text-xs text-slate-400">只能上传到上面这一项，30 分钟内有效，最多 20 个文件。</p>
          </>
        )}
      </div>
    </div>
  )
}
