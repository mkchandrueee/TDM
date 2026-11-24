import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ArrowRight, Bell, Check, CheckCheck, ChevronDown, Download, Plus } from 'lucide-react'
import { Link, useLocation } from 'react-router-dom'
import { apiDownload, apiRequest, type HumanRole } from './api'
import { Modal } from './components/ui'
import './tickets.css'

type Ticket = {
  id: string; display_id: string; title: string | null; description: string | null; status: string; priority: string
  workspace_id: string; workspace_name?: string | null; project_id: string | null; project_name: string | null; requester_name: string; assignee_name: string | null
  requester_user_id: string; assignee_user_id: string | null; needed_by: string | null; submitted_at: string | null
  updated_at: string; row_version: number; current_age_seconds: number | null; final_duration_seconds: number | null
  linked_dataset_id: string | null; linked_dataset_version_id: string | null; fulfillment_summary: string | null
  required_until?: string|null; requested_dataset_id?: string|null; requested_dataset?: {id:string;name:string;reference_code:string}|null
  request_type?: string; environment?: string; application?: string; scenarios?: string[]; schema_reference?: string
  data_date_from?: string|null; data_date_to?: string|null; relationships?: string|null; masking_needs?: string|null
  test_window_start?: string|null; test_window_end?: string|null; acceptance_criteria?: string|null; urgent_justification?: string|null
  record_count?: number; data_source_preference?: string; events?: {id:string;event_type:string;actor_user_id:string|null;actor_name?:string|null;actor_role:string|null;occurred_at:string;from_status?:string|null;to_status?:string|null;reason:string|null;event_data:Record<string,unknown>}[]
  comments?: {id:string;author_user_id:string;author_name?:string|null;author_role:string;body:string;created_at:string}[]
  dataset?: {id?:string;name?:string;reference_code?:string;version_number?:number;state:string}
}

const stamp = (value?: string | null) => value ? new Date(value).toLocaleString() : '—'
function TicketTableDateTime({value}:{value?:string|null}) {
  if(!value)return <>—</>
  const date=new Date(value)
  return <span className="ticket-table-date-time"><time dateTime={value}>{date.toLocaleDateString([], {month:'2-digit',day:'2-digit',year:'2-digit'})} · {date.toLocaleTimeString([], {hour:'numeric',minute:'2-digit'})}</time></span>
}
const age = (seconds: number | null) => seconds == null ? '—' : `${Math.floor(seconds / 86400)}d ${Math.floor(seconds % 86400 / 3600)}h`
const ageTone = (seconds: number | null) => seconds == null ? '' : seconds <= 3 * 86400 ? 'ticket-age-green' : seconds <= 5 * 86400 ? 'ticket-age-amber' : 'ticket-age-red'
const NO_DATASET = '__no_dataset__'
const TICKET_PAGE_SIZE = 5
const emptyRequestForm = () => ({title:'',description:'',request_type:'New dataset',environment:'SIT',application:'',project_id:'',project_name:'',scenario:'',schema_reference:'',record_count:'1',data_source_preference:'Source-backed',needed_by:'',required_until:'',requested_dataset_id:'',data_date_from:'',data_date_to:'',relationships:'',masking_needs:'',test_window_start:'',test_window_end:'',acceptance_criteria:'',priority:'Normal',urgent_justification:''})
type TicketActionDialog = {action:string;title:string;confirmLabel:string;fields:{name:string;label:string;placeholder?:string;multiline?:boolean;options?:{value:string;label:string}[]}[];values:Record<string,string>}
type TicketShareOptions = {users:{subject:string;display_name:string;email:string|null}[];datasets:{id:string;name:string;reference_code:string}[]}
type RequestDatasetOption = {id:string;name:string;reference_code:string;latest_version:{lifecycle_state:string;publication_state:string}}

