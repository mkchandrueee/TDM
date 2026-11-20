import { useEffect, useState } from 'react'
import { Link, NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { Activity, Boxes, ChevronDown, Database, GitBranch, Gauge, LockKeyhole, LogOut, Menu, Network, Settings, ShieldCheck, Sparkles, Users, X } from 'lucide-react'
import { apiRequest, setDevelopmentSubject, type DevelopmentAccess, type HumanRole } from './api'
import LoginPage from './LoginPage'
import ConnectionsPage from './ConnectionsPage'
import DataSetsPage from './DataSetsPage'
import ScenarioToDataPage from './ScenarioToDataPage'
import SourceToDataPage from './SourceToDataPage'
import PrivacyPage from './PrivacyPage'
import ReservationsPage from './ReservationsPage'
import JobsPage from './JobsPage'

type Workspace = { id: string; slug: string; name: string; purpose: string | null; status: 'active' | 'inactive'; role?: HumanRole }
type Person = { subject: string; email: string | null; display_name: string | null; role: HumanRole; valid_from: string; valid_until: string | null; revoked_at: string | null }
type ActivityItem = { id: string; actor_id: string; actor_role: string | null; action: string; object_type: string; object_id: string | null; outcome: string; occurred_at: string | null; request_id: string }
type Metric = { available: boolean; count: number | null; by_status?: Record<string, number> }
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
  { label: 'Data sets', path: '/datasets', icon: Database, group: 'Data management' },
  { label: 'Scenario to Data', path: '/scenario-to-data', icon: Sparkles, group: 'Data management' },
  { label: 'Source to Data', path: '/source-to-data', icon: GitBranch, group: 'Data management' },
  { label: 'Connections', path: '/connections', icon: Network, group: 'Configure' },
  { label: 'PII & Masking', path: '/privacy', icon: ShieldCheck, group: 'Configure' },
  { label: 'Reservations & Locks', path: '/reservations', icon: LockKeyhole, group: 'Operate' },
  { label: 'Jobs', path: '/jobs', icon: Boxes, group: 'Operate' },
  { label: 'Access & Roles', path: '/access', icon: Users, group: 'Administration' },
  { label: 'Activities & Audit', path: '/activities', icon: Activity, group: 'Administration' },
]
const descriptions: Record<string, string> = {
  '/datasets': 'Browse approved data sets, versions and lineage.',
  '/scenario-to-data': 'Turn a test requirement into a reviewed data generation plan.',
  '/source-to-data': 'Build a test data set from an approved source.',
  '/connections': 'Configure and validate source and target connections.',
  '/privacy': 'Review privacy findings and masking policy coverage.',
  '/reservations': 'Coordinate planned use and exclusive data locks.',
  '/jobs': 'Track background work and its execution steps.',
  '/access': 'Manage workspaces, memberships and scoped access.',
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
  const [error, setError] = useState('')
  const location = useLocation()
  const current = navigation.find((item) => item.path === location.pathname) ?? navigation[0]
  const isAdmin = (identity?.platform_admin ?? false) || (identity?.workspace_admin ?? false) || role === 'Admin'
  const visibleNavigation = navigation.filter((item) => !['/access','/activities'].includes(item.path) && (item.path !== '/access' || isAdmin))
  const administrationLinks = navigation.filter((item) => ['/access','/activities'].includes(item.path) && (item.path !== '/access' || isAdmin))
  const groups = [...new Set(visibleNavigation.map((item) => item.group))]

  async function refreshWorkspaces() {
    if (import.meta.env.DEV && !devSession) return
    const [who, rows] = await Promise.all([
      apiRequest<{ subject: string; display_name: string | null; platform_admin: boolean; last_seen_at: string | null; workspaces: Workspace[] }>('/me', role),
      apiRequest<Workspace[]>('/workspaces', role),
    ])
    setIdentity({ subject: who.subject, display_name: who.display_name, platform_admin: who.platform_admin, workspace_admin: who.workspaces.some((workspace) => workspace.role === 'Admin'), last_seen_at: who.last_seen_at })
    setWorkspaces(rows)
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
    if (import.meta.env.DEV && !devSession) return
    let active = true
    setDashboard(null)
    apiRequest<DashboardData>(`/dashboard${workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : ''}`, role)
      .then((data) => { if (active) setDashboard(data) })
      .catch((cause: unknown) => { if (active) setError(cause instanceof Error ? cause.message : 'Unable to load the Dashboard') })
    return () => { active = false }
  }, [role, workspaceId, devSubject, devSession?.subject])

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
            {headerMenu === 'settings' && <div className="header-popover"><strong>Administration</strong>{administrationLinks.map(({label,path,icon:Icon}) => <NavLink key={path} to={path} onClick={() => setHeaderMenu(null)}><Icon size={16}/><span>{label}</span></NavLink>)}</div>}
          </div>}
          <div className="header-menu-wrap">
            <button className="avatar top-avatar" aria-label="Account options" aria-expanded={headerMenu === 'profile'} onClick={() => setHeaderMenu(headerMenu === 'profile' ? null : 'profile')}>{initials(identity?.display_name ?? devSession?.display_name ?? 'User')}</button>
            {headerMenu === 'profile' && <div className="header-popover profile-popover"><strong>{identity?.display_name ?? devSession?.display_name ?? 'User'}</strong>{import.meta.env.DEV && <button className="profile-signout" onClick={() => {setHeaderMenu(null);signOut()}}><LogOut size={15}/>Sign out</button>}</div>}
          </div>
        </div>
      </header>
      <div className="content"><div className="page-heading"><div><h1>{current.label}</h1><p>{location.pathname === '/' ? 'Your TIGR workspace at a glance.' : descriptions[location.pathname]}</p></div>{location.pathname === '/' && <div className="phase-pill"><span/>{workspaces.find((workspace) => workspace.id === workspaceId)?.name ?? 'All workspaces'}</div>}</div>
        {error && <div className="alert alert-warning" role="status">{error}</div>}
        <Routes>
          <Route path="/" element={<Dashboard key={`${workspaceId}:${role}:${devSubject}`} data={dashboard?.workspace_id === (workspaceId || null) ? dashboard : null} workspaceId={workspaceId} role={role} displayName={identity?.display_name ?? devSession?.display_name}/>}/>
          <Route path="/access" element={isAdmin ? <AccessPage role={role} workspaceId={workspaceId} workspaces={workspaces} platformAdmin={identity?.platform_admin ?? false} onRefresh={refreshWorkspaces}/> : <Navigate to="/" replace/>}/>
          <Route path="/activities" element={<ActivitiesPage key={`${workspaceId}:${role}:${devSubject}`} role={role} workspaceId={workspaceId}/>}/>
          <Route path="/connections" element={<ConnectionsPage key={`${workspaceId}:${role}:${devSubject}`} role={role} workspaceId={workspaceId} ownerName={identity?.subject ?? devSession?.subject ?? ''}/>}/>
          <Route path="/datasets" element={<DataSetsPage key={`${workspaceId}:${role}:${devSubject}`} role={role} workspaceId={workspaceId}/>}/>
          <Route path="/scenario-to-data" element={<ScenarioToDataPage key={`${workspaceId}:${role}:${devSubject}`} role={role} workspaceId={workspaceId}/>}/>
          <Route path="/source-to-data" element={<SourceToDataPage key={`${workspaceId}:${role}:${devSubject}:${location.key}`} role={role} workspaceId={workspaceId}/>}/>
          <Route path="/privacy" element={<PrivacyPage key={`${workspaceId}:${role}:${devSubject}`} role={role} workspaceId={workspaceId}/>}/>
          <Route path="/reservations" element={<ReservationsPage key={`${workspaceId}:${role}:${devSubject}`} role={role} workspaceId={workspaceId}/>}/>
          <Route path="/jobs" element={<JobsPage key={`${workspaceId}:${role}:${devSubject}`} role={role} workspaceId={workspaceId}/>}/>
          <Route path="*" element={<Navigate to="/" replace/>}/>
        </Routes>
        <footer><span>TIGR · MVP1</span><span>{import.meta.env.DEV ? 'Development environment' : 'Authenticated environment'} <i/></span></footer>
      </div>
    </main>
  </div>
}

