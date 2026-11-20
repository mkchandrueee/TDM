import { useContext, useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { Activity, Boxes, Building2, CheckCircle2, ChevronDown, Code2, Database, GitBranch, Gauge, Grid2X2, LockKeyhole, LogOut, Menu, Network, PauseCircle, PlayCircle, Send, Settings, ShieldCheck, Sparkles, Users, X, Ticket as TicketIcon } from 'lucide-react'
import { apiRequest, setDevelopmentSubject, type DevelopmentAccess, type HumanRole } from './api'
import LoginPage from './LoginPage'
import ConnectionsPage from './ConnectionsPage'
import DataSetsPage from './DataSetsPage'
import ViewDataPage from './ViewDataPage'
import ScenarioToDataPage from './ScenarioToDataPage'
import SourceToDataPage from './SourceToDataPage'
import PrivacyPage from './PrivacyPage'
import SelfServicingDataPage from './SelfServicingDataPage'
import ReservationsPage from './ReservationsPage'
import JobsPage from './JobsPage'
import TicketsPage from './TicketsPage'
import { DatasetUsageContext, type DatasetUsageStatus } from './DatasetActivity'
import { ConfirmDialog } from './components/ui'
import { ScenarioToDataLabel } from './ScenarioToDataLabel'

type Workspace = { id: string; slug: string; name: string; purpose: string | null; pii_masking_enabled: boolean; status: 'active' | 'inactive'; role?: HumanRole }
type Person = { subject: string; email: string | null; display_name: string | null; role: HumanRole; valid_from: string; valid_until: string | null; revoked_at: string | null }
type DirectoryUser = { subject: string; email: string | null; display_name: string | null }
type AccessConnection = { id: string; name: string; connector_type: string; operation_scope: 'read_only' | 'write_only' | 'read_write'; status: string }
type ActivityItem = { id: string; actor_id: string; actor_role: string | null; action: string; object_type: string; object_id: string | null; object_reference?: string | null; outcome: string; occurred_at: string | null; request_id: string }
type ActionRequest = { id: string; dataset_id: string; dataset_name: string | null; dataset_reference_code: string | null; requester_id: string; starts_at: string; ends_at: string; status: string; can_decide: boolean }
type ActiveReservation = { id: string; dataset_id: string; dataset_name: string | null; dataset_reference_code: string | null; starts_at: string; ends_at: string; status: string }
type PublishedDatasetRef = { id: string; name: string; reference_code: string }
type Metric = { available: boolean; count: number | null; active_count?: number; by_status?: Record<string, number> }
type DashboardData = { workspace_id: string | null; metrics: Record<string, Metric>; recent_activities: ActivityItem[] }
const DEVELOPMENT_SESSION_KEY = 'tigr-development-session'

function loadDevelopmentSession(): DevelopmentAccess | null {
  if (!import.meta.env.DEV) return null
  try {
    const saved = sessionStorage.getItem(DEVELOPMENT_SESSION_KEY)
    if (!saved) return null
    const session = JSON.parse(saved) as DevelopmentAccess
    if (!session.subject || !session.workspace_id || !['Admin','QA Engineer','Test Data Engineer'].includes(session.role)) return null
    return session
  } catch { return null }
}

const navigation = [
  { label: 'Dashboard', path: '/', icon: Gauge, group: 'Overview' },
  { label: 'Test Data Request', path: '/tickets', icon: TicketIcon, group: 'Operate' },
  { label: 'Data sets', path: '/datasets', icon: Database, group: 'Data management' },
  { label: 'View Data', path: '/view-data', icon: Grid2X2, group: 'Data management' },
  { label: <ScenarioToDataLabel/>, path: '/scenario-to-data', icon: Sparkles, group: 'Data management' },
  { label: 'Source to Data', path: '/source-to-data', icon: GitBranch, group: 'Data management' },
  { label: 'Self Servicing Data', path: '/self-servicing-data', icon: Code2, group: 'Data management' },
  { label: 'SQL to Data', path: '/sql-to-data', icon: Code2, group: 'Data management' },
  { label: 'PII & Masking', path: '/privacy', icon: ShieldCheck, group: 'Data management' },
  { label: 'Reservations & Locks', path: '/reservations', icon: LockKeyhole, group: 'Operate' },
  { label: 'Jobs', path: '/jobs', icon: Boxes, group: 'Operate' },
  { label: 'Connections', path: '/connections', icon: Network, group: 'Configure' },
  { label: 'Workspace', path: '/workspaces', icon: Building2, group: 'Settings' },
  { label: 'Access & Roles', path: '/access', icon: Users, group: 'Administration' },
  { label: 'Activities & Audit', path: '/activities', icon: Activity, group: 'Administration' },
]
const descriptions: Record<string, string> = {
  '/datasets': 'Browse published data sets, versions, access and lineage.',
  '/tickets': 'Raise and track test data requests through fulfillment.',
  '/view-data': 'View and copy published data set records available to your role.',
  '/scenario-to-data': 'Turn a test requirement into a reviewed data generation plan.',
  '/source-to-data': 'Build a test data set from an approved source.',
  '/self-servicing-data': 'Execute authorized SQL, inspect a sample, and publish your own dataset.',
  '/sql-to-data': 'Generate a governed data set from a validated PostgreSQL query.',
  '/connections': 'Configure and validate source and target connections.',
  '/privacy': 'Test Data Engineer workflow for privacy findings and masking policy coverage. Admins manage workspace settings and access.',
  '/reservations': 'Coordinate planned use and exclusive data locks.',
  '/jobs': 'Track background work and its execution steps.',
  '/workspaces': 'Create, activate, and remove workspaces.',
  '/access': 'Add workspace users with optional SSD connection permissions, and manage scoped grants.',
  '/activities': 'Review activity and audit evidence visible to your role.',
}

function App() {
  const [menuOpen, setMenuOpen] = useState(false)
  const [headerMenu, setHeaderMenu] = useState<'settings'|'profile'|null>(null)
  const [devSession, setDevSession] = useState<DevelopmentAccess|null>(loadDevelopmentSession)
  const [role, setRole] = useState<HumanRole>(devSession?.role ?? 'Test Data Engineer')
  const [devSubject, setDevSubject] = useState(devSession?.subject ?? '')
  const [workspaces, setWorkspaces] = useState<Workspace[]>([])
  const [workspaceId, setWorkspaceId] = useState('')
  const [identity, setIdentity] = useState<{ subject: string; display_name: string | null; platform_admin: boolean; workspace_admin: boolean; last_seen_at: string | null } | null>(null)
  const [dashboard, setDashboard] = useState<DashboardData | null>(null)
  const [datasetUsage, setDatasetUsage] = useState<Record<string, DatasetUsageStatus>>({})
  const [error, setError] = useState('')
  const location = useLocation()
  const current = navigation.find((item) => item.path === location.pathname) ?? navigation[0]
  const activeWorkspace = workspaces.find((workspace) => workspace.id === workspaceId)
  const piiMaskingEnabled = activeWorkspace?.pii_masking_enabled ?? true
  const isAdmin = (identity?.platform_admin ?? false) || (identity?.workspace_admin ?? false) || role === 'Admin'
  const visibleNavigation = navigation.filter((item) => !['/workspaces','/access','/activities'].includes(item.path) &&
    (piiMaskingEnabled || item.path !== '/privacy') && (item.path !== '/self-servicing-data' || role === 'QA Engineer') &&
    (role === 'QA Engineer' ? !['/scenario-to-data','/source-to-data','/sql-to-data'].includes(item.path) && item.group !== 'Configure' : role === 'Test Data Engineer' ? true : item.path !== '/view-data'))
  const administrationLinks = navigation.filter((item) => ['/workspaces','/access','/activities'].includes(item.path) && (!['/workspaces','/access'].includes(item.path) || isAdmin))
  const groups = [...new Set(visibleNavigation.map((item) => item.group))]

  async function refreshWorkspaces() {
    if (import.meta.env.DEV && !devSession) return
    const [who, rows] = await Promise.all([
      apiRequest<{ subject: string; display_name: string | null; platform_admin: boolean; last_seen_at: string | null; workspaces: Workspace[] }>('/me', role),
      apiRequest<Workspace[]>('/workspaces', role),
    ])
    setIdentity({ subject: who.subject, display_name: who.display_name, platform_admin: who.platform_admin, workspace_admin: who.workspaces.some((workspace) => workspace.role === 'Admin'), last_seen_at: who.last_seen_at })
    const roleByWorkspace = new Map(who.workspaces.map((workspace) => [workspace.id, workspace.role]))
    setWorkspaces(rows.map((workspace) => ({ ...workspace, role: roleByWorkspace.get(workspace.id) })))
    setWorkspaceId((selected) => rows.some((workspace) => workspace.id === selected) ? selected :
      (rows.some((workspace) => workspace.id === devSession?.workspace_id) ? devSession!.workspace_id : (who.workspaces[0]?.id ?? rows[0]?.id ?? '')))
  }

  function completeLogin(session:DevelopmentAccess) {
    sessionStorage.setItem(DEVELOPMENT_SESSION_KEY,JSON.stringify(session))
    setDevelopmentSubject(session.subject)
    setDevSubject(session.subject)
    setRole(session.role)
    setDevSession(session)
    setWorkspaceId(session.workspace_id)
  }

  function signOut() {
    sessionStorage.removeItem(DEVELOPMENT_SESSION_KEY)
    setDevSession(null)
    setDevSubject('')
    setDevelopmentSubject('')
    setRole('Test Data Engineer')
    setWorkspaces([]);setWorkspaceId('');setIdentity(null);setDashboard(null)
  }

  useEffect(() => {
    if (devSession) setDevelopmentSubject(devSession.subject)
  }, [devSession?.subject])

  useEffect(() => {
    if (import.meta.env.DEV && !devSession) return
    let active = true
    setError('')
    refreshWorkspaces().catch((cause: unknown) => {
      if (active) setError(cause instanceof Error ? cause.message : 'Unable to load workspace access')
    })
    return () => { active = false }
  }, [role, devSubject, devSession?.subject])

  useEffect(() => {
    setDatasetUsage({})
    if (!workspaceId || (import.meta.env.DEV && !devSession)) return
    let active = true
    const refreshUsage = () => apiRequest<DatasetUsageStatus[]>(`/workspaces/${workspaceId}/my-active-dataset-usage`, role)
      .then((rows) => {
        if (active) setDatasetUsage(Object.fromEntries(rows.map((row) => [row.dataset_id, row])))
      })
      .catch(() => {})
    void refreshUsage()
    const timer = window.setInterval(() => void refreshUsage(), 5000)
    return () => { active = false; window.clearInterval(timer) }
  }, [workspaceId, role, devSession?.subject, devSubject])

  useEffect(() => {
    if (import.meta.env.DEV && !devSession) return
    if (location.pathname !== '/') return
    let active = true
    setDashboard(null)
    apiRequest<DashboardData>(`/dashboard${workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : ''}`, role)
      .then((data) => { if (active) setDashboard(data) })
      .catch((cause: unknown) => { if (active) setError(cause instanceof Error ? cause.message : 'Unable to load the Dashboard') })
    return () => { active = false }
  }, [role, workspaceId, devSubject, devSession?.subject, location.pathname])

  if (import.meta.env.DEV && !devSession) return <LoginPage onLogin={completeLogin}/>

  return <div className="app-shell">
    <aside className={`sidebar ${menuOpen ? 'sidebar-open' : ''}`}>
      <div className="brand"><div className="brand-mark">T</div><div><strong>TIGR</strong><small>Test Data Intelligence, Generation and Readiness</small></div><button className="icon-button close-menu" onClick={() => setMenuOpen(false)} aria-label="Close navigation"><X size={19}/></button></div>
      <label className="workspace-picker"><span>ACTIVE WORKSPACE</span><select aria-label="Active workspace" value={workspaceId} onChange={(event) => {const nextId=event.target.value;setWorkspaceId(nextId);const nextRole=workspaces.find(workspace=>workspace.id===nextId)?.role;if(nextRole)setRole(nextRole)}} disabled={!workspaces.length}><option value="">{workspaces.length ? 'Select a workspace' : 'No workspace available'}</option>{workspaces.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name}{workspace.status === 'inactive' ? ' (inactive)' : ''}</option>)}</select><ChevronDown size={15}/></label>
      <nav aria-label="Main navigation">{groups.map((group) => <div className="nav-group" key={group}><p>{group}</p>{visibleNavigation.filter((item) => item.group === group).map(({label,path,icon:Icon}) => <NavLink key={path} to={path} end={path === '/'} onClick={() => setMenuOpen(false)} className={({isActive}) => `nav-link ${isActive ? 'active' : ''}`}><Icon size={18}/><span>{label}</span></NavLink>)}</div>)}</nav>
      <div className="sidebar-bottom"><div className="user-card"><div className="avatar">{initials(identity?.display_name ?? devSession?.display_name ?? 'User')}</div><div className="user-text"><strong>{identity?.display_name || devSession?.display_name || 'User'}</strong><small>{role}</small><small>Last logged in · {formatTimestamp(devSession?.last_seen_at ?? identity?.last_seen_at)}</small></div></div></div>
    </aside>
    {menuOpen && <button aria-label="Close navigation" className="scrim" onClick={() => setMenuOpen(false)} />}
    <main className="main-area">
      <header className="topbar">
        <button className="icon-button menu-button" onClick={() => setMenuOpen(true)} aria-label="Open navigation"><Menu size={21}/></button>
        <div className="breadcrumb"><span>{workspaces.find((workspace) => workspace.id === workspaceId)?.name ?? 'TIGR'}</span><b>/</b><strong>{current.label}</strong></div>
        <div className="top-actions">
          {isAdmin && <div className="header-menu-wrap">
            <button className="icon-button header-menu-button" aria-label="Settings and administration" aria-expanded={headerMenu === 'settings'} onClick={() => setHeaderMenu(headerMenu === 'settings' ? null : 'settings')}><Settings size={19}/></button>
            {headerMenu === 'settings' && <div className="header-popover"><strong>Settings</strong>{administrationLinks.map(({label,path,icon:Icon}) => <NavLink key={path} to={path} onClick={() => setHeaderMenu(null)}><Icon size={16}/><span>{label}</span></NavLink>)}</div>}
          </div>}
          <div className="header-menu-wrap">
            <button className="avatar top-avatar" aria-label="Account options" aria-expanded={headerMenu === 'profile'} onClick={() => setHeaderMenu(headerMenu === 'profile' ? null : 'profile')}>{initials(identity?.display_name ?? devSession?.display_name ?? 'User')}</button>
            {headerMenu === 'profile' && <div className="header-popover profile-popover"><strong>{identity?.display_name ?? devSession?.display_name ?? 'User'}</strong>{import.meta.env.DEV && <button className="profile-signout" onClick={() => {setHeaderMenu(null);signOut()}}><LogOut size={15}/>Sign out</button>}</div>}
          </div>
        </div>
      </header>
      <div className={`content${location.pathname === '/self-servicing-data' ? ' content-ssd' : ''}`}><div className="page-heading"><div><h1>{current.label}</h1><p>{location.pathname === '/' ? 'Your TIGR workspace at a glance.' : descriptions[location.pathname]}</p></div>{location.pathname === '/' && <div className="phase-pill"><span/>{workspaces.find((workspace) => workspace.id === workspaceId)?.name ?? 'All workspaces'}</div>}</div>
        {error && <div className="alert alert-warning" role="status">{error}</div>}
        <DatasetUsageContext.Provider value={datasetUsage}><Routes>
          <Route path="/" element={<Dashboard key={`${workspaceId}:${role}:${devSubject}`} data={dashboard?.workspace_id === (workspaceId || null) ? dashboard : null} workspaceId={workspaceId} role={role} displayName={identity?.display_name ?? devSession?.display_name}/>}/>
          <Route path="/tickets" element={<TicketsPage key={`${workspaceId}:${role}:${devSubject}`} role={role} workspaceId={workspaceId} workspaceName={workspaces.find((workspace) => workspace.id === workspaceId)?.name ?? ''}/>}/>
          <Route path="/workspaces" element={isAdmin ? <WorkspacePage role={role} workspaces={workspaces} onRefresh={refreshWorkspaces}/> : <Navigate to="/" replace/>}/>
          <Route path="/access" element={isAdmin ? <AccessPage role={role} workspaceId={workspaceId} workspaces={workspaces} platformAdmin={identity?.platform_admin ?? false} onWorkspaceChange={(nextId)=>{setWorkspaceId(nextId);const nextRole=workspaces.find((workspace)=>workspace.id===nextId)?.role;if(nextRole)setRole(nextRole)}} onRefresh={refreshWorkspaces}/> : <Navigate to="/" replace/>}/>
          <Route path="/activities" element={<ActivitiesPage key={`${workspaceId}:${role}:${devSubject}`} role={role} workspaceId={workspaceId}/>}/>
          <Route path="/connections" element={<ConnectionsPage key={`${workspaceId}:${role}:${devSubject}`} role={role} workspaceId={workspaceId} ownerName={identity?.subject ?? devSession?.subject ?? ''}/>}/>
          <Route path="/datasets" element={<DataSetsPage key={`${workspaceId}:${role}:${devSubject}`} role={role} workspaceId={workspaceId} piiMaskingEnabled={piiMaskingEnabled}/>}/>
          <Route path="/view-data" element={role === 'QA Engineer' || role === 'Test Data Engineer' ? <ViewDataPage key={`${workspaceId}:${role}:${devSubject}`} role={role} workspaceId={workspaceId}/> : <Navigate to="/" replace/>}/>
          <Route path="/scenario-to-data" element={<ScenarioToDataPage key={`${workspaceId}:${role}:${devSubject}`} role={role} workspaceId={workspaceId} piiMaskingEnabled={piiMaskingEnabled}/>}/>
          <Route path="/source-to-data" element={<SourceToDataPage key={`${workspaceId}:${role}:${devSubject}:${location.key}`} role={role} workspaceId={workspaceId} piiMaskingEnabled={piiMaskingEnabled}/>}/>
          <Route path="/self-servicing-data" element={role === 'QA Engineer' ? <SelfServicingDataPage key={`${workspaceId}:${role}:${devSubject}`} role={role} workspaceId={workspaceId} piiMaskingEnabled={piiMaskingEnabled}/> : <Navigate to="/" replace/>}/>
          <Route path="/sql-to-data" element={<SourceToDataPage key={`${workspaceId}:${role}:${devSubject}:${location.key}`} role={role} workspaceId={workspaceId} piiMaskingEnabled={piiMaskingEnabled} workflow="sql"/>}/>
          <Route path="/privacy" element={piiMaskingEnabled?<PrivacyPage key={`${workspaceId}:${role}:${devSubject}`} role={role} workspaceId={workspaceId}/>:<Navigate to="/datasets" replace/>}/>
          <Route path="/reservations" element={<ReservationsPage key={`${workspaceId}:${role}:${devSubject}`} role={role} workspaceId={workspaceId}/>}/>
          <Route path="/jobs" element={<JobsPage key={`${workspaceId}:${role}:${devSubject}`} role={role} workspaceId={workspaceId}/>}/>
          <Route path="*" element={<Navigate to="/" replace/>}/>
        </Routes></DatasetUsageContext.Provider>
        <footer><span>TIGR · MVP1</span><span>{import.meta.env.DEV ? 'Development environment' : 'Authenticated environment'} <i/></span></footer>
      </div>
    </main>
  </div>
}