export default function TicketsPage({ role, workspaceId, workspaceName, currentSubject }: {currentSubject: string; role: HumanRole; workspaceId: string; workspaceName: string}) {
  const location = useLocation()
  const [items, setItems] = useState<Ticket[]>([])
  const [ticketTotal, setTicketTotal] = useState(0)
  const [ticketPage, setTicketPage] = useState(1)
  const [ticketHasMore, setTicketHasMore] = useState(false)
  const [existingDraft, setExistingDraft] = useState<Ticket|null>(null)
  const [existingDraftLoading, setExistingDraftLoading] = useState(true)
  const [selected, setSelected] = useState<Ticket | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [requestStep, setRequestStep] = useState(0)
  const [draftSaveState, setDraftSaveState] = useState<'idle'|'saving'|'saved'|'error'>('idle')
  const [editingDraftId, setEditingDraftId] = useState<string|null>(null)
  const [editingRowVersion, setEditingRowVersion] = useState<number|null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [notifications, setNotifications] = useState<{id:string;event_type:string;payload:{ticket_id:string;ticket_uuid:string;title:string;status:string;assigned_from?:string|null;assigned_from_name?:string|null;assigned_to?:string|null;assigned_to_name?:string|null;assignment_reason?:string;reason?:string;comment_body?:string;comment_author?:string;comment_author_role?:string;dataset_id?:string|null;dataset_name?:string|null;shared_with?:string|null;no_dataset?:boolean;requires_action?:boolean};created_at:string;read_at:string|null}[]>([])
  const [notificationsOpen, setNotificationsOpen] = useState(false)
  const [status, setStatus] = useState('')
  const [priority, setPriority] = useState('')
  const [ageBucket, setAgeBucket] = useState('')
  const [sort, setSort] = useState('submitted_at')
  const [queueView, setQueueView] = useState<'queue'|'assigned'|'open'>('queue')
  const [navigationReset, setNavigationReset] = useState(0)
  const [comment, setComment] = useState('')
  const activityListRef = useRef<HTMLOListElement|null>(null)
  const draftSaveTimerRef = useRef<number|null>(null)
  const [actionDialog, setActionDialog] = useState<TicketActionDialog|null>(null)
  const [shareDialogOpen, setShareDialogOpen] = useState(false)
  const [shareOptions, setShareOptions] = useState<TicketShareOptions|null>(null)
  const [shareOptionsLoading, setShareOptionsLoading] = useState(false)
  const [requestDatasets, setRequestDatasets] = useState<RequestDatasetOption[]>([])
  const [requestDatasetsLoading, setRequestDatasetsLoading] = useState(false)
  const [shareForm, setShareForm] = useState({shared_with_user_id:'',dataset_id:'',fulfillment_summary:''})
  const [form, setForm] = useState(emptyRequestForm)
  const [draftKey, setDraftKey] = useState(()=>crypto.randomUUID())

  async function refresh() {
    if (!workspaceId) return
    setLoading(true); setError('');if(role==='QA Engineer')setExistingDraftLoading(true)
    try {
      const query = new URLSearchParams({workspace_id:workspaceId,limit:String(TICKET_PAGE_SIZE),offset:String((ticketPage-1)*TICKET_PAGE_SIZE)})
      if (search) query.set('search',search)
      if (status) query.set('status',status)
      if (priority) query.set('priority',priority)
      if (ageBucket) query.set('age_bucket',ageBucket)
      query.set('sort',sort)
      if (queueView==='assigned')query.set('assigned_to_me','true')
      if (queueView==='open')query.set('open_only','true')
      const data = await apiRequest<{items:Ticket[];total:number;has_more:boolean}>(`/tickets?${query}`,role)
      setItems(data.items)
      setTicketTotal(data.total);setTicketHasMore(data.has_more)
      if(role==='QA Engineer'){
        const draft=await apiRequest<Ticket|null>('/tickets/draft',role).catch(()=>null)
        setExistingDraft(draft)
      }
      const notices = await apiRequest<typeof notifications>(`/notifications/tickets?workspace_id=${encodeURIComponent(workspaceId)}`,role).catch(()=>[])
      setNotifications(notices)
      if (selected) {
        const current = data.items.find(x=>x.id===selected.id)
        if (current) await openTicket(current.id)
      }
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not load tickets') }
    finally { setLoading(false);if(role==='QA Engineer')setExistingDraftLoading(false) }
  }
  async function openTicket(id:string) {
    try { setSelected(await apiRequest<Ticket>(`/tickets/${id}`,role)); setCreateOpen(false) }
    catch(e) { setError(e instanceof Error ? e.message : 'Could not load ticket') }
  }
  async function openNotification(notification:typeof notifications[number]) {
    setNotificationsOpen(false)
    if(!notification.read_at){
      const readAt=new Date().toISOString()
      setNotifications(current=>current.map(item=>item.id===notification.id?{...item,read_at:readAt}:item))
      void apiRequest(`/notifications/tickets/${notification.id}/read?workspace_id=${encodeURIComponent(workspaceId)}`,role,{method:'PATCH'}).catch(e=>setError(e instanceof Error?e.message:'Could not update notification'))
    }
    await openTicket(notification.payload.ticket_uuid)
  }
  async function markAllNotificationsRead() {
    const readAt=new Date().toISOString()
    setNotifications(current=>current.map(item=>item.read_at?item:{...item,read_at:readAt}))
    try { await apiRequest(`/notifications/tickets/read-all?workspace_id=${encodeURIComponent(workspaceId)}`,role,{method:'PATCH'}) }
    catch(e) {
      setError(e instanceof Error?e.message:'Could not update notifications')
      const current=await apiRequest<typeof notifications>(`/notifications/tickets?workspace_id=${encodeURIComponent(workspaceId)}`,role).catch(()=>null)
      if(current)setNotifications(current)
    }
  }
  useEffect(()=>{void refresh()},[workspaceId,status,queueView,priority,ageBucket,sort,navigationReset,ticketPage])
  useEffect(()=>{
    if(!createOpen||form.request_type==='New dataset'||!workspaceId)return
    let active=true
    setRequestDatasetsLoading(true)
    ;(async()=>{
      const items:RequestDatasetOption[]=[]
      let page=1,hasMore=true
      while(hasMore){
        const result=await apiRequest<{items:RequestDatasetOption[];has_more:boolean}>(`/datasets?workspace_id=${encodeURIComponent(workspaceId)}&lifecycle_state=published&page_size=100&page=${page}`,role)
        items.push(...result.items);hasMore=result.has_more;page+=1
      }
      if(active)setRequestDatasets(items.filter(item=>item.latest_version.lifecycle_state==='published'&&item.latest_version.publication_state==='published'))
    })()
      .catch(error=>{if(active)setError(error instanceof Error?error.message:'Could not load published datasets')})
      .finally(()=>{if(active)setRequestDatasetsLoading(false)})
    return()=>{active=false}
  },[createOpen,form.request_type,workspaceId,role])
  useEffect(()=>{
    setSelected(null);setCreateOpen(false);setEditingDraftId(null);setEditingRowVersion(null)
    setQueueView('queue');setStatus('');setPriority('');setAgeBucket('');setSearch('');setSort('submitted_at');setTicketPage(1)
    setNavigationReset(value=>value+1)
  },[location.key])

  function requestPayload() {
    return {workspace_id:workspaceId,title:form.title,description:form.description,
        request_type:form.request_type,environment:form.environment,application:form.application,scenarios:form.scenario.split('\n').filter(Boolean),
        schema_reference:form.schema_reference,record_count:form.record_count?Number(form.record_count):null,data_source_preference:form.data_source_preference,priority:form.priority,
        project_id:form.project_id||null,project_name:form.project_name||null,needed_by:form.needed_by?new Date(`${form.needed_by}T23:59:59`).toISOString():null,
        required_until:form.required_until||null,requested_dataset_id:form.request_type==='New dataset'?null:form.requested_dataset_id||null,
        data_date_from:form.data_date_from||null,data_date_to:form.data_date_to||null,relationships:form.relationships||null,masking_needs:form.masking_needs||null,
        test_window_start:form.test_window_start?new Date(form.test_window_start).toISOString():null,test_window_end:form.test_window_end?new Date(form.test_window_end).toISOString():null,
        acceptance_criteria:form.acceptance_criteria||null,urgent_justification:form.urgent_justification||null}
  }
  async function persistDraft() {
    if(!form.title.trim()) { setDraftSaveState('idle'); return null }
    setError('');setDraftSaveState('saving')
    try {
      const result = editingDraftId&&editingRowVersion
        ? await apiRequest<Ticket>(`/tickets/${editingDraftId}?row_version=${editingRowVersion}`,role,{method:'PATCH',body:JSON.stringify(requestPayload())})
        : await apiRequest<Ticket>('/tickets',role,{method:'POST',headers:{'Idempotency-Key':draftKey},body:JSON.stringify(requestPayload())})
      setEditingDraftId(result.id);setEditingRowVersion(result.row_version);setExistingDraft(result);setDraftSaveState('saved');return result
    } catch(e) { setDraftSaveState('error');setError(e instanceof Error ? e.message : 'Could not save draft');return null }
  }
  async function moveRequestStep(step:number) {
    const target=Math.max(0,Math.min(3,step))
    document.getElementById(`ticket-request-step-${target}`)?.scrollIntoView({behavior:'smooth',block:'start'})
  }
  function startNewRequest() {
    if(draftSaveTimerRef.current!==null)window.clearTimeout(draftSaveTimerRef.current)
    draftSaveTimerRef.current=null
    setForm(emptyRequestForm());setDraftKey(crypto.randomUUID());setEditingDraftId(null);setEditingRowVersion(null)
    setRequestStep(0);setDraftSaveState('idle');setError('');setCreateOpen(true)
  }
  async function submit(event:FormEvent) {
    event.preventDefault()
    const result=await persistDraft()
    if(!result)return
    setEditingDraftId(null);setEditingRowVersion(null);setDraftSaveState('idle')
    setCreateOpen(false); await refresh(); await openTicket(result.id)
  }
  async function submitDraftRequest() {
    if(!editingDraftId)return
    const draft=await persistDraft()
    if(!draft)return
    try{
      const submitted=await apiRequest<Ticket>(`/tickets/${draft.id}/submit`,role,{method:'POST',headers:{'Idempotency-Key':`${draft.id}:submit:${draft.row_version}`},body:JSON.stringify({row_version:draft.row_version})})
      setExistingDraft(null);setEditingDraftId(null);setEditingRowVersion(null);setDraftSaveState('idle');setCreateOpen(false)
      await refresh();await openTicket(submitted.id)
    }catch(e){setError(e instanceof Error?e.message:'Could not submit request')}
  }
  async function act(path:string, body:Record<string,unknown> = {}) {
    if (!selected) return false
    setError('')
    try { await apiRequest(`/tickets/${selected.id}/${path}`,role,{method:'POST',headers:{'Idempotency-Key':`${selected.id}:${path}:${selected.row_version}`},body:JSON.stringify({row_version:selected.row_version,...body})}); await refresh(); await openTicket(selected.id); return true }
    catch(e) { setError(e instanceof Error ? e.message : 'Action failed'); return false }
  }
  function showActionDialog(action:string,title:string,confirmLabel:string,fields:TicketActionDialog['fields']) {
    setError('')
    setActionDialog({action,title,confirmLabel,fields,values:Object.fromEntries(fields.map(field=>[field.name,'']))})
  }
  async function showAssignDialog() {
    if (!selected) return
    setError('')
    try {
      const users=await apiRequest<{subject:string;display_name:string;email:string|null}[]>(`/tickets/${selected.id}/assignment-options`,role)
      const options=users.map(user=>({value:user.subject,label:user.display_name}))
      const current=options.some(option=>option.value===selected.assignee_user_id)?selected.assignee_user_id??'':options[0]?.value||''
      setActionDialog({action:'assign',title:'Assign ticket',confirmLabel:'Assign ticket',fields:[
        {name:'assignee_user_id',label:'TDE assignee',options},
        {name:'reason',label:'Assignment reason',placeholder:'Explain the assignment'}
      ],values:{assignee_user_id:current,reason:''}})
    } catch(e) { setError(e instanceof Error ? e.message : 'Could not load TDE users') }
  }
  async function submitActionDialog(event:FormEvent) {
    event.preventDefault()
    if (!actionDialog) return
    const payload:Record<string,unknown>={...actionDialog.values}
    if (await act(actionDialog.action,payload)) setActionDialog(null)
  }
  async function openShareDialog() {
    if (!selected) return
    setError(''); setShareOptionsLoading(true); setShareDialogOpen(true)
    try {
      const options=await apiRequest<TicketShareOptions>(`/tickets/${selected.id}/share-options`,role)
      setShareOptions(options)
      setShareForm({shared_with_user_id:options.users.some(user=>user.subject===selected.requester_user_id)?selected.requester_user_id:options.users[0]?.subject||'',
        dataset_id:NO_DATASET,fulfillment_summary:selected.fulfillment_summary||''})
    } catch(e) { setError(e instanceof Error ? e.message : 'Could not load share options') }
    finally { setShareOptionsLoading(false) }
  }
  async function submitShareDialog(event:FormEvent) {
    event.preventDefault()
    const noDataset=shareForm.dataset_id===NO_DATASET
    if (!selected||!shareForm.dataset_id||(!noDataset&&!shareForm.shared_with_user_id)||!shareForm.fulfillment_summary.trim()) return
    const payload=noDataset?{no_dataset:true,fulfillment_summary:shareForm.fulfillment_summary}:
      {dataset_id:shareForm.dataset_id,shared_with_user_id:shareForm.shared_with_user_id,fulfillment_summary:shareForm.fulfillment_summary}
    if (await act('close',payload)) setShareDialogOpen(false)
  }
  async function addComment(e:FormEvent) {
    e.preventDefault(); if (!selected || !comment.trim()) return
    setError('')
    try { await apiRequest(`/tickets/${selected.id}/comments`,role,{method:'POST',body:JSON.stringify({body:comment})}); setComment(''); await openTicket(selected.id) }
    catch(e) { setError(e instanceof Error ? e.message : 'Comment failed') }
  }
  async function exportCsv() {
    try { const params=new URLSearchParams({workspace_id:workspaceId,sort});if(status)params.set('status',status);if(priority)params.set('priority',priority);if(ageBucket)params.set('age_bucket',ageBucket);if(search)params.set('search',search);if(queueView==='assigned')params.set('assigned_to_me','true');if(queueView==='open')params.set('open_only','true');const {blob,filename}=await apiDownload(`/reports/tickets/export?${params}`,role); const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=filename||'tickets.csv';a.click();URL.revokeObjectURL(a.href) }
    catch(e) { setError(e instanceof Error ? e.message : 'Export failed') }
  }
  function editDraft(ticket:Ticket|null=selected) {
    if(!ticket)return
    const local=(value?:string|null)=>value?new Date(value).toISOString().slice(0,16):''
    const localDate=(value?:string|null)=>{
      if(!value)return ''
      if(/^\d{4}-\d{2}-\d{2}$/.test(value))return value
      const date=new Date(value)
      return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`
    }
    setForm({...form,title:ticket.title||'',description:ticket.description||'',request_type:ticket.request_type||'New dataset',environment:ticket.environment||'',application:ticket.application||'',
      project_id:ticket.project_id||'',project_name:ticket.project_name||'',scenario:ticket.scenarios?.join('\n')||'',schema_reference:ticket.schema_reference||'',record_count:ticket.record_count?String(ticket.record_count):'',
      data_source_preference:ticket.data_source_preference==='Synthetic'?'Synthetic':'Source-backed',needed_by:localDate(ticket.needed_by),required_until:ticket.required_until||localDate(ticket.data_date_to),requested_dataset_id:ticket.requested_dataset_id||'',priority:ticket.priority==='Low'||ticket.priority==='Urgent'?ticket.priority:'Normal',urgent_justification:ticket.urgent_justification||'',
      data_date_from:ticket.data_date_from||'',data_date_to:ticket.data_date_to||'',relationships:ticket.relationships||'',masking_needs:ticket.masking_needs||'',
      test_window_start:local(ticket.test_window_start),test_window_end:local(ticket.test_window_end),acceptance_criteria:ticket.acceptance_criteria||''})
    setDraftSaveState('idle');setRequestStep(0);setEditingDraftId(ticket.id);setEditingRowVersion(ticket.row_version);setCreateOpen(true);setSelected(null)
  }
  const eventLabels:Record<string,string>={created:'Draft created',draft_edited:'Draft edited',submitted:'Request submitted',assignment_changed:'Assignee changed',status_changed:'Status changed',dataset_linked:'Dataset linked',closed:'Ticket closed',cancelled:'Ticket cancelled',reopened:'Ticket reopened'}
  const userEvents=(selected?.events||[]).filter(event=>event.actor_user_id&&event.event_type!=='comment_added')
  const firstDraftCreated=userEvents.filter(event=>event.event_type==='created').sort((a,b)=>new Date(a.occurred_at).getTime()-new Date(b.occurred_at).getTime())[0]
  const latestDraftEdit=userEvents.filter(event=>event.event_type==='draft_edited').sort((a,b)=>new Date(b.occurred_at).getTime()-new Date(a.occurred_at).getTime())[0]
  const visibleUserEvents=userEvents.filter(event=>event.event_type==='created'?event.id===firstDraftCreated?.id:event.event_type==='draft_edited'?event.id===latestDraftEdit?.id:true)
  const timeline = selected ? [
    ...visibleUserEvents.map(event=>({
      id:event.id,when:event.occurred_at,title:event.event_type==='draft_edited'?'Last updated':eventLabels[event.event_type]||event.event_type.replaceAll('_',' '),
      actor:event.actor_name||event.actor_user_id||'',role:event.actor_role||'',
      detail:event.event_type==='status_changed'?`${event.from_status||'—'} → ${event.to_status||'—'}`:event.event_type==='assignment_changed'?`${event.event_data.before_name||((event.event_data.before===selected.requester_user_id)?selected.requester_name:(event.event_data.before===event.actor_user_id)?event.actor_name:event.event_data.before)||'Unassigned'} → ${event.event_data.after_name||((event.event_data.after===selected.requester_user_id)?selected.requester_name:(event.event_data.after===event.actor_user_id)?event.actor_name:(event.event_data.after===selected.assignee_user_id)?selected.assignee_name:event.event_data.after)||'Unassigned'}`:event.reason||'',kind:'event'})),
    ...(selected.comments||[]).map(entry=>({id:entry.id,when:entry.created_at,title:'Comment',actor:entry.author_name||entry.author_user_id,role:entry.author_role,detail:entry.body,kind:'comment'})),
  ].sort((a,b)=>new Date(a.when).getTime()-new Date(b.when).getTime()) : []
  const latestTimelineEntry = timeline[timeline.length-1]
  const noDatasetSelected = shareForm.dataset_id===NO_DATASET
  const isAssignedEngineer = role==='Test Data Engineer' && selected?.assignee_user_id===currentSubject
  const commentDisabledReason = role==='QA Engineer' && selected?.status==='Closed'
    ? 'Comments are unavailable to QA on closed tickets'
    : role==='Test Data Engineer' && selected && ['Submitted','In Progress','On Hold'].includes(selected.status) && !isAssignedEngineer
      ? 'Only the assigned Test Data Engineer can comment on this open ticket'
      : ''
  const selectedRequestDataset=requestDatasets.find(dataset=>dataset.id===form.requested_dataset_id)
  const ticketStart=ticketTotal?(ticketPage-1)*TICKET_PAGE_SIZE+1:0
  const ticketEnd=Math.min(ticketPage*TICKET_PAGE_SIZE,ticketTotal)
  const unreadNotificationCount=notifications.filter(item=>!item.read_at).length
  const latestNotification=notifications[0]
  const latestNotificationLabel=latestNotification?.event_type==='ticket.comment_action_required'?'Comment · response needed':latestNotification?.event_type==='ticket.closed'?'Ticket closed':latestNotification?.event_type==='ticket.reopened'?'Ticket reopened':latestNotification?.event_type==='ticket.assigned'?(latestNotification.payload.assigned_from?'Ticket reassigned':'Ticket assigned'):'Ticket notifications'
  const notificationGroups=(()=>{
    const today=new Date().toDateString()
    const yesterday=new Date(Date.now()-86400000).toDateString()
    const groups:{title:string;items:typeof notifications}[]=[]
    for(const [title,match] of [['Today',(item:typeof notifications[number])=>new Date(item.created_at).toDateString()===today],['Yesterday',(item:typeof notifications[number])=>new Date(item.created_at).toDateString()===yesterday],['Earlier',(_:typeof notifications[number])=>true]] as const){
      const groupItems=notifications.filter(item=>match(item)&&!groups.some(group=>group.items.includes(item))).sort((a,b)=>Number(Boolean(a.read_at))-Number(Boolean(b.read_at))||new Date(b.created_at).getTime()-new Date(a.created_at).getTime())
      if(groupItems.length)groups.push({title,items:groupItems})
    }
    return groups
  })()
  useEffect(()=>{
    if (latestTimelineEntry) activityListRef.current?.scrollTo({top:activityListRef.current.scrollHeight})
  },[selected?.id,latestTimelineEntry?.id])
  useEffect(()=>{
    if(!notificationsOpen)return
    const closeOnOutside=(event:MouseEvent)=>{
      const target=event.target as Element
      if(!target.closest('.ticket-notification-bar, .ticket-notification-panel'))setNotificationsOpen(false)
    }
    const closeOnEscape=(event:KeyboardEvent)=>{if(event.key==='Escape')setNotificationsOpen(false)}
    document.addEventListener('mousedown',closeOnOutside);document.addEventListener('keydown',closeOnEscape)
    return()=>{document.removeEventListener('mousedown',closeOnOutside);document.removeEventListener('keydown',closeOnEscape)}
  },[notificationsOpen])

  return <section className="tickets-page">
    {error && <div className="alert alert-warning" role="alert">{error}</div>}
    {!selected && !createOpen && <>
      {role==='Test Data Engineer'&&<div className="ticket-tabs"><button className={queueView==='queue'?'active':''} onClick={()=>{setQueueView('queue');setTicketPage(1)}}>All tickets</button><button className={queueView==='assigned'?'active':''} onClick={()=>{setQueueView('assigned');setTicketPage(1)}}>Assigned to Me</button><button className={queueView==='open'?'active':''} onClick={()=>{setQueueView('open');setTicketPage(1)}}>All Open</button></div>}
      <div className="ticket-toolbar"><button type="button" className={`ticket-notification-bar${notificationsOpen?' is-open':''}`} aria-expanded={notificationsOpen} aria-controls="ticket-notification-panel" onClick={()=>setNotificationsOpen(open=>!open)}><span className="ticket-notification-bar-side"><Bell size={15}/></span><span className="ticket-notification-bar-content"><strong>{latestNotification?`${latestNotificationLabel} · ${latestNotification.payload.ticket_id} · ${latestNotification.payload.status}`:'No recent ticket notifications'}</strong></span><span className="ticket-notification-bar-side ticket-notification-bar-side-right"><span className={`ticket-notification-bar-count${unreadNotificationCount?' has-unread':''}`}>{unreadNotificationCount?`${unreadNotificationCount} unread`:'Updates'}</span><ChevronDown size={15}/></span></button>
        <div className="ticket-toolbar-heading"><div><strong>Search and filters</strong><small>Narrow the list by status, priority, age, or sort order.</small></div>
        <div className="ticket-toolbar-actions">{role==='Test Data Engineer'&&<button type="button" className="icon-button ticket-export-button" aria-label="Export tickets as CSV" title="Export CSV" onClick={()=>void exportCsv()}><Download size={17}/></button>}{role==='QA Engineer'&&(existingDraftLoading?<button type="button" className="button button-secondary" disabled>Checking draft…</button>:existingDraft?<div className="ticket-existing-draft-action"><button type="button" className="button button-confirm" onClick={()=>editDraft(existingDraft)}>Continue draft · {existingDraft.display_id}<ArrowRight size={15}/></button><small>Submit or cancel this draft before starting another request.</small></div>:<button type="button" className="button button-confirm" onClick={startNewRequest}><Plus size={15}/>New test data request</button>)}
        </div>
      </div><div className="ticket-toolbar-fields"><label className="form-field ticket-search">Search<input value={search} onChange={e=>setSearch(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'){if(ticketPage===1)void refresh();else setTicketPage(1)}}} placeholder="Ticket ID or title"/></label>
        <label className="form-field">Status<select value={status} onChange={e=>{setStatus(e.target.value);setTicketPage(1)}}><option value="">All statuses</option>{['Draft','Submitted','In Progress','On Hold','Closed','Cancelled'].map(x=><option key={x}>{x}</option>)}</select></label>
        <label className="form-field">Priority<select value={priority} onChange={e=>{setPriority(e.target.value);setTicketPage(1)}}><option value="">All priorities</option>{['Urgent','Normal','Low'].map(x=><option key={x}>{x}</option>)}</select></label>
        <label className="form-field">Open age<select value={ageBucket} onChange={e=>{setAgeBucket(e.target.value);setTicketPage(1)}}><option value="">Any age</option>{[['under_1_day','Under 1 day'],['1_3_days','1–3 days'],['4_7_days','4–7 days'],['8_14_days','8–14 days'],['over_14_days','Over 14 days']].map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
        <label className="form-field">Sort<select value={sort} onChange={e=>{setSort(e.target.value);setTicketPage(1)}}>{[['submitted_at','Submitted'],['needed_by','Needed by'],['priority','Priority'],['age','Open age'],['updated_at','Last updated'],['status','Status']].map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label></div>
      {notificationsOpen&&<section className="ticket-notification-panel" id="ticket-notification-panel" aria-label="Ticket notifications"><header><div><strong>Notifications</strong><small>{unreadNotificationCount?`${unreadNotificationCount} unread`:'You’re all caught up'}</small></div><div className="ticket-notification-panel-actions">{unreadNotificationCount>0&&<button type="button" onClick={()=>void markAllNotificationsRead()}><CheckCheck size={14}/>Mark all read</button>}</div></header>
        <div className="ticket-notification-list">{notificationGroups.length?notificationGroups.map(group=><section className="ticket-notification-group" key={group.title}><h3>{group.title}</h3>{group.items.map(n=>{
          const isComment=n.event_type==='ticket.comment_action_required'
          const assignment=n.event_type==='ticket.assigned'
          const isClosed=n.event_type==='ticket.closed'
          const isReopened=n.event_type==='ticket.reopened'
          const heading=isComment?'Comment · response needed':isClosed?'Ticket closed':isReopened?'Ticket reopened':assignment?(n.payload.assigned_from?'Ticket reassigned':'Ticket assigned'):'Ticket update'
          const assignmentDetail=n.payload.assigned_to?`Assigned to ${n.payload.assigned_to_name||n.payload.assigned_to}${n.payload.assigned_from?` · moved from ${n.payload.assigned_from_name||n.payload.assigned_from}`:''}`:'Ticket assignment changed'
          const closedDetail=n.payload.no_dataset?'Ticket closed without a data set.':n.payload.dataset_name?`Published data set ${n.payload.dataset_name} was shared.`:'The published data set was shared.'
          const reopenedDetail=n.payload.reason?`Reason: ${n.payload.reason}`:'Ticket returned to In Progress.'
          return <button type="button" className={`ticket-notice${n.read_at?'':' is-unread'}${isComment?' ticket-notice-comment':''}`} key={n.id} onClick={()=>void openNotification(n)}><span className="ticket-notice-indicator" aria-hidden="true"/><span className="ticket-notice-content"><span className="ticket-notice-topline"><strong className="ticket-notice-title">{heading}{!n.read_at&&<i>New</i>}</strong><span className={`ticket-notice-status ticket-notice-status-${n.payload.status.toLowerCase().replaceAll(' ','-')}`}>{n.payload.status}</span></span><span className="ticket-notice-ticket"><strong>{n.payload.ticket_id}</strong><span>{n.payload.title}</span></span>{isComment?<span className="ticket-notice-comment-copy">“{n.payload.comment_body||'New comment added'}”</span>:<span className="ticket-notice-assignment-copy">{isClosed?closedDetail:isReopened?reopenedDetail:assignmentDetail}</span>}<span className="ticket-notice-meta">{isComment?<small>{n.payload.comment_author||'Ticket participant'} · {n.payload.comment_author_role||'Participant'}</small>:assignment&&!isClosed&&n.payload.assignment_reason?<small>Reason: {n.payload.assignment_reason}</small>:<small>{heading}</small>}<time>{stamp(n.created_at)}</time></span></span></button>
        })}</section>):<div className="ticket-notification-empty"><span><Bell size={20}/></span><strong>No notifications yet</strong><small>Ticket assignments, comments, closures, and reopens will show here.</small></div>}</div>
      </section>}
      </div>
      <div className="ticket-table-wrap"><table className={`ticket-table${role==='QA Engineer'?' ticket-table-qa':''}`}><thead><tr>{['Ticket','Project','Requester','Assignee','Status','Priority','Needed by','Submitted','Open age','Updated'].map(x=><th key={x}>{x}</th>)}</tr></thead>
        <tbody>{items.map(t=><tr key={t.id} onClick={()=>void openTicket(t.id)}><td><button className="ticket-link">{t.display_id}<small>{t.title}</small></button></td><td>{t.project_name||'—'}<small>{t.project_id||''}</small></td><td>{t.requester_name}</td><td>{t.assignee_name||'Unassigned'}</td><td><span className={`ticket-status status-${t.status.toLowerCase().replaceAll(' ','-')}`}>{t.status}</span></td><td>{t.priority}</td><td><TicketTableDateTime value={t.needed_by}/></td><td><TicketTableDateTime value={t.submitted_at}/></td><td>{age(t.current_age_seconds)}</td><td><TicketTableDateTime value={t.updated_at}/></td></tr>)}
        {!items.length&&<tr><td colSpan={10} className="ticket-empty">{loading?'Loading tickets…':'No tickets match these filters.'}</td></tr>}</tbody></table></div>
      <div className="ticket-pagination"><span>Showing {ticketStart}–{ticketEnd} of {ticketTotal}</span><div><button type="button" className="button button-secondary" disabled={ticketPage<=1||loading} onClick={()=>setTicketPage(value=>Math.max(1,value-1))}>Previous</button><small>Page {ticketPage}</small><button type="button" className="button button-secondary" disabled={!ticketHasMore||loading} onClick={()=>setTicketPage(value=>value+1)}>Next</button></div></div>
    </>}
    {createOpen && <form className="ticket-form ticket-request-form ticket-request-wizard" onSubmit={submit}><div className="ticket-request-header"><div><button type="button" className="text-button" onClick={()=>{setCreateOpen(false);setEditingDraftId(null);setEditingRowVersion(null)}}>← Back to tickets</button><h2>{editingDraftId?'Edit draft request':'New test data request'}</h2><p>Share what you need to test, which data it relates to, and when you need it.</p></div><span className="ticket-request-workspace"><small>WORKSPACE</small><strong>{workspaceName||selected?.workspace_name||'—'}</strong></span></div>
      <div className="ticket-request-wizard-body"><nav className="ticket-request-stepper" aria-label="Request steps"><ol>{['Request details','Data & delivery','Scope & handling','Review request'].map((label,index)=><li key={label}><button type="button" disabled={draftSaveState==='saving'} className={`ticket-request-step${requestStep===index?' active':''}${requestStep>index?' complete':''}`} aria-current={requestStep===index?'step':undefined} onClick={()=>void moveRequestStep(index)}><span>{requestStep>index?<Check size={14}/>:index+1}</span><div><strong>{label}</strong><small>{['Request type and project','Volume, priority, and dates','Objective and data constraints','Check before saving'][index]}</small></div></button></li>)}</ol></nav>
        <section className="ticket-request-stage" onScroll={event=>{const container=event.currentTarget;const sections=[...container.querySelectorAll<HTMLElement>('[data-request-step]')];let active=0;sections.forEach((section,index)=>{if(section.getBoundingClientRect().top-container.getBoundingClientRect().top<=container.clientTop+110)active=index});if(active!==requestStep){setRequestStep(active);if(form.title.trim()){if(draftSaveTimerRef.current!==null)window.clearTimeout(draftSaveTimerRef.current);draftSaveTimerRef.current=window.setTimeout(()=>void persistDraft(),450)}}}}>
          <section className="ticket-request-flow-section" id="ticket-request-step-0" data-request-step="0"><div className="ticket-request-stage-heading"><span>STEP 1 OF 4</span><h3>Request details</h3><p>Start with a clear title and identify the system or existing data set.</p></div><div className="ticket-form-grid ticket-request-stage-fields">
            <label className="form-field wide">Request title<input value={form.title} onChange={e=>setForm({...form,title:e.target.value})} placeholder="Short summary of the data request"/></label>
            <label className="form-field">Request type<select value={form.request_type} onChange={e=>setForm({...form,request_type:e.target.value,requested_dataset_id:e.target.value==='New dataset'?'':form.requested_dataset_id})}>{['New dataset','Change to existing dataset','Additional records for existing dataset'].map(x=><option key={x}>{x}</option>)}</select></label>
            <label className="form-field">Application / system<input value={form.application} onChange={e=>setForm({...form,application:e.target.value})} placeholder="Application name"/></label>
            {form.request_type!=='New dataset'&&<label className="form-field wide">Published data set<select value={form.requested_dataset_id} disabled={requestDatasetsLoading} onChange={e=>setForm({...form,requested_dataset_id:e.target.value})}><option value="">{requestDatasetsLoading?'Loading published data sets…':'Select a published data set'}</option>{requestDatasets.map(dataset=><option key={dataset.id} value={dataset.id}>{dataset.name} · {dataset.reference_code}</option>)}</select>{!requestDatasetsLoading&&!requestDatasets.length&&<small>No published data sets are available in this workspace.</small>}</label>}
            <div className="ticket-request-three-field-row"><label className="form-field">Project ID<input value={form.project_id} onChange={e=>setForm({...form,project_id:e.target.value})}/></label><label className="form-field">Project name<input value={form.project_name} onChange={e=>setForm({...form,project_name:e.target.value})}/></label><label className="form-field">Environment<input value={form.environment} onChange={e=>setForm({...form,environment:e.target.value})}/></label></div>
          </div></section>
          <section className="ticket-request-flow-section" id="ticket-request-step-1" data-request-step="1"><div className="ticket-request-stage-heading"><span>STEP 2 OF 4</span><h3>Data &amp; delivery</h3><p>Set the data volume, source preference, priority, and dates.</p></div><div className="ticket-form-grid ticket-request-stage-fields">
            <div className="ticket-request-three-field-row"><label className="form-field">Record count<input value={form.record_count} type="number" min="1" onChange={e=>setForm({...form,record_count:e.target.value})}/></label><label className="form-field">Data source<select value={form.data_source_preference} onChange={e=>setForm({...form,data_source_preference:e.target.value})}>{['Source-backed','Synthetic'].map(x=><option key={x}>{x}</option>)}</select></label><label className="form-field">Priority<select value={form.priority} onChange={e=>setForm({...form,priority:e.target.value})}>{['Urgent','Normal','Low'].map(x=><option key={x}>{x}</option>)}</select></label></div>
            {form.priority==='Urgent'&&<label className="form-field wide">Urgent justification<input value={form.urgent_justification} onChange={e=>setForm({...form,urgent_justification:e.target.value})} placeholder="Explain the time-critical need"/></label>}
            <div className="ticket-request-date-block"><div><strong>Schedule</strong><small>When you need the data and how long it is required.</small></div><div className="ticket-request-date-fields"><label className="form-field">Needed by<input value={form.needed_by} type="date" onChange={e=>setForm({...form,needed_by:e.target.value})}/></label><label className="form-field">Required until<input value={form.required_until} type="date" onChange={e=>setForm({...form,required_until:e.target.value})}/></label></div></div>
          </div></section>
          <section className="ticket-request-flow-section" id="ticket-request-step-2" data-request-step="2"><div className="ticket-request-stage-heading"><span>STEP 3 OF 4</span><h3>Scope &amp; handling</h3><p>Describe the objective, scenarios, relationships, and masking needs.</p></div><div className="ticket-form-grid ticket-request-stage-fields">
            <label className="form-field wide">Objective<textarea rows={4} value={form.description} onChange={e=>setForm({...form,description:e.target.value})} placeholder="What should the test data help validate?"/></label>
            <label className="form-field wide">Scenarios and expected conditions<textarea rows={5} value={form.scenario} onChange={e=>setForm({...form,scenario:e.target.value})} placeholder="One scenario per line"/></label>
            <label className="form-field">Data relationships<textarea rows={3} value={form.relationships} onChange={e=>setForm({...form,relationships:e.target.value})} placeholder="Describe related records or dependencies"/></label>
            <label className="form-field">PII / masking needs<textarea rows={3} value={form.masking_needs} onChange={e=>setForm({...form,masking_needs:e.target.value})} placeholder="Describe the approved masking policy; do not enter real sensitive values."/></label>
            <label className="form-field wide">Provide SQL (if any)<textarea rows={2} value={form.acceptance_criteria} onChange={e=>setForm({...form,acceptance_criteria:e.target.value})} placeholder="Enter the SQL query, if available"/></label>
          </div></section>
          <section className="ticket-request-flow-section" id="ticket-request-step-3" data-request-step="3"><div className="ticket-request-stage-heading"><span>STEP 4 OF 4</span><h3>Review request</h3><p>Review the details before saving and leaving your draft.</p></div><div className="ticket-request-review"><section className="ticket-request-review-card"><header><strong>Request details</strong></header><dl><div><dt>Title</dt><dd>{form.title||'—'}</dd></div><div><dt>Request type</dt><dd>{form.request_type}</dd></div>{form.request_type!=='New dataset'&&<div><dt>Published data set</dt><dd>{selectedRequestDataset?`${selectedRequestDataset.name} · ${selectedRequestDataset.reference_code}`:form.requested_dataset_id||'Not selected'}</dd></div>}<div><dt>Application / system</dt><dd>{form.application||'—'}</dd></div><div><dt>Environment</dt><dd>{form.environment||'—'}</dd></div><div><dt>Project</dt><dd>{form.project_name||'—'}{form.project_id?` · ${form.project_id}`:''}</dd></div></dl></section>
            <section className="ticket-request-review-card"><header><strong>Data &amp; delivery</strong></header><dl><div><dt>Record count</dt><dd>{form.record_count||'—'}</dd></div><div><dt>Data source</dt><dd>{form.data_source_preference}</dd></div><div><dt>Priority</dt><dd>{form.priority}</dd></div>{form.priority==='Urgent'&&<div><dt>Urgent justification</dt><dd>{form.urgent_justification||'—'}</dd></div>}<div><dt>Needed by</dt><dd>{form.needed_by||'—'}</dd></div><div><dt>Required until</dt><dd>{form.required_until||'—'}</dd></div></dl></section>
            <section className="ticket-request-review-card ticket-request-review-wide"><header><strong>Objective &amp; scenarios</strong></header><dl><div><dt>Objective</dt><dd>{form.description||'—'}</dd></div><div><dt>Scenarios</dt><dd>{form.scenario||'—'}</dd></div><div><dt>Data relationships</dt><dd>{form.relationships||'—'}</dd></div><div><dt>PII / masking needs</dt><dd>{form.masking_needs||'—'}</dd></div><div><dt>Provide SQL (if any)</dt><dd>{form.acceptance_criteria||'—'}</dd></div></dl></section>
          </div></section>
        </section>
      </div><div className="ticket-request-wizard-actions"><span className="ticket-request-save-status" role="status">{draftSaveState==='saving'?'Saving draft…':draftSaveState==='saved'?'Draft saved automatically':draftSaveState==='error'?'Draft not saved':!form.title.trim()?'Enter a title to save automatically':'Scroll through the sections to complete your request'}</span><div><button type="button" className="button button-secondary" onClick={()=>{setCreateOpen(false);setEditingDraftId(null);setEditingRowVersion(null)}}>Discard</button><button type="submit" className="button button-confirm" disabled={draftSaveState==='saving'}>Save draft &amp; exit</button>{editingDraftId&&<button type="button" className="button button-primary" disabled={draftSaveState==='saving'} onClick={()=>void submitDraftRequest()}>Submit request</button>}</div></div>
    </form>}
    {selected && <article className="ticket-detail"><div className="ticket-detail-heading"><div><button className="text-button" onClick={()=>setSelected(null)}>← All tickets</button><h2>{selected.display_id} · {selected.title}</h2><p className="ticket-detail-meta"><span>{selected.project_name||'Workspace'} · Created {stamp(selected.submitted_at||selected.updated_at)}</span><span className={`ticket-status status-${selected.status.toLowerCase().replaceAll(' ','-')}`}>{selected.status}</span></p></div>
      {selected.dataset?<div className="ticket-dataset ticket-dataset-corner"><h3>Linked dataset</h3><strong>{selected.dataset.name||selected.dataset.reference_code}</strong><p>{selected.dataset.reference_code||'—'}</p><span className={`status-badge dataset-version-status lifecycle-${selected.dataset.state.toLowerCase().replaceAll(' ','-')}`}><span>Version {selected.dataset.version_number??'—'}</span><span className="dataset-version-separator" aria-hidden="true">·</span><span>{selected.dataset.state}</span></span>{selected.fulfillment_summary&&<p>{selected.fulfillment_summary}</p>}<Link to={`/datasets?dataset_id=${encodeURIComponent(selected.linked_dataset_id||'')}${selected.dataset.version_number?`&version_number=${selected.dataset.version_number}`:''}`}>Open linked data set</Link></div>:selected.status==='Closed'&&!selected.linked_dataset_id&&<div className="ticket-dataset ticket-dataset-corner ticket-dataset-unassigned"><h3>Linked dataset</h3><strong>No data set assigned</strong><p className="ticket-dataset-closed">Ticket is closed</p><p>Please check comments for further details.</p></div>}
    </div>
      <div className="ticket-detail-topline"><h3>Request details</h3><div className="ticket-actionbar">{selected.status==='Draft'&&role==='QA Engineer'&&<><button type="button" className="button button-secondary" onClick={()=>editDraft()}>Edit draft</button><button type="button" className="button button-primary" onClick={()=>void act('submit')}>Submit request</button></>}
        {['Submitted','On Hold'].includes(selected.status)&&isAssignedEngineer&&<button type="button" className="button button-primary" onClick={()=>void act(selected.status==='On Hold'?'resume':'start')}>{selected.status==='On Hold'?'Resume work':'Acknowledge and start'}</button>}
        {['Submitted','In Progress'].includes(selected.status)&&isAssignedEngineer&&<button type="button" className="button button-secondary" onClick={()=>showActionDialog('request-clarification','Request clarification','Send request',[{name:'reason',label:'Clarification question',placeholder:'Describe the information needed',multiline:true}])}>Request clarification</button>}
        {['Submitted','In Progress','On Hold'].includes(selected.status)&&role==='Admin'&&<button type="button" className="button button-secondary" onClick={()=>void showAssignDialog()}>Assign ticket</button>}
        {selected.status==='In Progress'&&isAssignedEngineer&&<button type="button" className="button button-primary" onClick={()=>void openShareDialog()}>Share data set</button>}
        {selected.status==='Closed'&&isAssignedEngineer&&<button type="button" className="button button-secondary" onClick={()=>showActionDialog('reopen','Reopen ticket','Reopen ticket',[{name:'reason',label:'Reason for reopening',placeholder:'Explain why this ticket needs to be reopened',multiline:true}])}>Reopen</button>}
        {((role==='QA Engineer'&&['Draft','Submitted'].includes(selected.status))||(isAssignedEngineer&&['Submitted','In Progress','On Hold'].includes(selected.status)))&&<button type="button" className="button button-secondary" onClick={()=>showActionDialog('cancel','Cancel request','Cancel request',[{name:'reason',label:'Cancellation reason',placeholder:'Explain why this request is being cancelled',multiline:true}])}>Cancel request</button>}
      </div></div>
      <div className="ticket-detail-grid"><section className="ticket-info"><section className="ticket-detail-group"><h4>Ticket overview</h4><dl className="ticket-detail-fields">
          <div className="ticket-detail-field"><dt>Workspace</dt><dd>{selected.workspace_name||workspaceName||'—'}</dd></div>
          <div className="ticket-detail-field"><dt>Requester</dt><dd>{selected.requester_name}</dd></div>
          <div className="ticket-detail-field"><dt>Assignee</dt><dd>{selected.assignee_name||'Unassigned'}</dd></div>
          <div className="ticket-detail-field"><dt>Priority</dt><dd><span className={`ticket-priority priority-${selected.priority.toLowerCase()}`}>{selected.priority}</span></dd></div>
          <div className="ticket-detail-field"><dt>Needed by</dt><dd>{stamp(selected.needed_by)}</dd></div>
          <div className="ticket-detail-field"><dt>Open age / duration</dt><dd><span className={`ticket-age ${ageTone(selected.current_age_seconds??selected.final_duration_seconds)}`}>{age(selected.current_age_seconds??selected.final_duration_seconds)}</span></dd></div>
        </dl></section>
        <section className="ticket-detail-group"><h4>Data requirements</h4><dl className="ticket-detail-fields">
          <div className="ticket-detail-field"><dt>Request type</dt><dd>{selected.request_type||'—'}</dd></div>
          <div className="ticket-detail-field"><dt>Environment</dt><dd>{selected.environment||'—'}</dd></div>
          <div className="ticket-detail-field"><dt>Application</dt><dd>{selected.application||'—'}</dd></div>
          {selected.requested_dataset&&<div className="ticket-detail-field field-wide"><dt>Existing data set</dt><dd>{selected.requested_dataset.name} · {selected.requested_dataset.reference_code}</dd></div>}
          <div className="ticket-detail-field"><dt>Record count</dt><dd>{selected.record_count??'—'}</dd></div>
          <div className="ticket-detail-paired-fields"><div className="ticket-detail-field"><dt>Data source</dt><dd>{selected.data_source_preference||'—'}</dd></div><div className="ticket-detail-field"><dt>Required until</dt><dd>{selected.required_until||selected.data_date_to||'—'}</dd></div></div>
          <div className="ticket-detail-field field-wide"><dt>Objective</dt><dd>{selected.description||'—'}</dd></div>
          <div className="ticket-detail-field field-wide"><dt>Scenarios</dt><dd>{selected.scenarios?.join(', ')||'—'}</dd></div>
          <div className="ticket-detail-field"><dt>Relationships</dt><dd>{selected.relationships||'—'}</dd></div>
          <div className="ticket-detail-field"><dt>PII / masking needs</dt><dd>{selected.masking_needs||'—'}</dd></div>
          <div className="ticket-detail-field field-wide ticket-sql-field"><dt>Provide SQL (if any)</dt><dd>{selected.acceptance_criteria||'—'}</dd></div>
        </dl></section>
        </section>
        <section className="ticket-history"><h3>Activity & comments</h3><ol ref={activityListRef}>{timeline.map(entry=><li className={`timeline-${entry.kind}`} key={`${entry.kind}-${entry.id}`}><div className="ticket-history-event-heading"><b>{entry.title}</b><time>{stamp(entry.when)}</time></div><p><strong>{entry.actor}</strong>{entry.role&&<span className="ticket-history-role"> · {entry.role}</span>}{entry.detail&&<span className={`ticket-history-detail${entry.kind==='comment'||(entry.kind==='event'&&entry.title!=='Status changed'&&entry.title!=='Assignee changed')?' ticket-history-detail-next-line':''}`}>{entry.kind==='event'&&(entry.title==='Status changed'||entry.title==='Assignee changed')?' · ':' '}{entry.detail}</span>}</p></li>)}</ol>
          <form onSubmit={addComment}><textarea className="ticket-comment-field" aria-label="Add a comment" value={comment} onChange={e=>setComment(e.target.value)} disabled={Boolean(commentDisabledReason)} placeholder="Add a comment or clarification response"/><button className={`button ticket-comment-submit${Boolean(commentDisabledReason)?' ticket-comment-disabled':''}`} disabled={!comment.trim()||Boolean(commentDisabledReason)} title={commentDisabledReason||undefined}>Add comment</button></form>
        </section></div>
    </article>}
    {shareDialogOpen&&<Modal title={noDatasetSelected?'Close ticket without a data set':'Share published data set and close'} onClose={()=>setShareDialogOpen(false)} footer={<><button type="button" className="button button-secondary" onClick={()=>setShareDialogOpen(false)}>Cancel</button><button type="submit" form="ticket-share-form" className="button button-confirm" disabled={shareOptionsLoading||!shareForm.dataset_id||(!noDatasetSelected&&!shareForm.shared_with_user_id)||!shareForm.fulfillment_summary.trim()}>{noDatasetSelected?'Close ticket':'Share and close'}</button></>}>
      <form id="ticket-share-form" className="ticket-action-form" onSubmit={submitShareDialog}>
        <label className="form-field">Published data set<select required value={shareForm.dataset_id} disabled={shareOptionsLoading} onChange={event=>setShareForm({...shareForm,dataset_id:event.target.value})}><option value={NO_DATASET}>No data set assigned</option><option value="__dataset_divider__" disabled>----------------------</option>{shareOptions?.datasets.map(dataset=><option key={dataset.id} value={dataset.id}>{dataset.name} · {dataset.reference_code}</option>)}</select></label>
        {!noDatasetSelected&&<label className="form-field">Share with<select required value={shareForm.shared_with_user_id} disabled={shareOptionsLoading||!shareOptions?.users.length} onChange={event=>setShareForm({...shareForm,shared_with_user_id:event.target.value})}><option value="">{shareOptionsLoading?'Loading users…':'Select a user'}</option>{shareOptions?.users.map(user=><option key={user.subject} value={user.subject}>{user.display_name}{user.email&&user.email!==user.display_name?` · ${user.email}`:''}</option>)}</select></label>}
        {!noDatasetSelected&&!shareOptionsLoading&&shareOptions&&!shareOptions.datasets.length&&<p className="ticket-share-empty">No eligible published data sets are available. Choose “No data set assigned -” to close without one.</p>}
        {noDatasetSelected&&<p className="ticket-share-empty">The ticket will be closed without sharing or linking a data set.</p>}
        <label className="form-field">Fulfillment summary<textarea required rows={4} placeholder="Summarize what was fulfilled and shared" value={shareForm.fulfillment_summary} onChange={event=>setShareForm({...shareForm,fulfillment_summary:event.target.value})}/></label>
        {error&&<div className="alert alert-warning" role="alert">{error}</div>}
      </form>
    </Modal>}
    {actionDialog&&<Modal title={actionDialog.title} onClose={()=>setActionDialog(null)} footer={<><button type="button" className="button button-secondary" onClick={()=>setActionDialog(null)}>Cancel</button><button type="submit" form="ticket-action-form" className="button button-confirm" disabled={actionDialog.action==='assign'&&!actionDialog.fields[0].options?.length}>{actionDialog.confirmLabel}</button></>}>
      <form id="ticket-action-form" className="ticket-action-form" onSubmit={submitActionDialog}>{actionDialog.fields.map(field=><label className="form-field" key={field.name}>{field.label}{field.options?<select required autoFocus={actionDialog.fields[0].name===field.name} value={actionDialog.values[field.name]} onChange={event=>setActionDialog({...actionDialog,values:{...actionDialog.values,[field.name]:event.target.value}})}><option value="">Select a TDE</option>{field.options.map(option=><option key={option.value} value={option.value}>{option.label}</option>)}</select>:field.multiline?<textarea required autoFocus rows={4} placeholder={field.placeholder} value={actionDialog.values[field.name]} onChange={event=>setActionDialog({...actionDialog,values:{...actionDialog.values,[field.name]:event.target.value}})}/>:<input required autoFocus={actionDialog.fields[0].name===field.name} placeholder={field.placeholder} value={actionDialog.values[field.name]} onChange={event=>setActionDialog({...actionDialog,values:{...actionDialog.values,[field.name]:event.target.value}})}/>}</label>)}{actionDialog.action==='assign'&&!actionDialog.fields[0].options?.length&&<p className="ticket-share-empty">No active TDE users are available in this workspace.</p>}{error&&<div className="alert alert-warning" role="alert">{error}</div>}</form>
    </Modal>}
  </section>
}