function Dashboard({data, workspaceId, role, displayName}:{data:DashboardData|null;workspaceId:string;role:HumanRole;displayName?:string|null}) {
  const metrics = data?.metrics
  const cards = [
    {key:'activities', label:'Visible activities', icon:Activity, tone:'purple'},
    {key:'datasets', label:'Data sets', icon:Database, tone:'cyan'},
    {key:'jobs', label:'Jobs', icon:Boxes, tone:'orange'},
    {key:'reservations', label:'Reservations', icon:LockKeyhole, tone:'yellow'},
    {key:'locks', label:'Locks', icon:LockKeyhole, tone:'purple'},
    {key:'connections', label:'Source connections', icon:Network, tone:'cyan'},
  ]
  return <>
    <section className="welcome-card"><div className="welcome-copy"><div className="welcome-kicker"><Sparkles size={15}/> YOUR TIGR WORKSPACE</div><h2>{displayName ? `Welcome, ${displayName}!` : 'Welcome!'}</h2><p>Review the data sets, jobs, and activity available to you. Select a workspace to focus on its latest work.</p><div className="welcome-actions"><Link to="/scenario-to-data" className="button button-primary">Explore Scenario to Data</Link><Link to="/source-to-data" className="button button-light">Create from source <span>→</span></Link></div></div><div className="welcome-art"><div className="art-ring ring-one"/><div className="art-ring ring-two"/><div className="art-orb"><Database size={40}/></div><div className="art-chip chip-a"><ShieldCheck size={15}/> Access scoped</div><div className="art-chip chip-b"><GitBranch size={15}/> Auditable</div><div className="art-dot dot-a"/><div className="art-dot dot-b"/></div></section>
    <div className="section-title"><div><h2>Workspace snapshot</h2><p>Summary for the selected access scope.</p></div><span className="updated">{workspaceId ? 'Current workspace' : 'All visible activity'}</span></div>
    <section className="stat-grid">{cards.map(({key,label,icon:Icon,tone})=>{const metric=metrics?.[key];return <article className="stat-card" key={key}><div className={`stat-icon ${tone}`}><Icon size={19}/></div><span>{label}</span><strong>{metric?.available ? metric.count ?? '—' : '—'}</strong><small>{metric?.available ? (key === 'jobs' || key === 'connections' || key === 'datasets' ? Object.entries(metric.by_status ?? {}).map(([status,count])=>`${status}: ${count}`).join(' · ') || (key==='jobs'?'No recorded jobs':key==='datasets'?'No visible data sets':'No configured connections') : 'Authorized records only') : 'Available in a later workflow phase'}</small></article>})}</section>
    <div className="lower-grid"><section className="panel activity-panel"><div className="panel-heading"><div><h2>Recent activities</h2><p>Filtered before listing and totals</p></div><Link to="/activities">View activity <span>→</span></Link></div><ActivityList items={data?.recent_activities ?? []}/></section><section className="panel quick-panel"><div className="panel-heading"><div><h2>Access context</h2><p>Current role and workspace scope</p></div></div><div className="context-card"><span className="context-icon"><Users size={17}/></span><div><strong>{role}</strong><small>{workspaceId ? 'Scoped to selected workspace' : 'Scoped to your available workspaces'}</small></div></div><div className="context-note"><ShieldCheck size={16}/><span>Workspace and object grants are checked by the API. Expired and revoked grants are denied.</span></div></section></div>
    <div className="foundation-note"><span><ShieldCheck size={17}/></span><p><strong>Workspace access controls</strong> · Connection counts include only the selected or otherwise authorized workspace scope.</p></div>
  </>
}