function Dashboard({data, workspaceId, role, displayName}:{data:DashboardData|null;workspaceId:string;role:HumanRole;displayName?:string|null}) {
  const metrics = data?.metrics
  const usageStatuses = useContext(DatasetUsageContext)
  const [actionRequests, setActionRequests] = useState<ActionRequest[]>([])
  const [requestsLoading, setRequestsLoading] = useState(false)
  const [activeReservations, setActiveReservations] = useState<ActiveReservation[]>([])
  const [activeReservationsLoading, setActiveReservationsLoading] = useState(false)
  const [publishedDatasets, setPublishedDatasets] = useState<PublishedDatasetRef[]>([])
  const [lockDatasetListLoading, setLockDatasetListLoading] = useState(false)
  const [ticketReport, setTicketReport] = useState<{items:{id:string;display_id:string;title:string|null;status:string;current_age_seconds:number|null}[];counts:Record<string,number>;median_closure_seconds:number|null;overdue_needed_by_count:number;on_hold_duration_seconds:number}|null>(null)
  useEffect(()=>{
    if(!workspaceId){setTicketReport(null);return}
    let active=true
    apiRequest<typeof ticketReport>(`/reports/tickets?workspace_id=${encodeURIComponent(workspaceId)}`,role)
      .then(result=>{if(active)setTicketReport(result)})
      .catch(()=>{if(active)setTicketReport(null)})
    return()=>{active=false}
  },[workspaceId,role])
  useEffect(() => {
    if (!workspaceId) {
      setActionRequests([])
      setRequestsLoading(false)
      return
    }
    let active = true
    setActionRequests([])
    setRequestsLoading(true)
    apiRequest<ActionRequest[]>(`/workspaces/${workspaceId}/data-requests`, role)
      .then((rows) => { if (active) setActionRequests(rows.filter((request) => request.status === 'pending' && (role === 'QA Engineer' || request.can_decide))) })
      .catch(() => { if (active) setActionRequests([]) })
      .finally(() => { if (active) setRequestsLoading(false) })
    return () => { active = false }
  }, [workspaceId, role])
  useEffect(() => {
    if (!workspaceId || role !== 'QA Engineer') {
      setPublishedDatasets([])
      setLockDatasetListLoading(false)
      return
    }
    let active = true
    setPublishedDatasets([])
    setLockDatasetListLoading(true)
    apiRequest<{items:PublishedDatasetRef[]}>(`/datasets?workspace_id=${encodeURIComponent(workspaceId)}&lifecycle_state=published&page_size=100`, role)
      .then((result) => { if (active) setPublishedDatasets(result.items) })
      .catch(() => { if (active) setPublishedDatasets([]) })
      .finally(() => { if (active) setLockDatasetListLoading(false) })
    return () => { active = false }
  }, [workspaceId, role])
  const lockedDatasets = publishedDatasets.filter((dataset) => usageStatuses[dataset.id]?.lock_active)
  useEffect(() => {
    if (!workspaceId || role !== 'QA Engineer') {
      setActiveReservations([])
      setActiveReservationsLoading(false)
      return
    }
    let active = true
    setActiveReservations([])
    setActiveReservationsLoading(true)
    apiRequest<ActiveReservation[]>(`/workspaces/${workspaceId}/reservations?include_closed=false`, role)
      .then((rows) => { if (active) setActiveReservations(rows.filter((reservation) => reservation.status === 'active')) })
      .catch(() => { if (active) setActiveReservations([]) })
      .finally(() => { if (active) setActiveReservationsLoading(false) })
    return () => { active = false }
  }, [workspaceId, role])
  const canReviewRequests = role === 'Admin' || role === 'Test Data Engineer'
  const allCards = [
    {key:'activities', label:'Visible Activities', icon:Activity, tone:'purple'},
    {key:'datasets', label:'Data Sets', icon:Database, tone:'cyan'},
    {key:'jobs', label:'Jobs', icon:Boxes, tone:'orange'},
    {key:'reservations', label:'Reservations', icon:LockKeyhole, tone:'yellow'},
    {key:'locks', label:'Locks', icon:LockKeyhole, tone:'purple'},
    {key:'connections', label:'Source connections', icon:Network, tone:'cyan'},
  ]
  const cards = role === 'QA Engineer' ? allCards.filter(({key}) => ['activities','datasets','reservations','locks'].includes(key)) : allCards
  return <>
    <section className="welcome-card"><div className="welcome-copy"><div className="welcome-kicker"><Sparkles size={15}/> YOUR TIGR WORKSPACE</div><h2>{displayName ? `Welcome, ${displayName}!` : 'Welcome!'}</h2><p>Review the data sets, jobs, and activity available to you. Select a workspace to focus on its latest work.</p><div className="welcome-actions"><Link to="/scenario-to-data" className="button button-primary">Explore scenario</Link><Link to="/source-to-data" className="button button-light">Create from source <span>→</span></Link></div></div><div className="welcome-art"><div className="art-ring ring-one"/><div className="art-ring ring-two"/><div className="art-orb"><Database size={40}/></div><div className="art-chip chip-a"><ShieldCheck size={15}/> Access scoped</div><div className="art-chip chip-b"><GitBranch size={15}/> Auditable</div><div className="art-dot dot-a"/><div className="art-dot dot-b"/></div></section>
    <div className="section-title"><div><h2>Workspace snapshot</h2><p>Summary for the selected access scope.</p></div><span className="updated">{workspaceId ? 'Current workspace' : 'All visible activity'}</span></div>
    <section className={`stat-grid${role==='QA Engineer'?' stat-grid-qa':''}`}>{cards.map(({key,label,icon:Icon,tone})=>{const metric=metrics?.[key];const pendingCount=metrics?.reservation_requests?.available?metrics.reservation_requests.by_status?.pending??0:'—';const activeCount=metric?.active_count??metric?.by_status?.active??'—';const detail=role==='QA Engineer'&&key==='reservations'?<><span>Created</span><span>Pending Approval: {pendingCount} · Currently Active: {activeCount}</span></>:role==='QA Engineer'&&key==='locks'?<><span>Created</span><span>Currently In use: {activeCount}</span></>:role==='QA Engineer'&&key==='datasets'?'Published':role!=='QA Engineer'&&key==='reservations'?`Across data sets · Current Active: ${activeCount}`:role!=='QA Engineer'&&key==='locks'?`Across data sets · In use: ${activeCount}`:metric?.available ? (key === 'jobs' || key === 'connections' || key === 'datasets' ? Object.entries(metric.by_status ?? {}).map(([status,count])=>`${status}: ${count}`).join(' · ') || (key==='jobs'?'No recorded jobs':key==='datasets'?'No visible data sets':'No configured connections') : 'Authorized records only') : 'Available in a later workflow phase';return <article className="stat-card" key={key}><div className={`stat-icon ${tone}`}><Icon size={19}/></div><span>{label}</span><strong>{metric?.available ? metric.count ?? '—' : '—'}</strong><small>{detail}</small></article>})}</section>
    <div className={`lower-grid${role==='QA Engineer'?' dashboard-qa-grid':''}`}><section className="panel activity-panel dashboard-recent-panel"><div className="panel-heading"><div><h2>Recent activities</h2><p>Filtered before listing and totals</p></div><Link to="/activities">View activity <span>→</span></Link></div><ActivityList items={data?.recent_activities ?? []}/></section>{canReviewRequests?<section className="panel quick-panel"><div className="panel-heading"><div><h2>Requests awaiting your action</h2><p>Pending reservation approvals</p></div><Link to="/reservations">Review requests <span>→</span></Link></div>{requestsLoading?<div className="empty-state"><p>Loading pending requests…</p></div>:actionRequests.length?<div className="activity-list">{actionRequests.slice(0,4).map((request)=><Link className="activity-row" to="/reservations" key={request.id}><span className="activity-dot"/><div><strong>{request.dataset_name ?? request.dataset_reference_code ?? 'Data set reservation'}</strong><small>{request.dataset_reference_code ?? 'Data set reference unavailable'} · Requested by {request.requester_id}</small></div><time>{new Date(request.starts_at).toLocaleDateString()}</time></Link>)}</div>:<div className="empty-state"><div className="empty-icon"><Users size={20}/></div><strong>No pending requests</strong><p>Reservation requests needing approval will appear here.</p></div>}</section>:role==='QA Engineer'?<><section className="panel quick-panel dashboard-reservations-panel"><div className="panel-heading"><div><h2>Active reservations</h2><p>Reservation windows currently in progress</p></div><Link to="/reservations">View reservations <span>→</span></Link></div>{activeReservationsLoading?<div className="empty-state"><p>Loading active reservations…</p></div>:activeReservations.length?<div className="activity-list">{activeReservations.slice(0,4).map((reservation)=><Link className="activity-row dashboard-reservation-row" to="/reservations" key={reservation.id}><span className="activity-dot"/><div><strong>{reservation.dataset_name ?? 'Data set reservation'}</strong><small>{reservation.dataset_reference_code ?? 'Data set reference unavailable'} · Ends {new Date(reservation.ends_at).toLocaleString()}</small></div></Link>)}</div>:<div className="empty-state"><div className="empty-icon"><LockKeyhole size={20}/></div><strong>No active reservations</strong><p>Approved reservation windows in progress will appear here.</p></div>}</section><section className="panel quick-panel dashboard-locks-panel"><div className="panel-heading"><div><h2>Locks in use</h2><p>Your active lock leases</p></div><Link to="/view-data">View Data <span>→</span></Link></div>{lockDatasetListLoading?<div className="empty-state"><p>Loading active locks…</p></div>:lockedDatasets.length?<div className="activity-list">{lockedDatasets.slice(0,4).map((dataset)=><Link className="activity-row live-lock-row" to="/view-data" key={dataset.id}><span className="activity-dot"/><div><strong>{dataset.name}</strong><small>{dataset.reference_code}</small></div><time>In use</time></Link>)}</div>:<div className="empty-state"><div className="empty-icon"><LockKeyhole size={20}/></div><strong>No locks in use</strong><p>Data sets with your active lock leases will appear here.</p></div>}</section></>:<section className="panel quick-panel"><div className="panel-heading"><div><h2>Access context</h2><p>Current role and workspace scope</p></div></div><div className="context-card"><span className="context-icon"><Users size={17}/></span><div><strong>{role}</strong><small>{workspaceId ? 'Scoped to selected workspace' : 'Scoped to your available workspaces'}</small></div></div></section>}</div>
    <section className="panel ticket-dashboard-card"><div className="panel-heading"><div><h2>{role==='QA Engineer'?'My Test Data Tickets':'Ticket Queue'}</h2><p>Current status and elapsed open age</p></div><Link to="/tickets">Open ticket management <span>→</span></Link></div><div className="ticket-dashboard-counts">{[{label:'Submitted',icon:Send},{label:'In Progress',icon:PlayCircle},{label:'On Hold',icon:PauseCircle},{label:'Closed',icon:CheckCircle2}].map(({label:s,icon:StatusIcon})=><div className={`ticket-dashboard-status status-${s.toLowerCase().replaceAll(' ','-')}`} key={s}><span><StatusIcon aria-hidden="true"/>{s}</span><strong>{ticketReport?.counts[s]??'—'}</strong></div>)}</div>{ticketReport&&<div className="ticket-dashboard-extra"><span>Overdue to needed-by: <b>{ticketReport.overdue_needed_by_count}</b></span><span>On Hold time: <b>{Math.floor(ticketReport.on_hold_duration_seconds/3600)}h</b></span><span>Median closure: <b>{ticketReport.median_closure_seconds==null?'—':`${Math.floor(ticketReport.median_closure_seconds/3600)}h`}</b></span></div>}{ticketReport?.items?.length?<div className="activity-list">{ticketReport.items.slice(0,4).map(t=><Link className="activity-row" to="/tickets" key={t.id}><span className="activity-dot"/><div><strong>{t.display_id} · {t.title||'Untitled request'}</strong><small>{t.status}</small></div><time>{t.current_age_seconds==null?'Closed':`${Math.floor(t.current_age_seconds/86400)}d ${Math.floor(t.current_age_seconds%86400/3600)}h`}</time></Link>)}</div>:<div className="empty-state"><p>{ticketReport?'No tickets in this workspace yet.':'Ticket metrics are unavailable.'}</p></div>}</section>
    <div className="foundation-note"><span><ShieldCheck size={17}/></span><p><strong>Workspace access controls</strong> · Connection counts include only the selected or otherwise authorized workspace scope.</p></div>
  </>
}

