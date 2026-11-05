import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { AuthProvider, useAuth } from './lib/auth'
import { Spinner, ToastProvider } from './components/ui'
import { Shell } from './screens/Shell'
import { Login } from './screens/Login'
import { Home, EvidenceReview } from './screens/Home'
import { PageByName, PageView } from './screens/PageView'
import { PageEditor, PagesAdmin } from './screens/PageEditor'
import { EvidenceDetail, RecordDetail, TaskDetail, TasksList } from './screens/Details'
import { Notebook } from './screens/Notebook'
import { Ingest } from './screens/Ingest'
import { Audit, Me, Schema, Users } from './screens/Admin'
import { UploadByLink } from './screens/Agent'

function Routed() {
  const { me, loading } = useAuth()
  const loc = useLocation()
  // Original-file upload links from the AI assistant work without signing in (scoped, short-lived token)
  if (loc.pathname.startsWith('/u/')) return <Routes><Route path="/u/:token" element={<UploadByLink />} /></Routes>
  if (loading) return <Spinner />
  if (!me) return <Login />
  return (
    <Shell key={me.id + me.lang}>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/p/:id" element={<PageView />} />
        <Route path="/p/:id/edit" element={<PageEditor />} />
        <Route path="/r/:id" element={<RecordDetail />} />
        <Route path="/t/:id" element={<TaskDetail />} />
        <Route path="/tasks" element={<TasksList />} />
        <Route path="/evidence/:id" element={<EvidenceDetail />} />
        <Route path="/evidence-review" element={<EvidenceReview />} />
        <Route path="/notebook" element={<Notebook />} />
        <Route path="/ingest" element={<Ingest />} />
        <Route path="/quality" element={<PageByName match="质量" />} />
        <Route path="/inventory" element={<PageByName match="库存" />} />
        <Route path="/purchasing" element={<PageByName match="采购" />} />
        <Route path="/admin/pages" element={<PagesAdmin />} />
        <Route path="/admin/schema" element={<Schema />} />
        <Route path="/admin/users" element={<Users />} />
        <Route path="/admin/audit" element={<Audit />} />
        <Route path="/me" element={<Me />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <ToastProvider>
        <AuthProvider>
          <Routed />
        </AuthProvider>
      </ToastProvider>
    </BrowserRouter>
  )
}
