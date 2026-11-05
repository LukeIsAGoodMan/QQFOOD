import { useParams, Link, Navigate, useLocation } from 'react-router-dom'
import { Pencil } from 'lucide-react'
import { get } from '../lib/api'
import { ErrorBox, Spinner, useLoad } from '../components/ui'
import { ModuleCard } from '../modules/registry'
import { pageIcon } from './Shell'

export function PageView() {
  const { id } = useParams()
  const { data, error, reload } = useLoad(() => get(`/api/pages/${id}`), [id])
  if (error) return <ErrorBox error={error} onRetry={reload} />
  if (!data) return <Spinner />
  const { page, layout } = data
  return (
    <div className="mx-auto max-w-7xl">
      <div className="mb-3 flex items-start gap-3 sm:mb-4">
        <div className="mt-0.5 hidden rounded-lg bg-brand-50 p-2 text-brand-700 sm:block">{pageIcon(page.icon, 22)}</div>
        <div className="min-w-0 flex-1">
          <h1 className="text-lg font-bold sm:text-xl">{page.name}</h1>
          {page.description && <p className="text-xs text-slate-500 sm:text-sm">{page.description}</p>}
        </div>
        {page.can_edit && (
          <Link to={`/p/${page.id}/edit`} className="btn-secondary shrink-0" aria-label="编辑页面"><Pencil size={16} /><span className="hidden sm:inline">编辑页面</span>{page.has_unpublished && <span className="h-2 w-2 rounded-full bg-amber-500" />}</Link>
        )}
      </div>
      {!layout ? (
        <div className="card p-8 text-center text-sm text-slate-500">此页面尚未发布。{page.can_edit && <Link className="ml-1 text-brand-700 underline" to={`/p/${page.id}/edit`}>去编辑</Link>}</div>
      ) : (
        <div className="grid grid-cols-6 gap-3 sm:gap-4">
          {layout.modules.filter((m: any) => !m.hidden).map((m: any) => <ModuleCard key={m.id} module={m} page={page} />)}
        </div>
      )}
    </div>
  )
}

/** Resolve well-known drill links (/quality, /inventory) to the page that hosts them. */
export function PageByName({ match }: { match: string }) {
  const loc = useLocation()
  const { data } = useLoad(() => get('/api/pages'), [])
  if (!data) return <Spinner />
  const p = data.find((x: any) => x.name.includes(match))
  return p ? <Navigate to={`/p/${p.id}${loc.search}`} replace /> : <div className="text-sm text-slate-500">未找到「{match}」页面</div>
}