function ActivityList({items}:{items:ActivityItem[]}) {
  if (!items.length) return <div className="empty-state"><div className="empty-icon"><Activity size={20}/></div><strong>No visible activities</strong><p>Authorized activity will appear here.</p></div>
  return <div className="activity-list">{items.map((item)=>{const reference=item.object_reference?`${item.object_type==='reservation'?'reservation · ':''}${item.object_reference}`:`${item.object_type}${item.object_id?` · ${item.object_id.slice(0,10)}`:''}`;const timestamp=item.occurred_at?new Date(item.occurred_at).toLocaleString():'';return <div className="activity-row" key={item.id}><span className="activity-dot"/><div><strong>{humanize(item.action)}</strong><small>{reference}{timestamp?`  ${timestamp}`:''}</small></div></div>})}</div>
}

function ActivitiesPage({role,workspaceId}:{role:HumanRole;workspaceId:string}) {
  const [items,setItems]=useState<ActivityItem[]>([])
  const [page,setPage]=useState(1)
  const [pageSize]=useState(20)
  const [total,setTotal]=useState(0)
  const [hasMore,setHasMore]=useState(false)
  const [search,setSearch]=useState('')
  const [query,setQuery]=useState('')
  const [error,setError]=useState('')
  const [loading,setLoading]=useState(false)
  useEffect(()=>{
    let active=true
    setLoading(true);setItems([]);setError('')
    const params=new URLSearchParams({page:String(page),page_size:String(pageSize)})
    if(workspaceId)params.set('workspace_id',workspaceId)
    if(query.trim())params.set('search',query.trim())
    apiRequest<{items:ActivityItem[];total:number;has_more:boolean}>(`/activities?${params}`,role)
      .then((result)=>{if(active){setItems(result.items);setTotal(result.total);setHasMore(result.has_more)}})
      .catch((cause:unknown)=>{if(active)setError(cause instanceof Error?cause.message:'Unable to load activity')})
      .finally(()=>{if(active)setLoading(false)})
    return ()=>{active=false}
  },[role,workspaceId,page,pageSize,query])
  return <section className="panel audit-page">
    <div className="panel-heading">
      <div><h2>Visible activity</h2><p>Activity is filtered by your role and workspace before search and pagination.</p></div>
      <span className="updated audit-event-count">{total} authorized events</span>
    </div>
    <form className="activity-search" onSubmit={(event)=>{event.preventDefault();setPage(1);setQuery(search)}}>
      <input aria-label="Search visible activity" value={search} onChange={(event)=>setSearch(event.target.value)} placeholder="Search actions, roles or object references"/>
      <button type="submit" className="button button-confirm">Search</button>
    </form>
    {error&&<div className="alert alert-warning">{error}</div>}
    <div className="table-shell">
      <table>
        <thead><tr><th>Action</th><th>Data set ID</th><th>Actor role</th><th>Object</th><th>Outcome</th><th>Time</th><th>Request</th></tr></thead>
        <tbody>
          {items.map((item)=><tr key={item.id}><td>{humanize(item.action)}</td><td>{item.object_reference??'—'}</td><td>{item.actor_role??'Service'}</td><td>{item.object_type}{item.object_id?` · ${item.object_id}`:''}</td><td><span className={`status-badge ${item.outcome==='succeeded'?'status-success':'status-warning'}`}><i/>{item.outcome}</span></td><td>{item.occurred_at?new Date(item.occurred_at).toLocaleString():''}</td><td>{item.request_id.slice(0,12)}</td></tr>)}
          {!loading&&!items.length&&<tr><td colSpan={7} className="table-empty">No visible activity matches this search.</td></tr>}
          {loading&&<tr><td colSpan={7} className="table-empty">Loading authorized activity…</td></tr>}
        </tbody>
      </table>
    </div>
    <nav className="pagination audit-pagination" aria-label="Activity pages">
      <span className="audit-page-count">Page {page} of {Math.max(1,Math.ceil(total/pageSize))}</span>
      <div className="audit-pagination-actions">
        <button type="button" className="button button-secondary compact" disabled={page<=1||loading} onClick={()=>setPage((value)=>value-1)}>Previous</button>
        <button type="button" className="button button-secondary compact" disabled={!hasMore||loading} onClick={()=>setPage((value)=>value+1)}>Next</button>
      </div>
    </nav>
  </section>
}