function ActivityList({items}:{items:ActivityItem[]}) {
  if (!items.length) return <div className="empty-state"><div className="empty-icon"><Activity size={20}/></div><strong>No visible activities</strong><p>Authorized activity will appear here.</p></div>
  return <div className="activity-list">{items.map((item)=><div className="activity-row" key={item.id}><span className="activity-dot"/><div><strong>{humanize(item.action)}</strong><small>{item.actor_role ?? 'Service'} · {item.object_type}{item.object_id ? ` · ${item.object_id.slice(0, 10)}` : ''}</small></div><time>{item.occurred_at ? new Date(item.occurred_at).toLocaleString() : ''}</time></div>)}</div>
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
        <thead><tr><th>Action</th><th>Actor role</th><th>Object</th><th>Outcome</th><th>Time</th><th>Request</th></tr></thead>
        <tbody>
          {items.map((item)=><tr key={item.id}><td>{humanize(item.action)}</td><td>{item.actor_role??'Service'}</td><td>{item.object_type}{item.object_id?` · ${item.object_id}`:''}</td><td><span className={`status-badge ${item.outcome==='succeeded'?'status-success':'status-warning'}`}><i/>{item.outcome}</span></td><td>{item.occurred_at?new Date(item.occurred_at).toLocaleString():''}</td><td>{item.request_id.slice(0,12)}</td></tr>)}
          {!loading&&!items.length&&<tr><td colSpan={6} className="table-empty">No visible activity matches this search.</td></tr>}
          {loading&&<tr><td colSpan={6} className="table-empty">Loading authorized activity…</td></tr>}
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

function AccessPage({role,workspaceId,workspaces,platformAdmin,onRefresh}:{role:HumanRole;workspaceId:string;workspaces:Workspace[];platformAdmin:boolean;onRefresh:()=>Promise<void>}) {
  const [members,setMembers]=useState<Person[]>([])
  const [grants,setGrants]=useState<Array<{id:string;subject:string;scope_type:string;scope_id:string|null;permissions:string[];valid_until:string|null;revoked_at:string|null}>>([])
  const [message,setMessage]=useState('')
  const [busy,setBusy]=useState(false)
  const [slug,setSlug]=useState('')
  const [name,setName]=useState('')
  const [purpose,setPurpose]=useState('')
  const [memberSubject,setMemberSubject]=useState('')
  const [memberRole,setMemberRole]=useState<HumanRole>('QA Engineer')
  const [validUntil,setValidUntil]=useState('')
  const [grantScope,setGrantScope]=useState('dataset')
  const [grantScopeId,setGrantScopeId]=useState('')
  const [permission,setPermission]=useState('reservation:read')
  const [grantReason,setGrantReason]=useState('')
  const selected=workspaces.find((workspace)=>workspace.id===workspaceId)

  async function reload() {
    if(!workspaceId) { setMembers([]);setGrants([]);return }
    try {
      const [people,access]=await Promise.all([
        apiRequest<Person[]>(`/workspaces/${workspaceId}/memberships?include_revoked=true`,role),
        apiRequest<typeof grants>(`/workspaces/${workspaceId}/grants?include_revoked=true`,role),
      ])
      setMembers(people);setGrants(access);setMessage('')
    } catch(cause) { setMessage(cause instanceof Error?cause.message:'Unable to load access records') }
  }
  useEffect(()=>{reload()},[workspaceId,role])

  async function action(work:()=>Promise<unknown>): Promise<boolean> {
    setBusy(true);setMessage('')
    try { await work();await onRefresh();await reload();setMessage('Changes saved and recorded in the audit trail.');return true }
    catch(cause) { setMessage(cause instanceof Error?cause.message:'The request could not be completed');return false }
    finally { setBusy(false) }
  }

  return <div className="access-page">
    {message&&<div className="alert alert-info" role="status">{message}</div>}
    <section className="panel access-workspace-panel"><div className="panel-heading"><div><h2>Workspaces</h2><p>{platformAdmin?'Create workspaces and control their lifecycle.':'Workspaces you administer.'}</p></div></div>
      <div className="workspace-admin-list">{workspaces.map((workspace)=><div className="workspace-admin-row" key={workspace.id}><span className="workspace-row-icon">{workspace.name.slice(0,1).toUpperCase()}</span><div><strong>{workspace.name}</strong><small>{workspace.slug} · {workspace.purpose||'No purpose recorded'}</small></div><span className={`status-badge ${workspace.status==='active'?'status-success':'status-neutral'}`}><i/>{workspace.status}</span><button className="button button-secondary compact" disabled={busy} onClick={()=>action(()=>apiRequest(`/workspaces/${workspace.id}`,role,{method:'PATCH',body:JSON.stringify({status:workspace.status==='active'?'inactive':'active'})}))}>{workspace.status==='active'?'Deactivate':'Activate'}</button></div>)}{!workspaces.length&&<p className="quiet-message">No workspaces have been created.</p>}</div>
      {platformAdmin&&<form className="access-form workspace-form" onSubmit={(event)=>{event.preventDefault();void action(()=>apiRequest('/workspaces',role,{method:'POST',body:JSON.stringify({slug,name,purpose:purpose||null})})).then((saved)=>{if(saved){setSlug('');setName('');setPurpose('')}})}}><h3>Create workspace</h3><div className="form-grid"><label className="form-field">Workspace name<input required minLength={2} maxLength={180} value={name} onChange={(e)=>setName(e.target.value)} placeholder="e.g. Quality assurance"/></label><label className="form-field">Identifier<input required minLength={2} pattern="[a-z0-9]+(-[a-z0-9]+)*" maxLength={80} value={slug} onChange={(e)=>setSlug(e.target.value.toLowerCase())} placeholder="e.g. quality-assurance"/><small>Lowercase letters, digits and hyphens</small></label><label className="form-field span-two">Purpose<input maxLength={2000} value={purpose} onChange={(e)=>setPurpose(e.target.value)} placeholder="e.g. Banking test data workflows"/></label></div><button className="button button-confirm" disabled={busy}>Create workspace</button></form>}
    </section>
    <section className="panel access-management-panel"><div className="panel-heading"><div><h2>Memberships</h2><p>{selected?`People with access to ${selected.name}.`:'Select an active workspace to manage members.'}</p></div></div>
      {workspaceId&&<form className="access-form" onSubmit={(event)=>{event.preventDefault();void action(()=>apiRequest(`/workspaces/${workspaceId}/memberships`,role,{method:'POST',body:JSON.stringify({subject:memberSubject,role:memberRole,valid_until:validUntil?new Date(validUntil).toISOString():null})})).then((saved)=>{if(saved){setMemberSubject('');setValidUntil('')}})}}><h3>Add or update a member</h3><div className="form-grid"><label className="form-field">Identity subject<input required maxLength={180} value={memberSubject} onChange={(e)=>setMemberSubject(e.target.value)} placeholder="e.g. OIDC subject (sub)"/><small>Use the exact subject from your identity provider.</small></label><label className="form-field">Human role<select value={memberRole} onChange={(e)=>setMemberRole(e.target.value as HumanRole)}><option>Test Data Engineer</option><option>QA Engineer</option><option>Admin</option></select></label><label className="form-field span-two">Access expires (optional)<input type="datetime-local" value={validUntil} onChange={(e)=>setValidUntil(e.target.value)}/></label></div><button className="button button-confirm" disabled={busy||!workspaceId}>Save membership</button></form>}
      <div className="table-shell"><table><thead><tr><th>Identity</th><th>Role</th><th>Valid until</th><th>Status</th><th/></tr></thead><tbody>{members.map((member)=><tr key={member.subject}><td><strong>{member.display_name||member.subject}</strong>{member.email&&<small className="cell-subtitle">{member.email}</small>}</td><td>{member.role}</td><td>{member.valid_until?new Date(member.valid_until).toLocaleString():'No expiry'}</td><td><span className={`status-badge ${membershipStatus(member)==='active'?'status-success':membershipStatus(member)==='expired'?'status-warning':'status-neutral'}`}><i/>{membershipStatus(member)}</span></td><td>{!member.revoked_at&&<button className="text-button" disabled={busy||member.subject==='local-developer'&&member.role==='Admin'} onClick={()=>action(()=>apiRequest(`/workspaces/${workspaceId}/memberships/${encodeURIComponent(member.subject)}`,role,{method:'DELETE'}))}>Revoke</button>}</td></tr>)}{!members.length&&<tr><td colSpan={5} className="table-empty">No membership records.</td></tr>}</tbody></table></div>
    </section>
    <section className="panel access-management-panel"><div className="panel-heading"><div><h2>Scoped access grants</h2><p>Grant or revoke time-bounded access to a resource inside this workspace.</p></div></div>
      {workspaceId&&<form className="access-form" onSubmit={(event)=>{event.preventDefault();void action(()=>apiRequest(`/workspaces/${workspaceId}/grants`,role,{method:'POST',body:JSON.stringify({subject:memberSubject,scope_type:grantScope,scope_id:grantScope==='workspace'?null:grantScopeId,permissions:[permission],valid_until:validUntil?new Date(validUntil).toISOString():null,reason:grantReason||null})}))}}><h3>Create access grant</h3><div className="form-grid"><label className="form-field">Identity subject<input required maxLength={180} value={memberSubject} onChange={(e)=>setMemberSubject(e.target.value)} placeholder="e.g. OIDC subject (sub)"/></label><label className="form-field">Resource scope<select value={grantScope} onChange={(e)=>setGrantScope(e.target.value)}><option value="workspace">Workspace</option><option value="dataset">Data set</option><option value="reservation">Reservation</option><option value="lock">Lock</option></select></label>{grantScope!=='workspace'&&<label className="form-field">Resource ID<input required maxLength={180} value={grantScopeId} onChange={(e)=>setGrantScopeId(e.target.value)} placeholder="e.g. Opaque resource identifier"/></label>}<label className="form-field">Permission<input required maxLength={120} value={permission} onChange={(e)=>setPermission(e.target.value)} placeholder="e.g. reservation:read"/><small>The data:read grant is disabled. QA data set access follows workspace membership and publication state.</small></label><label className="form-field">Access expires (optional)<input type="datetime-local" value={validUntil} onChange={(e)=>setValidUntil(e.target.value)}/></label><label className="form-field">Reason<input maxLength={1000} value={grantReason} onChange={(e)=>setGrantReason(e.target.value)} placeholder="Why this access is needed"/></label></div><button className="button button-confirm" disabled={busy||!workspaceId}>Create grant</button></form>}
      <div className="table-shell"><table><thead><tr><th>Identity</th><th>Scope</th><th>Permission</th><th>Expiry</th><th>Status</th><th/></tr></thead><tbody>{grants.map((grant)=><tr key={grant.id}><td>{grant.subject}</td><td>{grant.scope_type}{grant.scope_id?` · ${grant.scope_id}`:''}</td><td>{grant.permissions.join(', ')}</td><td>{grant.valid_until?new Date(grant.valid_until).toLocaleString():'No expiry'}</td><td>{grant.revoked_at?'Revoked':'Active'}</td><td>{!grant.revoked_at&&<button className="text-button" disabled={busy} onClick={()=>action(()=>apiRequest(`/workspaces/${workspaceId}/grants/${grant.id}/revoke`,role,{method:'POST'}))}>Revoke</button>}</td></tr>)}{!grants.length&&<tr><td colSpan={6} className="table-empty">No access grants.</td></tr>}</tbody></table></div>
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

export default App