function WorkspacePage({role,workspaces,onRefresh}:{role:HumanRole;workspaces:Workspace[];onRefresh:()=>Promise<void>}) {
  const [message,setMessage]=useState('')
  const [busy,setBusy]=useState(false)
  const [slug,setSlug]=useState('')
  const [name,setName]=useState('')
  const [purpose,setPurpose]=useState('')
  const [piiMaskingEnabled,setPiiMaskingEnabled]=useState(true)
  const [editingWorkspace,setEditingWorkspace]=useState<Workspace|null>(null)
  const [workspaceToDelete,setWorkspaceToDelete]=useState<Workspace|null>(null)

  function clearWorkspaceForm(){setEditingWorkspace(null);setSlug('');setName('');setPurpose('');setPiiMaskingEnabled(true)}
  function editWorkspace(workspace:Workspace){setEditingWorkspace(workspace);setSlug(workspace.slug);setName(workspace.name);setPurpose(workspace.purpose??'');setPiiMaskingEnabled(workspace.pii_masking_enabled)}

  async function createWorkspace(event:FormEvent<HTMLFormElement>) {
    event.preventDefault();setBusy(true);setMessage('')
    try {
      const body=JSON.stringify({slug,name,purpose:purpose||null,pii_masking_enabled:piiMaskingEnabled})
      if(editingWorkspace)await apiRequest(`/workspaces/${editingWorkspace.id}`,role,{method:'PATCH',body})
      else await apiRequest('/workspaces',role,{method:'POST',body})
      clearWorkspaceForm();await onRefresh();setMessage(editingWorkspace?'Workspace updated.':'Workspace created.')
    } catch(cause) { setMessage(cause instanceof Error?cause.message:editingWorkspace?'Unable to update workspace':'Unable to create workspace') }
    finally { setBusy(false) }
  }
  async function setWorkspaceStatus(workspace:Workspace) {
    setBusy(true);setMessage('')
    try {
      await apiRequest(`/workspaces/${workspace.id}`,role,{method:'PATCH',body:JSON.stringify({status:workspace.status==='active'?'inactive':'active'})})
      await onRefresh();setMessage(`Workspace ${workspace.status==='active'?'deactivated':'activated'}.`)
    } catch(cause) { setMessage(cause instanceof Error?cause.message:'Unable to change workspace status') }
    finally { setBusy(false) }
  }
  async function deleteWorkspace() {
    if(!workspaceToDelete||busy)return
    setBusy(true);setMessage('')
    try {
      await apiRequest(`/workspaces/${workspaceToDelete.id}`,role,{method:'DELETE'})
      const deletedName=workspaceToDelete.name;setWorkspaceToDelete(null);await onRefresh();setMessage(`${deletedName} deleted.`)
    } catch(cause) { setMessage(cause instanceof Error?cause.message:'Unable to delete workspace') }
    finally { setBusy(false) }
  }

  return <div className="access-page">
    {message&&<div className="alert alert-info" role="status">{message}</div>}
    <section className="panel access-workspace-panel"><div className="panel-heading"><div><h2>Workspaces</h2><p>Create, activate or deactivate, and delete workspace containers.</p></div></div>
      <div className="workspace-admin-list">{workspaces.map((workspace)=><div className="workspace-admin-row" key={workspace.id}><span className="workspace-row-icon"><Building2 size={15}/></span><div><strong>{workspace.name}</strong><small>{workspace.slug} · {workspace.purpose||'No purpose recorded'}</small></div><span className={`status-badge ${workspace.status==='active'?'status-success':'status-neutral'}`}><i/>{workspace.status}</span><span className={`status-badge ${workspace.pii_masking_enabled?'status-warning':'status-neutral'}`}>{workspace.pii_masking_enabled?'PII & Masking on':'PII & Masking off'}</span><button type="button" className="button button-secondary compact" disabled={busy} onClick={()=>editWorkspace(workspace)}>Edit</button><button className="button button-secondary compact" disabled={busy} onClick={()=>void setWorkspaceStatus(workspace)}>{workspace.status==='active'?'Deactivate':'Activate'}</button><button className="button button-danger compact" disabled={busy} onClick={()=>setWorkspaceToDelete(workspace)}>Delete</button></div>)}{!workspaces.length&&<p className="quiet-message">No workspaces have been created.</p>}</div>
      <form className="access-form workspace-form" onSubmit={createWorkspace}><h3>{editingWorkspace?`Edit ${editingWorkspace.name}`:'Create workspace'}</h3><div className="form-grid"><label className="form-field">Workspace name<input required minLength={2} maxLength={180} value={name} onChange={(event)=>setName(event.target.value)} placeholder="e.g. Quality assurance"/></label><label className="form-field">Identifier<input required minLength={2} pattern="[a-z0-9]+(-[a-z0-9]+)*" maxLength={80} value={slug} onChange={(event)=>setSlug(event.target.value.toLowerCase())} placeholder="e.g. quality-assurance"/><small>Lowercase letters, digits and hyphens</small></label><label className="form-field span-two">Purpose<input maxLength={2000} value={purpose} onChange={(event)=>setPurpose(event.target.value)} placeholder="Describe this workspace"/></label><fieldset className="form-field span-two workspace-pii-setting"><legend>PII &amp; Masking</legend><div className="workspace-pii-options"><label className={piiMaskingEnabled?'selected':''}><input type="radio" name="workspace-pii-masking" checked={piiMaskingEnabled} onChange={()=>setPiiMaskingEnabled(true)}/><span>With PII &amp; Masking</span></label><label className={!piiMaskingEnabled?'selected':''}><input type="radio" name="workspace-pii-masking" checked={!piiMaskingEnabled} onChange={()=>setPiiMaskingEnabled(false)}/><span>Without PII &amp; Masking</span></label></div><small>{piiMaskingEnabled?'Generated source-backed data follows the current PII review and masking workflow.':'PII discovery and masking are skipped for this workspace; validated data can proceed directly to publication.'} This setting applies to new generation runs.</small></fieldset></div><div className="access-form-actions"><button className="button button-confirm" disabled={busy}>{editingWorkspace?'Save workspace changes':'Create workspace'}</button>{editingWorkspace&&<button type="button" className="button button-secondary" disabled={busy} onClick={clearWorkspaceForm}>Cancel</button>}</div></form>
    </section>
    {workspaceToDelete&&<ConfirmDialog title="Delete workspace?" confirmLabel="Delete workspace" destructive onClose={()=>setWorkspaceToDelete(null)} onConfirm={()=>void deleteWorkspace()}><p>Delete <strong>{workspaceToDelete.name}</strong>? Workspaces with data sets, connections, or jobs cannot be deleted. User accounts and audit history are preserved.</p></ConfirmDialog>}
  </div>
}

function AccessPage({role,workspaceId,workspaces,platformAdmin,onWorkspaceChange,onRefresh}:{role:HumanRole;workspaceId:string;workspaces:Workspace[];platformAdmin:boolean;onWorkspaceChange:(workspaceId:string)=>void;onRefresh:()=>Promise<void>}) {
  const [members,setMembers]=useState<Person[]>([])
  const [grants,setGrants]=useState<Array<{id:string;subject:string;scope_type:string;scope_id:string|null;permissions:string[];valid_until:string|null;revoked_at:string|null}>>([])
  const [directoryUsers,setDirectoryUsers]=useState<DirectoryUser[]>([])
  const [ssdConnections,setSsdConnections]=useState<AccessConnection[]>([])
  const [ssdConnectionsOpen,setSsdConnectionsOpen]=useState(false)
  const ssdConnectionLovRef=useRef<HTMLDivElement>(null)
  const [accessWorkspaceId,setAccessWorkspaceId]=useState('')
  const [loadingAccess,setLoadingAccess]=useState(false)
  const accessRequestId=useRef(0)
  const [message,setMessage]=useState('')
  const [busy,setBusy]=useState(false)
  const [memberEntryMode,setMemberEntryMode]=useState<'existing'|'new'>('existing')
  const [memberSubject,setMemberSubject]=useState('')
  const [memberName,setMemberName]=useState('')
  const [memberEmail,setMemberEmail]=useState('')
  const [memberRole,setMemberRole]=useState<HumanRole>('QA Engineer')
  const [editingMemberSubject,setEditingMemberSubject]=useState<string|null>(null)
  const [memberValidFrom,setMemberValidFrom]=useState(()=>toDateTimeInput(new Date()))
  const [memberValidUntil,setMemberValidUntil]=useState('')
  const [ssdConnectionIds,setSsdConnectionIds]=useState<string[]>([])
  const [ssdSelect,setSsdSelect]=useState(false)
  const [ssdModify,setSsdModify]=useState(false)
  const [ssdDelete,setSsdDelete]=useState(false)
  const [ssdValidUntil,setSsdValidUntil]=useState('')
  const [grantSubject,setGrantSubject]=useState('')
  const [grantValidUntil,setGrantValidUntil]=useState('')
  const [grantScope,setGrantScope]=useState('dataset')
  const [grantScopeId,setGrantScopeId]=useState('')
  const [permission,setPermission]=useState('reservation:read')
  const [grantReason,setGrantReason]=useState('')
  const selected=workspaces.find((workspace)=>workspace.id===workspaceId)
  const canManageSelectedWorkspace=platformAdmin||selected?.role==='Admin'
  const grantableMembers=members.filter((member)=>membershipStatus(member)==='active'&&(member.display_name||member.email))
  const namedDirectoryUsers=directoryUsers.filter((user)=>user.display_name||user.email)
  const selectableSsdConnections=ssdConnections.filter((connection)=>['postgresql','mysql','oracle','mssql'].includes(connection.connector_type))
  const selectedSsdConnections=selectableSsdConnections.filter((connection)=>ssdConnectionIds.includes(connection.id))
  const activeSsdGrants=(subject:string)=>grants.filter((grant)=>grant.subject===subject&&grant.scope_type==='connection'&&!grant.revoked_at&&(!grant.valid_until||Date.parse(grant.valid_until)>Date.now())&&grant.permissions.some((permission)=>permission.startsWith('ssd:')))
  const activeSsdPermissions=(subject:string)=>activeSsdGrants(subject).flatMap((grant)=>grant.permissions)
  const ssdConnectionAccess=(subject:string)=>Array.from(new Set(activeSsdGrants(subject).map((grant)=>grant.scope_id))).map((id)=>({id,name:ssdConnections.find((connection)=>connection.id===id)?.name??(id?`Unavailable connection (${id})`:'Unspecified connection')}))

  async function reload() {
    const requestId=++accessRequestId.current
    const targetWorkspaceId=workspaceId
    setAccessWorkspaceId('');setMembers([]);setGrants([]);setDirectoryUsers([]);setSsdConnections([])
    if(!targetWorkspaceId||!canManageSelectedWorkspace) { setLoadingAccess(false);return }
    setLoadingAccess(true)
    try {
      const [people,access,knownUsers,connections]=await Promise.all([
        apiRequest<Person[]>(`/workspaces/${targetWorkspaceId}/memberships?include_revoked=true`,role),
        apiRequest<typeof grants>(`/workspaces/${targetWorkspaceId}/grants?include_revoked=true`,role),
        apiRequest<DirectoryUser[]>('/users',role).catch(()=>[] as DirectoryUser[]),
        apiRequest<AccessConnection[]>(`/workspaces/${targetWorkspaceId}/connections?include_disabled=true`,role).catch(()=>[] as AccessConnection[]),
      ])
      if(requestId===accessRequestId.current) { setMembers(people);setGrants(access);setDirectoryUsers(knownUsers);setSsdConnections(connections);setAccessWorkspaceId(targetWorkspaceId);setMessage('') }
    } catch(cause) { if(requestId===accessRequestId.current)setMessage(cause instanceof Error?cause.message:'Unable to load access records') }
    finally { if(requestId===accessRequestId.current)setLoadingAccess(false) }
  }
  useEffect(()=>{void reload();clearMemberForm();setGrantSubject('');setGrantValidUntil('');setGrantScopeId('');setGrantReason('')},[workspaceId,role,platformAdmin,canManageSelectedWorkspace])
  useEffect(()=>{
    if(!ssdConnectionsOpen)return
    const closeOutside=(event:MouseEvent)=>{if(event.target instanceof Node&&!ssdConnectionLovRef.current?.contains(event.target))setSsdConnectionsOpen(false)}
    document.addEventListener('mousedown',closeOutside)
    return ()=>document.removeEventListener('mousedown',closeOutside)
  },[ssdConnectionsOpen])

  async function action(work:()=>Promise<unknown>): Promise<boolean> {
    setBusy(true);setMessage('')
    try { await work();await onRefresh();await reload();setMessage('Changes saved and recorded in the audit trail.');return true }
    catch(cause) { setMessage(cause instanceof Error?cause.message:'The request could not be completed');return false }
    finally { setBusy(false) }
  }

  function editMember(member:Person) {
    setEditingMemberSubject(member.subject);setMemberEntryMode('existing');setMemberSubject(member.subject);setMemberName(member.display_name??'');setMemberEmail(member.email??'');setMemberRole(member.role)
    setMemberValidFrom(toDateTimeInput(member.valid_from));setMemberValidUntil(member.valid_until?toDateTimeInput(member.valid_until):'')
  }
  function clearMemberForm() {
    setEditingMemberSubject(null);setMemberEntryMode('existing');setMemberSubject('');setMemberName('');setMemberEmail('');setMemberRole('QA Engineer')
    setMemberValidFrom(toDateTimeInput(new Date()));setMemberValidUntil('')
    setSsdConnectionIds([]);setSsdConnectionsOpen(false);setSsdSelect(false);setSsdModify(false);setSsdDelete(false);setSsdValidUntil('')
  }
  async function saveMembership(event:FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const editing=Boolean(editingMemberSubject)
    const permissions=[...(ssdSelect?['ssd:select']:[]),...(ssdModify?['ssd:insert','ssd:update']:[]),...(ssdDelete?['ssd:delete']:[])]
    if(permissions.length&&!ssdConnectionIds.length) { setMessage('Select at least one supported database connection for the SSD permissions.');return }
    setBusy(true);setMessage('')
    const url=editing
      ?`/workspaces/${workspaceId}/memberships/${encodeURIComponent(editingMemberSubject!)}`
      :`/workspaces/${workspaceId}/memberships`
    const matchedUser=namedDirectoryUsers.find((user)=>user.subject===memberSubject)
    const displayName=editing?memberName.trim():(matchedUser?.display_name||memberName.trim())
    const email=editing?undefined:(matchedUser?.email||(memberEntryMode==='new'?memberEmail.trim():undefined))
    let membershipSaved=false
    let grantedConnections=0
    try {
      await apiRequest(url,role,{method:editing?'PATCH':'POST',body:JSON.stringify({subject:memberSubject,display_name:displayName,...(email?{email}:{}),role:memberRole,valid_from:new Date(memberValidFrom).toISOString(),valid_until:memberValidUntil?new Date(memberValidUntil).toISOString():null})})
      membershipSaved=true
      const requestedGrantExpiry=ssdValidUntil?new Date(ssdValidUntil).toISOString():null
      if(permissions.length) for(const connectionId of ssdConnectionIds) {
        const expiryIsCovered=(grant:{valid_until:string|null})=>requestedGrantExpiry?(!grant.valid_until||Date.parse(grant.valid_until)>=Date.parse(requestedGrantExpiry)):!grant.valid_until
        const hasEquivalentGrant=grants.some((grant)=>grant.subject===memberSubject&&grant.scope_type==='connection'&&grant.scope_id===connectionId&&!grant.revoked_at&&(!grant.valid_until||Date.parse(grant.valid_until)>Date.now())&&expiryIsCovered(grant)&&permissions.every((permission)=>grant.permissions.includes(permission)))
        if(!hasEquivalentGrant) await apiRequest(`/workspaces/${workspaceId}/grants`,role,{method:'POST',body:JSON.stringify({subject:memberSubject,scope_type:'connection',scope_id:connectionId,permissions,valid_until:ssdValidUntil?new Date(ssdValidUntil).toISOString():null,reason:'Self Servicing Data connection operations'})})
        grantedConnections++
      }
      await onRefresh();await reload();clearMemberForm();setMessage(permissions.length?`${editing?'User updated':memberEntryMode==='new'?'New user created':'User added'} with SSD access on ${grantedConnections} connection${grantedConnections===1?'':'s'}.`:editing?'User membership updated.':memberEntryMode==='new'?'New user created and added to the workspace.':'User added to the workspace.')
    } catch(cause) {
      const detail=cause instanceof Error?cause.message:'The request could not be completed'
      if(membershipSaved) { await onRefresh();await reload();setMessage(`${editing?'Workspace membership was updated':'Workspace membership was saved'}; SSD access was granted on ${grantedConnections} of ${ssdConnectionIds.length} selected connections. Error: ${detail}`) }
      else setMessage(detail)
    } finally { setBusy(false) }
  }
  async function saveGrant(event:FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const saved=await action(()=>apiRequest(`/workspaces/${workspaceId}/grants`,role,{method:'POST',body:JSON.stringify({subject:grantSubject,scope_type:grantScope,scope_id:grantScope==='workspace'?null:grantScopeId,permissions:[permission],valid_until:grantValidUntil?new Date(grantValidUntil).toISOString():null,reason:grantReason||null})}))
    if(saved) { setGrantSubject('');setGrantValidUntil('');setGrantScopeId('');setGrantReason('') }
  }

  return <div className="access-page">
    {message&&<div className="alert alert-info" role="status">{message}</div>}
    <section className="panel access-scope-panel"><div className="access-scope-heading"><div><span className="access-scope-kicker">WORKSPACE SCOPE</span><h2>{selected?.name??'Select a workspace'}</h2><p>{selected?`Users and grants below are limited to ${selected.name}.`:'Choose a workspace to load its users and scoped access grants.'}</p></div><label className="form-field access-workspace-select">Workspace<select aria-label="Workspace to manage" value={workspaceId} onChange={(event)=>onWorkspaceChange(event.target.value)}><option value="">Select a workspace</option>{workspaces.map((workspace)=><option key={workspace.id} value={workspace.id}>{workspace.name}{workspace.status==='inactive'?' (inactive)':''}</option>)}</select></label></div>{selected&&<div className="access-scope-meta"><span><strong>Identifier</strong>{selected.slug}</span><span><strong>Status</strong>{selected.status}</span><span><strong>Your role</strong>{selected.role??(platformAdmin?'Admin':'No access')}</span></div>}</section>
    {workspaceId&&canManageSelectedWorkspace&&<section className="panel access-add-user-panel"><div className="panel-heading"><div><span className="access-panel-kicker">WORKSPACE MEMBERSHIP + SSD</span><h2>{editingMemberSubject?'Edit user access':'Add user'}</h2><p>Set workspace membership and optional Self Servicing Data permissions in one save.</p></div></div><form className="access-combined-form" onSubmit={saveMembership}>
      <section className="access-form-section"><header><span className="access-form-step">01</span><div><h3>User and workspace access</h3><p>Select an existing TIGR account or create an identity, then set its workspace role and access period.</p></div></header><div className="access-user-fields">{!editingMemberSubject&&<div className="access-user-entry-mode" role="group" aria-label="User account type"><button type="button" className={memberEntryMode==='existing'?'is-active':''} onClick={()=>{setMemberEntryMode('existing');setMemberSubject('');setMemberName('');setMemberEmail('')}}>Existing TIGR user</button><button type="button" className={memberEntryMode==='new'?'is-active':''} onClick={()=>{setMemberEntryMode('new');setMemberSubject('');setMemberName('');setMemberEmail('')}}>Create new user</button></div>}{memberEntryMode==='existing'?<label className="form-field">Name<input required maxLength={240} list="workspace-user-directory" value={memberName} onChange={(event)=>{const value=event.target.value;const normalized=value.trim().toLocaleLowerCase();const matches=namedDirectoryUsers.filter((user)=>[user.display_name,user.email].some((entry)=>entry?.trim().toLocaleLowerCase()===normalized));setMemberName(value);setMemberSubject(editingMemberSubject?memberSubject:(matches.length===1?matches[0].subject:''))}} placeholder="Enter a registered user name or email"/><datalist id="workspace-user-directory">{namedDirectoryUsers.map((user)=><option key={user.subject} value={user.display_name||user.email||''}/>)}{namedDirectoryUsers.filter((user)=>user.display_name&&user.email).map((user)=><option key={`${user.subject}-email`} value={user.email!}/>)}</datalist><small>{memberName&&!memberSubject?'No unique account match. Select a registered user, or choose Create new user.':namedDirectoryUsers.length?'Select an existing TIGR identity.':'No directory accounts are listed. Choose Create new user and enter the exact identity subject.'}</small></label>:<><label className="form-field">Full name<input required maxLength={240} value={memberName} onChange={(event)=>setMemberName(event.target.value)} placeholder="e.g. Amina Hassan"/></label><label className="form-field">Email<input required type="email" maxLength={320} value={memberEmail} onChange={(event)=>setMemberEmail(event.target.value)} placeholder="name@company.com"/></label><label className="form-field access-user-subject">Identity subject / login ID<input required maxLength={180} value={memberSubject} onChange={(event)=>setMemberSubject(event.target.value.trim())} placeholder="Unique user ID from your identity provider"/><small>For OIDC, enter the exact subject claim. For local development, use a unique login ID. This creates TIGR profile/access records; authentication credentials come from your login setup.</small></label></> }<label className="form-field">Workspace role<select value={memberRole} onChange={(event)=>{const nextRole=event.target.value as HumanRole;setMemberRole(nextRole);if(nextRole!=='QA Engineer'){setSsdConnectionIds([]);setSsdSelect(false);setSsdModify(false);setSsdDelete(false);setSsdValidUntil('')}}}><option>Test Data Engineer</option><option>QA Engineer</option><option>Admin</option></select></label><label className="form-field">Access starts<input required type="datetime-local" value={memberValidFrom} onChange={(event)=>setMemberValidFrom(event.target.value)}/></label><label className="form-field">Access expires (optional)<input type="datetime-local" value={memberValidUntil} onChange={(event)=>setMemberValidUntil(event.target.value)}/></label></div></section>
      <section className="access-form-section access-ssd-section"><header><span className="access-form-step">02</span><div><h3>Self Servicing Data access</h3><p>Optional. Select one or more database connections, then choose operations for the QA user.</p></div></header><div className="access-connection-lov" ref={ssdConnectionLovRef} onKeyDown={(event)=>{if(event.key==='Escape')setSsdConnectionsOpen(false)}}><button type="button" className="access-connection-lov-trigger" aria-haspopup="dialog" aria-controls="ssd-connection-options" aria-expanded={ssdConnectionsOpen} disabled={memberRole!=='QA Engineer'||!ssdConnections.length} onClick={()=>setSsdConnectionsOpen((open)=>!open)}><span><small>Database connections</small><strong>{ssdConnectionIds.length?`${ssdConnectionIds.length} connection${ssdConnectionIds.length===1?'':'s'} selected`:selectableSsdConnections.length?'Select connections':'No supported database connections available'}</strong></span><ChevronDown size={16}/></button>{ssdConnectionsOpen&&<div id="ssd-connection-options" className="access-connection-lov-menu" role="dialog" aria-label="Select database connections"><div className="access-connection-lov-actions"><span>{ssdConnectionIds.length} selected</span><button type="button" onClick={()=>setSsdConnectionIds(selectableSsdConnections.map((connection)=>connection.id))}>Select all</button><button type="button" onClick={()=>setSsdConnectionIds([])}>Clear</button></div>{ssdConnections.map((connection)=>{const unsupported=!['postgresql','mysql','oracle','mssql'].includes(connection.connector_type);return <label key={connection.id} className={`${ssdConnectionIds.includes(connection.id)?'is-selected ':''}${unsupported?'is-unavailable':''}`}><input type="checkbox" checked={ssdConnectionIds.includes(connection.id)} disabled={unsupported} onChange={(event)=>setSsdConnectionIds((current)=>event.target.checked?[...current,connection.id]:current.filter((id)=>id!==connection.id))}/><span><strong>{connection.name}</strong><small>{unsupported?`${connection.connector_type.toUpperCase()} · SSD supports PostgreSQL, MySQL, Oracle and SQL Server`:`${connection.status} · ${connection.operation_scope==='read_only'?'Read only':connection.operation_scope==='write_only'?'Write only':'Read and write'}`}</small></span></label>})}</div>}</div>{ssdConnectionIds.length>0&&<div className="access-connection-selected" aria-live="polite">{selectedSsdConnections.map((connection)=><span key={connection.id}>{connection.name}<button type="button" aria-label={`Remove ${connection.name}`} onClick={()=>setSsdConnectionIds((current)=>current.filter((id)=>id!==connection.id))}>×</button></span>)}</div>}<div className="access-ssd-permissions" role="group" aria-label="SSD operations"><label className={ssdSelect?'is-selected':''}><input type="checkbox" checked={ssdSelect} disabled={memberRole!=='QA Engineer'} onChange={(event)=>setSsdSelect(event.target.checked)}/><span><b>SEL</b><strong>Select</strong><small>Read queries</small></span></label><label className={ssdModify?'is-selected':''}><input type="checkbox" checked={ssdModify} disabled={memberRole!=='QA Engineer'} onChange={(event)=>setSsdModify(event.target.checked)}/><span><b>MOD</b><strong>Insert / update</strong><small>Change records</small></span></label><label className={ssdDelete?'is-selected is-destructive':''}><input type="checkbox" checked={ssdDelete} disabled={memberRole!=='QA Engineer'} onChange={(event)=>setSsdDelete(event.target.checked)}/><span><b>DEL</b><strong>Delete</strong><small>Remove records</small></span></label></div><label className="form-field access-ssd-expiry">SSD access expires <span>(optional)</span><input type="datetime-local" value={ssdValidUntil} disabled={memberRole!=='QA Engineer'} onChange={(event)=>setSsdValidUntil(event.target.value)}/></label>{memberRole!=='QA Engineer'&&<p className="access-ssd-hint">SSD connection operations are available for QA Engineer accounts.</p>}{selectedSsdConnections.some((connection)=>connection.status!=='enabled')&&<p className="access-ssd-hint is-warning">One or more selected connections are not enabled. Enable them before use in SSD.</p>}{selectedSsdConnections.some((connection)=>ssdModify&&connection.operation_scope==='read_only')&&<p className="access-ssd-hint is-warning">This connection is read only. In Connections, edit it and set Operation scope to Read and write, then test and enable it. The saved database login must also have write privileges before MOD or DEL can run.</p>}</section>
      <footer className="access-combined-footer"><span>One save adds the user to this workspace and applies selected SSD operations to every checked connection.</span><div className="access-form-actions"><button className="button button-confirm" disabled={busy||!memberSubject}>{busy?'Saving access…':editingMemberSubject?'Save user access':'Add user'}</button>{editingMemberSubject&&<button type="button" className="button button-secondary" disabled={busy} onClick={clearMemberForm}>Cancel</button>}</div></footer>
    </form></section>}
    <div className="access-user-list">
    <section className="panel access-management-panel"><div className="panel-heading"><div><span className="access-panel-kicker">MEMBERSHIP &amp; SSD ACCESS</span><h2>Workspace users{selected?` · ${selected.name}`:''}</h2><p>{selected?`Review workspace membership and SSD permissions for each user.`:'Select a workspace above to view its users and SSD access.'}</p><div className="ssd-access-legend"><span><b>SEL</b> Select</span><span><b>MOD</b> Insert / update</span><span><b>DEL</b> Delete</span></div></div></div>
      {workspaceId&&!canManageSelectedWorkspace&&<p className="quiet-message">Select a workspace where you are an Admin to manage users.</p>}
      {workspaceId&&canManageSelectedWorkspace&&loadingAccess&&accessWorkspaceId!==workspaceId&&<p className="quiet-message">Loading users for {selected?.name??'the selected workspace'}…</p>}
      {workspaceId&&canManageSelectedWorkspace&&accessWorkspaceId===workspaceId&&<div className="table-shell"><table><thead><tr><th>Name</th><th>Workspace role</th><th>SSD connection access</th><th>SSD access</th><th>Access period</th><th>Status</th><th>Actions</th></tr></thead><tbody>{members.map((member)=>{const permissions=activeSsdPermissions(member.subject);return <tr key={member.subject}><td><strong>{member.display_name||member.email||'Unnamed user'}</strong>{member.email&&member.display_name&&<small className="cell-subtitle">{member.email}</small>}</td><td>{member.role}</td><td>{ssdConnectionAccess(member.subject).length?ssdConnectionAccess(member.subject).map((connection)=><div key={connection.id??'unspecified'}>{connection.name}</div>):<span className="cell-subtitle">No SSD connections granted</span>}</td><td><div className="ssd-access-indicators" aria-label={`SSD access: ${permissions.includes('ssd:select')?'select, ':''}${permissions.some((item)=>item==='ssd:insert'||item==='ssd:update')?'modify, ':''}${permissions.includes('ssd:delete')?'delete':'none'}`} title="SEL = select, MOD = insert or update, DEL = delete"><span className={permissions.includes('ssd:select')?'is-granted':''}>SEL</span><span className={permissions.some((item)=>item==='ssd:insert'||item==='ssd:update')?'is-granted':''}>MOD</span><span className={permissions.includes('ssd:delete')?'is-granted':''}>DEL</span></div></td><td>{new Date(member.valid_from).toLocaleString()} – {member.valid_until?new Date(member.valid_until).toLocaleString():'No expiry'}</td><td><span className={`status-badge ${membershipStatus(member)==='active'?'status-success':membershipStatus(member)==='expired'?'status-warning':'status-neutral'}`}><i/>{membershipStatus(member)}</span></td><td><div className="inline-actions">{!member.revoked_at&&<><button className="text-button" disabled={busy} onClick={()=>editMember(member)}>Edit</button><button className="text-button" disabled={busy||member.subject==='local-developer'&&member.role==='Admin'} onClick={()=>action(()=>apiRequest(`/workspaces/${workspaceId}/memberships/${encodeURIComponent(member.subject)}`,role,{method:'DELETE'}))}>Remove access</button></>}</div></td></tr>})}{!members.length&&<tr><td colSpan={7} className="table-empty">No workspace users yet.</td></tr>}</tbody></table></div>}
    </section>
    </div>
    <section className="panel access-management-panel"><div className="panel-heading"><div><h2>Scoped access grants{selected?` · ${selected.name}`:''}</h2><p>{selected?`Grants for ${selected.name} only. Each grant is checked inside this workspace.`:'Select a workspace above to view its grants.'}</p></div></div>
      {workspaceId&&canManageSelectedWorkspace&&<form className="access-form" onSubmit={saveGrant}><h3>Create access grant</h3><div className="form-grid"><label className="form-field">Name<select required value={grantSubject} onChange={(e)=>setGrantSubject(e.target.value)} disabled={!grantableMembers.length}><option value="">{grantableMembers.length?'Select a workspace user':'Add a named active user first'}</option>{grantableMembers.map((member)=><option key={member.subject} value={member.subject}>{member.display_name||member.email}</option>)}</select><small>Only active users added to {selected?.name??'this workspace'} are listed.</small></label><label className="form-field">Resource scope<select value={grantScope} onChange={(e)=>setGrantScope(e.target.value)}><option value="workspace">Workspace</option><option value="dataset">Data set</option><option value="reservation">Reservation</option><option value="lock">Lock</option></select></label>{grantScope!=='workspace'&&<label className="form-field">Resource ID<input required maxLength={180} value={grantScopeId} onChange={(e)=>setGrantScopeId(e.target.value)} placeholder="Resource ID within this workspace"/></label>}<label className="form-field">Permission<input required maxLength={120} value={permission} onChange={(e)=>setPermission(e.target.value)} placeholder="e.g. reservation:read"/><small>The data:read grant is disabled. QA data set access follows workspace membership and publication state.</small></label><label className="form-field">Access expires (optional)<input type="datetime-local" value={grantValidUntil} onChange={(e)=>setGrantValidUntil(e.target.value)}/></label><label className="form-field">Reason<input maxLength={1000} value={grantReason} onChange={(e)=>setGrantReason(e.target.value)} placeholder="Why this access is needed"/></label></div><button className="button button-confirm" disabled={busy||!workspaceId||!grantableMembers.length}>Create grant</button></form>}
      {workspaceId&&canManageSelectedWorkspace&&loadingAccess&&accessWorkspaceId!==workspaceId&&<p className="quiet-message">Loading grants for {selected?.name??'the selected workspace'}…</p>}
      {workspaceId&&canManageSelectedWorkspace&&accessWorkspaceId===workspaceId&&<div className="table-shell"><table><thead><tr><th>Name</th><th>Scope</th><th>Permission</th><th>Expiry</th><th>Status</th><th/></tr></thead><tbody>{grants.map((grant)=>{const grantMember=members.find((member)=>member.subject===grant.subject);return <tr key={grant.id}><td>{grantMember?.display_name||grantMember?.email||'Unnamed user'}</td><td>{grant.scope_type}{grant.scope_id?` · ${grant.scope_id}`:''}</td><td>{grant.permissions.join(', ')}</td><td>{grant.valid_until?new Date(grant.valid_until).toLocaleString():'No expiry'}</td><td>{grant.revoked_at?'Revoked':'Active'}</td><td>{!grant.revoked_at&&<button className="text-button" disabled={busy} onClick={()=>action(()=>apiRequest(`/workspaces/${workspaceId}/grants/${grant.id}/revoke`,role,{method:'POST'}))}>Revoke</button>}</td></tr>})}{!grants.length&&<tr><td colSpan={6} className="table-empty">No access grants.</td></tr>}</tbody></table></div>}
    </section>
  </div>
}

function initials(name:string) { return name.split(/\s+/).filter(Boolean).slice(0,2).map((part)=>part[0]).join('').toUpperCase() }
function humanize(action:string) { return action.replaceAll('.', ' · ').replaceAll('_', ' ') }
function formatTimestamp(value?:string|null) {
  if (!value) return 'Not available'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? 'Not available' : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}
function membershipStatus(member:Person) { if(member.revoked_at)return 'revoked';if(member.valid_until&&Date.parse(member.valid_until)<=Date.now())return 'expired';if(Date.parse(member.valid_from)>Date.now())return 'scheduled';return 'active' }
function toDateTimeInput(value:string|Date) { const date=typeof value==='string'?new Date(value):new Date(value.getTime());date.setMinutes(date.getMinutes()-date.getTimezoneOffset());return date.toISOString().slice(0,16) }

export default App
