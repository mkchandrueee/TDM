import { Component, isValidElement, useCallback, useContext, useEffect, useRef, useState, type ErrorInfo, type FormEvent, type ReactNode } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Activity, ArrowRight, CalendarCheck2, CalendarPlus, Check, Code, Copy, Database, Download, Eye, FilePlus2, GitBranch, KeyRound, ListFilter, LockKeyhole, Pencil, Plus, RefreshCw, Save, Search, ShieldCheck, Sparkles, X } from 'lucide-react'
import { apiDownload, apiRequest, type HumanRole } from './api'
import { creationPathwayLabel, generationModeClass, generationModeLabel, SCENARIO_TO_DATA_LABEL } from './datasetLabels'
import { DatasetActivityIcons, DatasetUsageContext, type DatasetUsageStatus } from './DatasetActivity'
import DownloadReviewDialog from './DownloadReviewDialog'
import './dataset-federated.css'
import DatasetRefreshButton from './DatasetRefreshButton'

type Lifecycle = 'draft'|'ready'|'published'|'failed'|'quarantined'|'expired'
type DataSetVersion = {id:string;dataset_id:string;workspace_id:string;version_number:number;lifecycle_state:Lifecycle;schema_scope:Array<{field:string;data_type:string;entity?:string|null;source_field?:string|null;json_path?:string|null;required:boolean}>;fields:Array<{id:string;field_name:string;field_path:string;data_type:string;required:boolean;target_field_path:string|null;constraint_spec:Record<string,unknown>;ordinal:number}>;selected_fields:string[];generation_settings:Record<string,string|number>;record_count:number|null;provenance:Record<string,any>;privacy_state:string;validation_state:string;publication_state:string;expiry_at:string|null;permitted_use:string[];validation_evidence:Record<string,string|number|boolean|null>;published_at:string|null;expired_at:string|null;created_by:string;created_at:string}
type DataSetSummary = {id:string;reference_code:string;workspace_id:string;name:string;description:string|null;owner_id:string;creation_pathway:'scenario_to_data'|'source_to_data'|'self_servicing_data';visibility?:'private'|'public'|null;source_connection_name?:string|null;federated_plan_name?:string|null;latest_version:DataSetVersion;created_at:string;updated_at:string}
type LineageEvent = {id:string;dataset_id:string;version_id:string;version_number:number;sequence_number:number;event_type:string;stage:string;source_reference:string|null;artifact_reference:string|null;mapping_version:string|null;scenario_version:string|null;job_id:string|null;policy_version:string|null;validation_version:string|null;actor_id:string;actor_type:string;occurred_at:string;outcome:string;safe_evidence:Record<string,string|number|boolean|null>;parent_event_ids:string[];child_event_ids:string[]}
type DatasetDetail = {data_set:DataSetSummary;selected_version:DataSetVersion;versions:DataSetVersion[];test_data_requirements:string|null;source_sql:string|null;masked_fields:string[];entity_key_fields:string[];lineage:LineageEvent[];blocked_record_count:number;identity_unavailable_record_count:number;identity_unavailable_guidance:string|null;failure_message:string|null;status_history:Array<{from_status:string|null;to_status:string;changed_by:string;changed_at:string;reason:string|null}>;validation_runs:Array<{id:string;validation_type:string;status:string;started_at:string;completed_at:string|null;summary:Record<string,unknown>;results:Array<{check_code:string;severity:string;status:string;field_path:string|null;safe_message:string;observed_count:number|null}>}>;access:{owner_id:string;permitted_use:string[];workspace_id:string};sample_records:Array<Record<string,string|number|null>>}
type DownloadAccess = {can_download:boolean;record_count:number;available_record_count:number;blocked_record_count:number;reservation_active:boolean;lock_active:boolean}
type DatasetDashboardSummary = {draft_count:number;ready_count:number;published_count:number;quarantined_count:number;reserved_count:number;active_lock_count:number}
type DownloadReview = {datasetId:string;datasetName:string;referenceCode:string;versionNumber:number;format:'csv'|'json';access:DownloadAccess}
type Tab = 'overview'|'lineage'|'validation'|'access'
const stateLabel:Record<Lifecycle,string>={draft:'Draft',ready:'Ready',published:'Published',failed:'Failed',quarantined:'Quarantined',expired:'Expired'}
function workflowPathFor(detail:DatasetDetail){return detail.data_set.creation_pathway==='source_to_data'?(detail.selected_version.provenance.source_query_id?'/sql-to-data':'/source-to-data'):'/scenario-to-data'}
function creationPathwaySourceLabel(dataset:DataSetSummary,version:DataSetVersion){
  const pathway=dataset.creation_pathway==='scenario_to_data'?SCENARIO_TO_DATA_LABEL:creationPathwayLabel(dataset.creation_pathway)
  if(dataset.creation_pathway==='self_servicing_data')return `${pathway} · Source: Query/${dataset.visibility==='public'?'Public':'Private'}`
  if(version.provenance.origin==='synthetic')return `${pathway} · Synthetic`
  if(dataset.federated_plan_name)return `${pathway} · Federated: ${dataset.federated_plan_name}`
  if(dataset.source_connection_name)return `${pathway} · Source: ${dataset.source_connection_name}`
  return pathway
}

class DatasetModalBoundary extends Component<{children:ReactNode;onClose:()=>void},{failed:boolean;message:string}>{
  state={failed:false,message:''}
  static getDerivedStateFromError(error:Error){return {failed:true,message:error.message||'Unknown rendering error'}}
  componentDidCatch(error:Error,info:ErrorInfo){console.error('Data set details failed to render',error,info.componentStack)}
  render(){
    if(this.state.failed)return <div className="dataset-overlay"><section className="dataset-modal dataset-detail-modal" role="alertdialog" aria-modal="true" aria-label="Data set details unavailable"><header className="dataset-modal-header"><div><div className="eyebrow">DATA SET DETAILS</div><h2>Unable to display this data set</h2><p>The details view encountered a display error.</p></div><button type="button" className="icon-button" aria-label="Close data set details" onClick={this.props.onClose}><X size={18}/></button></header><div className="dataset-detail-content"><div className="alert alert-warning">{this.state.message}</div></div><footer className="dataset-modal-footer"><span>Close this message to return to the catalog.</span><button type="button" className="button button-secondary" onClick={this.props.onClose}>Close</button></footer></section></div>
    return this.props.children
  }
}

export default function DataSetsPage({role,workspaceId,piiMaskingEnabled}:{role:HumanRole;workspaceId:string;piiMaskingEnabled:boolean}){
  const navigate=useNavigate()
  const [searchParams]=useSearchParams()
  const requestedDatasetId=searchParams.get('dataset_id'),requestedVersion=searchParams.get('version_number')
  const [items,setItems]=useState<DataSetSummary[]>([])
  const [workspaceTotal,setWorkspaceTotal]=useState(0)
  const [loading,setLoading]=useState(false)
  const [error,setError]=useState('')
  const [search,setSearch]=useState('')
  const [query,setQuery]=useState('')
  const [state,setState]=useState('')
  const [pathway,setPathway]=useState('')
  const [filterOpen,setFilterOpen]=useState(false)
  const [refreshToken,setRefreshToken]=useState(0)
  const [createdFrom,setCreatedFrom]=useState('')
  const [updatedFrom,setUpdatedFrom]=useState(''),[updatedTo,setUpdatedTo]=useState('')
  const [generationMode,setGenerationMode]=useState('')
  const [draftCreatedFrom,setDraftCreatedFrom]=useState('')
  const [draftUpdatedFrom,setDraftUpdatedFrom]=useState(''),[draftUpdatedTo,setDraftUpdatedTo]=useState('')
  const [draftGenerationMode,setDraftGenerationMode]=useState('')
  const [showCreate,setShowCreate]=useState(false)
  const [selection,setSelection]=useState<{id:string;version?:number;tab:Tab}|null>(null)
  const [editSource,setEditSource]=useState<DataSetVersion|null>(null)
  const [detail,setDetail]=useState<DatasetDetail|null>(null)
  const [detailError,setDetailError]=useState('')
  const [canManage,setCanManage]=useState(role==='Test Data Engineer')
  const datasetUsage=useContext(DatasetUsageContext)
  const [downloadReview,setDownloadReview]=useState<DownloadReview|null>(null)
  const [downloadBusy,setDownloadBusy]=useState(false)
  const [dashboardSummary,setDashboardSummary]=useState<DatasetDashboardSummary|null>(null)
  const [workspaceDatasetUsage,setWorkspaceDatasetUsage]=useState<Record<string,DatasetUsageStatus>>({})
  const [workspaceNames,setWorkspaceNames]=useState<Record<string,string>>({})

  const refreshWorkspaceDatasetUsage=useCallback(async()=>{
    if(!workspaceId||!(role==='Test Data Engineer'||role==='Admin')){setWorkspaceDatasetUsage({});return}
    try{
      const usage=await apiRequest<DatasetUsageStatus[]>(`/workspaces/${workspaceId}/active-dataset-usage`,role)
      setWorkspaceDatasetUsage(Object.fromEntries(usage.map(status=>[status.dataset_id,status])))
    }catch{/* Keep the last known status during a transient refresh failure. */}
  },[workspaceId,role])

  const load=useCallback(async()=>{
    if(!workspaceId){setItems([]);return}
    setLoading(true);setError('')
    const params=new URLSearchParams({page:'1',page_size:'100'})
    const totalParams=new URLSearchParams({page:'1',page_size:'1'})
    if(role!=='Admin'){params.set('workspace_id',workspaceId);totalParams.set('workspace_id',workspaceId)}
    if(query.trim())params.set('search',query.trim())
    if(state&&role!=='QA Engineer')params.set('lifecycle_state',state)
    if(pathway)params.set('creation_pathway',pathway)
    try{const [result,allDataSets]=await Promise.all([apiRequest<{items:DataSetSummary[];total:number;has_more?:boolean}>(`/datasets?${params}`,role),apiRequest<{items:DataSetSummary[];total:number}>(`/datasets?${totalParams}`,role)]);let allItems=result.items;if(role==='Admin'){let page=1,hasMore=Boolean(result.has_more);while(hasMore){page+=1;const nextParams=new URLSearchParams(params);nextParams.set('page',String(page));const next=await apiRequest<{items:DataSetSummary[];has_more?:boolean}>(`/datasets?${nextParams}`,role);allItems=allItems.concat(next.items);hasMore=Boolean(next.has_more)}try{const workspaces=await apiRequest<Array<{id:string;name:string}>>('/workspaces',role);setWorkspaceNames(Object.fromEntries(workspaces.map(workspace=>[workspace.id,workspace.name])))}catch{setWorkspaceNames({})}}setItems(allItems);setWorkspaceTotal(allDataSets.total);if(role==='Test Data Engineer'){try{setDashboardSummary(await apiRequest<DatasetDashboardSummary>(`/workspaces/${workspaceId}/dataset-dashboard-summary`,role))}catch(cause){setDashboardSummary(null);setError(cause instanceof Error?cause.message:'Unable to load workspace data set summary')}}else setDashboardSummary(null)}
    catch(cause){setError(cause instanceof Error?cause.message:'Unable to load data sets')}
    finally{setLoading(false)}
  },[workspaceId,role,query,state,pathway])
  useEffect(()=>{void load()},[load,refreshToken])
  useEffect(()=>{
    setWorkspaceDatasetUsage({})
    void refreshWorkspaceDatasetUsage()
    if(!(role==='Test Data Engineer'||role==='Admin'))return
    const timer=window.setInterval(()=>void refreshWorkspaceDatasetUsage(),5000)
    return()=>window.clearInterval(timer)
  },[refreshWorkspaceDatasetUsage,role])
  useEffect(()=>{setCanManage(role==='Test Data Engineer')},[role])
  useEffect(()=>{
    if(!requestedDatasetId||selection)return
    const versionNumber=Number(requestedVersion)
    setSelection({id:requestedDatasetId,version:Number.isInteger(versionNumber)&&versionNumber>0?versionNumber:undefined,tab:'overview'})
    navigate('/datasets',{replace:true})
  },[requestedDatasetId,requestedVersion,selection,navigate])

  useEffect(()=>{
    if(!selection){setDetail(null);return}
    let active=true
    setDetail(null);setDetailError('')
    const version=selection.version?`?version_number=${selection.version}`:''
    // Dataset SQL may be added to the detail response after a generation job
    // finishes. Avoid reusing a cached pre-generation detail payload when the
    // user opens the same version again.
    apiRequest<DatasetDetail>(`/datasets/${selection.id}${version}`,role,{cache:'no-store'})
      .then(value=>{if(active){setDetail(value);if(!selection.version)setSelection(current=>current?.id===selection.id?{...current,version:value.selected_version.version_number}:current)}})
      .catch(cause=>{if(active)setDetailError(cause instanceof Error?cause.message:'Unable to load data set details')})
    return()=>{active=false}
  },[selection?.id,selection?.version,role])

  useEffect(()=>{
    if(detail?.data_set.creation_pathway!=='self_servicing_data'||!selection)return
    let active=true
    const recheck=()=>apiRequest<DatasetDetail>(`/datasets/${selection.id}?version_number=${detail.selected_version.version_number}`,role,{cache:'no-store'})
      .then(value=>{if(active)setDetail(value)})
      .catch(cause=>{if(active){setDetail(null);setSelection(null);setDownloadReview(null);setError(cause instanceof Error?cause.message:'Data set access changed');void load()}})
    const timer=window.setInterval(()=>void recheck(),5000)
    const focus=()=>void recheck()
    window.addEventListener('focus',focus)
    return()=>{active=false;window.clearInterval(timer);window.removeEventListener('focus',focus)}
  },[detail?.data_set.id,detail?.selected_version.version_number,selection?.id,role,load])
  useEffect(()=>{
    const timer=window.setInterval(()=>void load(),15000)
    return()=>window.clearInterval(timer)
  },[load])

  function open(item:DataSetSummary,tab:Tab='overview'){setSelection({id:item.id,version:item.latest_version.version_number,tab})}
  function handleExpiryUpdated(updated:DataSetVersion){setDetail(current=>current?withUpdatedVersion(current,updated):current);void load()}
  async function publishCurrent(){
    if(!detail||!selection)return
    setDetailError('')
    try{await apiRequest(`/datasets/${detail.data_set.id}/versions/${detail.selected_version.version_number}/publish`,role,{method:'POST'});await load();setDetail(await apiRequest<DatasetDetail>(`/datasets/${detail.data_set.id}?version_number=${detail.selected_version.version_number}`,role,{cache:'no-store'}))}
    catch(cause){setDetailError(cause instanceof Error?cause.message:'Unable to publish this version')}
  }
  async function createEditedVersion(versionNumber:number,editPlanId?:string,sourceWizardEdit=false){
    if(!selection)return
    const datasetId=selection.id
    const pathway=detail?workflowPathFor(detail).slice(1):'scenario-to-data'
    setEditSource(null)
    await load()
    setSelection(null)
    navigate(sourceWizardEdit
      ?`/${pathway}?edit_dataset_id=${encodeURIComponent(datasetId)}&base_version_number=${versionNumber}`
      :editPlanId
      ?`/${pathway}?edit_plan_id=${encodeURIComponent(editPlanId)}&edit_dataset_id=${encodeURIComponent(datasetId)}&base_version_number=${versionNumber}`
      :`/${pathway}?draft_id=${encodeURIComponent(datasetId)}&version_number=${versionNumber}`)
  }
  async function reviewDownload(datasetId:string,datasetName:string,referenceCode:string,version:DataSetVersion){
    setError('');setDetailError('')
    try{
      const access=await apiRequest<DownloadAccess>(`/datasets/${datasetId}/versions/${version.version_number}/download-access`,role)
      const review={datasetId,datasetName,referenceCode,versionNumber:version.version_number,format:artifactFormat(version),access}
      if(access.blocked_record_count>0){setDownloadReview(review);return}
      if(!access.can_download){setDownloadReview(review);return}
      setDownloadBusy(true)
      const download=await apiDownload(`/datasets/${datasetId}/versions/${version.version_number}/artifact?format=${review.format}`,role)
      saveDownload(download.blob,download.filename??`${referenceCode}.${review.format}`)
    }catch(cause){const message=cause instanceof Error?cause.message:'Unable to check download access';if(selection?.id===datasetId)setDetailError(message);else setError(message)}
    finally{setDownloadBusy(false)}
  }
  async function downloadCurrent(){
    if(!detail)return
    await reviewDownload(detail.data_set.id,detail.data_set.name,detail.data_set.reference_code,detail.selected_version)
  }
  async function downloadRow(item:DataSetSummary){
    await reviewDownload(item.id,item.name,item.reference_code,item.latest_version)
  }
  async function confirmDownload(){
    if(!downloadReview||!downloadReview.access.can_download)return
    setDownloadBusy(true)
    try{
      const download=await apiDownload(`/datasets/${downloadReview.datasetId}/versions/${downloadReview.versionNumber}/artifact?format=${downloadReview.format}`,role)
      saveDownload(download.blob,download.filename??`${downloadReview.referenceCode}.${downloadReview.format}`)
      setDownloadReview(null)
    }catch(cause){setError(cause instanceof Error?cause.message:'Unable to download this data set')}
    finally{setDownloadBusy(false)}
  }
  function openFilters(){setDraftCreatedFrom(createdFrom);setDraftUpdatedFrom(updatedFrom);setDraftUpdatedTo(updatedTo);setDraftGenerationMode(generationMode);setFilterOpen(true)}
  function applyFilters(){setQuery(search);setCreatedFrom(draftCreatedFrom);setUpdatedFrom(draftUpdatedFrom);setUpdatedTo(draftUpdatedTo);setGenerationMode(draftGenerationMode);setFilterOpen(false)}
  function clearFilters(){setSearch('');setQuery('');setState('');setPathway('');setCreatedFrom('');setDraftCreatedFrom('');setUpdatedFrom('');setDraftUpdatedFrom('');setUpdatedTo('');setDraftUpdatedTo('');setGenerationMode('');setDraftGenerationMode('');setFilterOpen(false)}
  function refreshAndClearFilters(){clearFilters();setRefreshToken(value=>value+1)}
  const dateOnly=(value:string)=>new Date(value).toISOString().slice(0,10)
  const visibleItems=items.filter(item=>{
    const created=dateOnly(item.created_at),updated=dateOnly(item.updated_at)
    const mode=generationModeLabel(item.latest_version.provenance,item.creation_pathway)
    return (!createdFrom||created>=createdFrom)&&(!updatedFrom||updated>=updatedFrom)&&(!updatedTo||updated<=updatedTo)&&(!generationMode||mode===generationMode)
  })
  const publishedCount=visibleItems.filter(item=>item.latest_version.lifecycle_state==='published').length
  const syntheticCount=visibleItems.filter(item=>generationModeLabel(item.latest_version.provenance,item.creation_pathway)==='Synthetic').length
  const sourceBackedCount=visibleItems.filter(item=>['Source-backed','Self-service Query'].includes(generationModeLabel(item.latest_version.provenance,item.creation_pathway))).length
  const reservedCount=visibleItems.filter(item=>datasetUsage[item.id]?.reservation_active).length
  const activeLockCount=visibleItems.filter(item=>datasetUsage[item.id]?.lock_active).length
  if(!workspaceId)return <section className="panel dataset-empty"><Database size={24}/><h2>Select a workspace</h2><p>Data sets are scoped to a workspace. Select one in the top bar to browse its catalog.</p></section>
  return <div className="datasets-page" data-pii-masking-enabled={piiMaskingEnabled} data-role={role}>
    {role==='Test Data Engineer'?<section className="dataset-summary-row dataset-summary-row-six"><div className="dataset-summary-card"><span><FilePlus2 size={17}/></span><div><strong>{dashboardSummary?.draft_count??'—'}</strong><small>Draft</small></div></div><div className="dataset-summary-card"><span><Check size={17}/></span><div><strong>{dashboardSummary?.ready_count??'—'}</strong><small>Ready</small></div></div><div className="dataset-summary-card"><span><Database size={17}/></span><div><strong>{dashboardSummary?.published_count??'—'}</strong><small>Published</small></div></div><div className="dataset-summary-card"><span><ShieldCheck size={17}/></span><div><strong>{dashboardSummary?.quarantined_count??'—'}</strong><small>Quarantined</small></div></div><div className="dataset-summary-card"><span><CalendarCheck2 size={17}/></span><div><strong>{dashboardSummary?.reserved_count??'—'}</strong><small>Reserved</small></div></div><div className="dataset-summary-card"><span><LockKeyhole size={17}/></span><div><strong>{dashboardSummary?.active_lock_count??'—'}</strong><small>Active Locks</small></div></div></section>:role==='QA Engineer'?<section className="dataset-summary-row dataset-summary-row-qa"><div className="dataset-summary-card"><span><Database size={17}/></span><div><strong>{publishedCount}</strong><small>Published Data Sets</small></div></div><div className="dataset-summary-card"><span><Sparkles size={17}/></span><div><strong>{syntheticCount}</strong><small>Synthetic</small></div></div><div className="dataset-summary-card"><span><GitBranch size={17}/></span><div><strong>{sourceBackedCount}</strong><small>Source-backed</small></div></div><div className="dataset-summary-card"><span><CalendarCheck2 size={17}/></span><div><strong>{reservedCount}</strong><small>Reserved</small></div></div><div className="dataset-summary-card"><span><LockKeyhole size={17}/></span><div><strong>{activeLockCount}</strong><small>Active Locks</small></div></div></section>:null}
    <section className="panel dataset-catalog"><div className="dataset-toolbar"><div><h2>{role==='Admin'?'Data set catalog':'Workspace catalog'}</h2><p>{role==='Admin'?'Browse all data sets created by users across your workspaces. Details are view only.':role==='Test Data Engineer'?'View the data sets available in this workspace.':'Latest version for each published data set available in this workspace.'}</p></div><div className="inline-actions">{canManage&&<button className="button button-confirm" onClick={()=>setShowCreate(true)}><Plus size={16}/>Create draft</button>}</div></div>
      <div className="dataset-filter-count" aria-live="polite">Showing <strong>{visibleItems.length}</strong> of <strong>{workspaceTotal}</strong> data sets</div><form className="dataset-filters" onSubmit={event=>{event.preventDefault();setQuery(search)}}><label className="dataset-search"><Search size={16}/><input aria-label="Search data sets" value={search} onChange={event=>setSearch(event.target.value)} placeholder="Search name, ID, description, or owner"/></label>{role!=='QA Engineer'&&<select aria-label="Filter by status" value={state} onChange={event=>setState(event.target.value)}><option value="">All states</option>{Object.entries(stateLabel).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select>}<select aria-label="Filter by creation mode" value={pathway} onChange={event=>setPathway(event.target.value)}><option value="">All creation modes</option><option value="source_to_data">Source to Data</option><option value="scenario_to_data">{SCENARIO_TO_DATA_LABEL}</option><option value="self_servicing_data">Self-service Query</option></select><button type="button" className="icon-button" aria-label="Filter data sets" title="Filter data sets" onClick={openFilters}><ListFilter size={17}/></button><button type="button" className="icon-button" aria-label="Refresh data sets and clear filters" title="Refresh data sets and clear filters" disabled={loading} onClick={refreshAndClearFilters}><RefreshCw size={17}/></button></form>
      {error&&<div className="alert alert-warning">{error}</div>}
      <div className="table-shell dataset-table"><table><thead><tr><th>Data set</th><th>Generation</th><th>Latest version</th><th>Record Count</th>{role==='QA Engineer'?<><th>Created</th><th>Last Updated</th></>:<><th>Privacy</th><th>Validation</th><th>Updated</th></>}<th/></tr></thead><tbody>{visibleItems.map(item=><tr key={item.id}><td><button className="dataset-name-button" onClick={()=>open(item)}><strong>{item.name}<DatasetActivityIcons datasetId={item.id} status={role==='QA Engineer'?undefined:(workspaceDatasetUsage[item.id]??null)}/></strong><small><em>{catalogRequirementLabel(item)}</em></small><small className="dataset-reference-code">{item.reference_code}</small>{role==='Admin'&&<small className="dataset-reference-code">{workspaceNames[item.workspace_id]??'Workspace'}</small>}</button></td><td><span className={`dataset-mode-value ${generationModeClass(item.latest_version.provenance,item.creation_pathway)}`}>{generationModeLabel(item.latest_version.provenance,item.creation_pathway)}</span></td><td><span className={`status-badge dataset-version-status lifecycle-${item.latest_version.lifecycle_state}`}><span>v{item.latest_version.version_number}</span><span className="dataset-version-separator" aria-hidden="true">·</span><span>{stateLabel[item.latest_version.lifecycle_state]}</span></span></td><td>{item.latest_version.record_count?.toLocaleString()??'—'}</td>{role==='QA Engineer'?<><td>{new Date(item.created_at).toLocaleDateString()}</td><td>{new Date(item.updated_at).toLocaleDateString()}</td></>:<><td>{humanize(item.latest_version.privacy_state)}</td><td>{humanize(item.latest_version.validation_state)}</td><td>{new Date(item.updated_at).toLocaleDateString()}</td></>}<td><div className="dataset-row-actions">{item.creation_pathway==='self_servicing_data'&&item.latest_version.lifecycle_state==='published'&&<DatasetRefreshButton datasetId={item.id} version={item.latest_version.version_number} role={role} operation={item.latest_version.provenance.ssd_operation} onRefreshed={()=>void load()} iconOnly/>}<button type="button" className="icon-button dataset-row-icon-button dataset-lineage-button" aria-label="View lineage" title="View lineage" onClick={()=>open(item,'lineage')}><GitBranch size={16}/></button><button type="button" className="icon-button dataset-row-icon-button dataset-view-button" aria-label="View details" title="View details" onClick={()=>open(item)}><Eye size={16}/></button>{role!=='Admin'&&<button className="text-button dataset-download-button" disabled={!downloadEligible(item.latest_version)} title={downloadUnavailableReason(item.latest_version)??'Download this version as '+artifactFormat(item.latest_version).toUpperCase()} onClick={()=>void downloadRow(item)}><Download size={14}/>{artifactFormat(item.latest_version).toUpperCase()}</button>}</div></td></tr>)}{!loading&&!visibleItems.length&&<tr><td colSpan={role==='QA Engineer'?7:8} className="table-empty">No data sets match this workspace and filter.</td></tr>}{loading&&<tr><td colSpan={role==='QA Engineer'?7:8} className="table-empty">Loading authorized data sets…</td></tr>}</tbody></table></div>
    </section>
    {filterOpen&&<div className="dataset-overlay" role="presentation" onMouseDown={event=>{if(event.target===event.currentTarget)setFilterOpen(false)}}><section className="dataset-modal dataset-filter-modal" role="dialog" aria-modal="true" aria-labelledby="dataset-filter-title"><header className="dataset-modal-header"><div><div className="eyebrow">WORKSPACE CATALOG</div><h2 id="dataset-filter-title">Filter data sets</h2><p>Choose date ranges and generation mode for the visible data sets.</p></div><button type="button" className="icon-button" aria-label="Close filters" onClick={()=>setFilterOpen(false)}><X size={18}/></button></header><div className="dataset-detail-content"><div className="dataset-filter-grid"><fieldset className="dataset-filter-range dataset-filter-since"><legend>Created since</legend><label className="form-field">Date<input type="date" value={draftCreatedFrom} onChange={event=>setDraftCreatedFrom(event.target.value)}/></label></fieldset><fieldset className="dataset-filter-range"><legend>Updated between</legend><label className="form-field">From<input type="date" value={draftUpdatedFrom} onChange={event=>setDraftUpdatedFrom(event.target.value)}/></label><label className="form-field">To<input type="date" value={draftUpdatedTo} onChange={event=>setDraftUpdatedTo(event.target.value)}/></label></fieldset><label className="form-field">Generation mode<select value={draftGenerationMode} onChange={event=>setDraftGenerationMode(event.target.value)}><option value="">All generation modes</option><option value="Synthetic">Synthetic</option><option value="Source-backed">Source-backed</option><option value="Self-service Query">Self-service Query</option></select></label></div></div><footer className="dataset-modal-footer"><button type="button" className="text-button" onClick={()=>{setDraftCreatedFrom('');setDraftUpdatedFrom('');setDraftUpdatedTo('');setDraftGenerationMode('')}}>Clear filters</button><div className="inline-actions"><button type="button" className="button button-secondary" onClick={()=>setFilterOpen(false)}>Cancel</button><button type="button" className="button button-confirm" onClick={applyFilters}>Apply filters</button></div></footer></section></div>}
    {showCreate&&<CreateDraft role={role} workspaceId={workspaceId} onClose={()=>setShowCreate(false)} onCreated={async(id,pathway)=>{setShowCreate(false);navigate(`${pathway==='source_to_data'?'/source-to-data':'/scenario-to-data'}?draft_id=${encodeURIComponent(id)}`)}}/>}
    {selection&&<DatasetModalBoundary key={`${selection.id}:${selection.version??'latest'}`} onClose={()=>setSelection(null)}><DatasetModal role={role} piiMaskingEnabled={piiMaskingEnabled} selection={selection} detail={detail} error={detailError} onClose={()=>setSelection(null)} onTab={tab=>setSelection(current=>current?{...current,tab}:current)} onVersion={version=>setSelection(current=>current?{...current,version}:current)} onPublish={()=>void publishCurrent()} onDownload={()=>void downloadCurrent()} onExpiryUpdated={handleExpiryUpdated} onEditVersion={version=>setEditSource(version)} onReviewPrivacy={()=>{if(detail)navigate(`/privacy?dataset_id=${encodeURIComponent(detail.data_set.id)}`)}} onResume={()=>{if(!detail)return;const path=workflowPathFor(detail);navigate(`${path}?draft_id=${encodeURIComponent(detail.data_set.id)}`);setSelection(null)}}/></DatasetModalBoundary>}
    {downloadReview&&<DownloadReviewDialog review={downloadReview} busy={downloadBusy} onCancel={()=>setDownloadReview(null)} onDownload={()=>void confirmDownload()}/> }
    {editSource&&detail&&<EditPublishedVersion role={role} dataset={detail.data_set} version={editSource} onClose={()=>setEditSource(null)} onCreated={createEditedVersion}/>}
  </div>
}

export function DataSetDetailsDialog({role,datasetId,versionNumber,onClose,onPublished,piiMaskingEnabled=true}:{role:HumanRole;workspaceId:string;datasetId:string;versionNumber?:number;onClose:()=>void;onPublished?:()=>void;piiMaskingEnabled?:boolean}){
  const navigate=useNavigate()
  const [selection,setSelection]=useState<{id:string;version?:number;tab:Tab}>({id:datasetId,version:versionNumber,tab:'overview'})
  const [detail,setDetail]=useState<DatasetDetail|null>(null)
  const [error,setError]=useState('')
  const [editSource,setEditSource]=useState<DataSetVersion|null>(null)
  const [downloadReview,setDownloadReview]=useState<DownloadReview|null>(null)
  const [downloadBusy,setDownloadBusy]=useState(false)
  useEffect(()=>{
    let active=true
    setDetail(null);setError('')
    const version=selection.version??versionNumber
    apiRequest<DatasetDetail>(`/datasets/${selection.id}${version?`?version_number=${version}`:''}`,role,{cache:'no-store'})
      .then(value=>{if(active){setDetail(value);if(!version)setSelection(current=>current.id===selection.id?{...current,version:value.selected_version.version_number}:current)}})
      .catch(cause=>{if(active)setError(cause instanceof Error?cause.message:'Unable to load data set details')})
    return()=>{active=false}
  },[selection.id,selection.version,role,versionNumber])
  async function publishCurrent(){
    if(!detail)return
    try{await apiRequest(`/datasets/${detail.data_set.id}/versions/${detail.selected_version.version_number}/publish`,role,{method:'POST'});setDetail(await apiRequest<DatasetDetail>(`/datasets/${detail.data_set.id}?version_number=${detail.selected_version.version_number}`,role,{cache:'no-store'}));onPublished?.()}
    catch(cause){setError(cause instanceof Error?cause.message:'Unable to publish this version')}
  }
  async function downloadCurrent(){
    if(!detail)return
    try{const current=detail.selected_version;const access=await apiRequest<DownloadAccess>(`/datasets/${detail.data_set.id}/versions/${current.version_number}/download-access`,role);setDownloadReview({datasetId:detail.data_set.id,datasetName:detail.data_set.name,referenceCode:detail.data_set.reference_code,versionNumber:current.version_number,format:artifactFormat(current),access})}
    catch(cause){setError(cause instanceof Error?cause.message:'Unable to check download access')}
  }
  async function confirmDownload(){
    if(!downloadReview?.access.can_download)return
    setDownloadBusy(true)
    try{const result=await apiDownload(`/datasets/${downloadReview.datasetId}/versions/${downloadReview.versionNumber}/artifact?format=${downloadReview.format}`,role);saveDownload(result.blob,result.filename??`${downloadReview.referenceCode}.${downloadReview.format}`);setDownloadReview(null)}
    catch(cause){setError(cause instanceof Error?cause.message:'Unable to download this artifact')}
    finally{setDownloadBusy(false)}
  }
  function handleExpiryUpdated(updated:DataSetVersion){setDetail(current=>current?withUpdatedVersion(current,updated):current);onPublished?.()}
  return <>
    <DatasetModal role={role} piiMaskingEnabled={piiMaskingEnabled} selection={selection} detail={detail} error={error} onClose={onClose} onTab={tab=>setSelection(current=>({...current,tab}))} onVersion={version=>setSelection(current=>({...current,version}))} onPublish={()=>void publishCurrent()} onDownload={()=>void downloadCurrent()} onExpiryUpdated={handleExpiryUpdated} onEditVersion={version=>setEditSource(version)} onReviewPrivacy={()=>{if(!detail)return;onClose();navigate(`/privacy?dataset_id=${encodeURIComponent(detail.data_set.id)}`)}} onResume={()=>{if(!detail)return;const path=workflowPathFor(detail);navigate(`${path}?draft_id=${encodeURIComponent(detail.data_set.id)}`);onClose()}}/>
    {downloadReview&&<DownloadReviewDialog review={downloadReview} busy={downloadBusy} onCancel={()=>setDownloadReview(null)} onDownload={()=>void confirmDownload()}/> }
    {editSource&&detail&&<EditPublishedVersion role={role} dataset={detail.data_set} version={editSource} onClose={()=>setEditSource(null)} onCreated={async(version,editPlanId,sourceWizardEdit)=>{setEditSource(null);onClose();const path=workflowPathFor(detail);navigate(sourceWizardEdit?`${path}?edit_dataset_id=${encodeURIComponent(detail.data_set.id)}&base_version_number=${version}`:editPlanId?`/scenario-to-data?edit_plan_id=${encodeURIComponent(editPlanId)}&edit_dataset_id=${encodeURIComponent(detail.data_set.id)}&base_version_number=${version}`:`${path}?draft_id=${encodeURIComponent(detail.data_set.id)}&version_number=${version}`)}}/>}
  </>
}

function withUpdatedVersion(detail:DatasetDetail,updated:DataSetVersion):DatasetDetail{
  return {
    ...detail,
    selected_version:detail.selected_version.version_number===updated.version_number?updated:detail.selected_version,
    versions:detail.versions.map(version=>version.version_number===updated.version_number?updated:version),
    data_set:{...detail.data_set,latest_version:detail.data_set.latest_version.version_number===updated.version_number?updated:detail.data_set.latest_version},
  }
}

function recordPrivacyComplete(version:DataSetVersion){return version.provenance.origin!=='source_backed'||version.provenance.pii_masking_required===false||Boolean(version.validation_evidence.masking_run_id)}
function downloadUnavailableReason(version:DataSetVersion){
  if(version.lifecycle_state!=='published'||version.publication_state!=='published')return 'Download is available after this version is published.'
  if(version.validation_state!=='passed')return 'Download is unavailable because validation has not passed.'
  if(version.privacy_state!=='compliant')return 'Download is unavailable until privacy review is compliant.'
  if(!recordPrivacyComplete(version))return 'Download is unavailable because this source-backed version has no completed PII masking record.'
  if(version.expiry_at&&new Date(version.expiry_at).getTime()<=Date.now())return 'Download is unavailable because this version has expired.'
  return null
}
function downloadEligible(version:DataSetVersion){return downloadUnavailableReason(version)===null}
function nextActionText(version:DataSetVersion,piiMaskingEnabled=true){
  if(version.lifecycle_state==='expired')return 'Expired → Create a new version before use.'
  if(version.lifecycle_state==='failed'&&version.provenance.origin==='source_backed')return 'Failed → Choose Retry generation to review the source query and generate a new version.'
  if(version.lifecycle_state==='quarantined'||version.lifecycle_state==='failed'||version.privacy_state==='blocked')return `${stateLabel[version.lifecycle_state]} → Review validation details, correct the issue, and create a fresh draft.`
  if(piiMaskingEnabled&&version.provenance.origin==='source_backed'&&version.provenance.pii_masking_required!==false&&!version.validation_evidence.masking_run_id)return version.lifecycle_state==='published'?'Published → Create a new version and complete PII masking before use.':`${stateLabel[version.lifecycle_state]} → Complete PII masking before preview or publication.`
  if(version.lifecycle_state==='draft'&&version.validation_state!=='passed')return !piiMaskingEnabled||version.provenance.pii_masking_required===false?'Draft → Complete validation before publication.':'Draft → Complete validation, then scan fields and complete PII masking.'
  if(piiMaskingEnabled&&version.lifecycle_state==='draft'&&version.provenance.pii_masking_required!==false&&['requires_masking','not_assessed'].includes(version.privacy_state))return 'Draft → Scan Fields and complete PII masking.'
  if(version.lifecycle_state==='ready')return 'Ready → Publish this data set.'
  if(version.lifecycle_state==='published'&&version.privacy_state==='compliant')return 'Published → Ready to use.'
  return `${stateLabel[version.lifecycle_state]} → Review validation details for the next required action.`
}
function artifactFormat(version:DataSetVersion):'csv'|'json'{const reference=version.validation_evidence?.artifact_ref;if(typeof reference==='string'&&/\.csv(?:$|[?#])/i.test(reference))return 'csv';return version.generation_settings?.output_format==='csv'?'csv':'json'}
function saveDownload(blob:Blob,filename:string){const url=URL.createObjectURL(blob);const anchor=document.createElement('a');anchor.href=url;anchor.download=filename;anchor.click();URL.revokeObjectURL(url)}
function catalogRequirementLabel(item:DataSetSummary){if(item.creation_pathway==='scenario_to_data')return `Scenario: ${item.description||'No requirement description'}`;if(item.creation_pathway==='self_servicing_data')return `Source: SSD-Query/${item.visibility==='public'?'Public':'Private'}`;if(item.latest_version.provenance.source_query_id)return 'Source: STD-Query';return `Source: ${item.federated_plan_name||item.latest_version.provenance.source_object||'-'}` }

function CreateDraft({role,workspaceId,onClose,onCreated}:{role:HumanRole;workspaceId:string;onClose:()=>void;onCreated:(id:string,pathway:'source_to_data'|'scenario_to_data')=>Promise<void>}){
  const [name,setName]=useState('')
  const [targetUse,setTargetUse]=useState('')
  const [pathway,setPathway]=useState<'source_to_data'|'scenario_to_data'>('source_to_data')
  const [maximumRecords,setMaximumRecords]=useState(100)
  const [outputFormat,setOutputFormat]=useState<'json'|'csv'>('json')
  const [expiry,setExpiry]=useState('')
  const [error,setError]=useState('')
  const [busy,setBusy]=useState(false)
  const submitLock=useRef(false)
  async function submit(event:FormEvent){
    event.preventDefault();if(submitLock.current)return;submitLock.current=true;setBusy(true);setError('')
    try{
      const created=await apiRequest<DataSetSummary>(`/workspaces/${workspaceId}/datasets`,role,{method:'POST',body:JSON.stringify({name,description:targetUse.trim()||null,creation_pathway:pathway,maximum_records:maximumRecords,output_format:outputFormat,schema_scope:[],selected_fields:[],provenance:{origin:'not_set'},expiry_at:expiry?new Date(expiry).toISOString():null,permitted_use:[targetUse.trim()||'testing']})})
      await onCreated(created.id,pathway)
    }catch(cause){setError(cause instanceof Error?cause.message:'Unable to create draft data set')}
    finally{submitLock.current=false;setBusy(false)}
  }
  return <div className="dataset-overlay" role="presentation" onMouseDown={event=>{if(event.target===event.currentTarget)onClose()}}><section className="dataset-modal dataset-create-modal" role="dialog" aria-modal="true" aria-labelledby="create-dataset-title"><header className="dataset-modal-header"><div><div className="eyebrow">NEW DATA SET</div><h2 id="create-dataset-title">Create draft metadata</h2><p>This creates a versioned draft. Generation and validation happen in later workflow phases.</p></div><button className="icon-button" aria-label="Close" onClick={onClose}><X size={18}/></button></header><form onSubmit={submit}><div className="dataset-form-grid"><label className="form-field">Data set name<input required minLength={2} maxLength={180} value={name} onChange={event=>setName(event.target.value)} placeholder="e.g. Customer test data"/></label><label className="form-field">Creation pathway<select value={pathway} onChange={event=>setPathway(event.target.value as typeof pathway)}><option value="source_to_data">Source to Data</option><option value="scenario_to_data">{SCENARIO_TO_DATA_LABEL}</option><option value="self_servicing_data">Self-service Query</option></select></label><label className="form-field">Generation mode<input readOnly value={pathway==='source_to_data'?'Source-backed':'Choose in '+SCENARIO_TO_DATA_LABEL}/><small>{pathway==='source_to_data'?'The selected pathway uses approved source data.':'The mode is chosen in Step 1 of the Scenario to Data workflow.'}</small></label><label className="form-field dataset-span-two">Target use<input required maxLength={80} value={targetUse} onChange={event=>setTargetUse(event.target.value)} placeholder="e.g. Regression testing"/></label><label className="form-field">Record Count<input required type="number" inputMode="numeric" step={1} min={1} max={10000} value={maximumRecords} onChange={event=>setMaximumRecords(Number(event.target.value))}/></label><label className="form-field">Output format<select value={outputFormat} onChange={event=>setOutputFormat(event.target.value as 'json'|'csv')}><option value="json">JSON</option><option value="csv">CSV</option></select></label><label className="form-field dataset-span-two">Expiry (optional)<input type="datetime-local" value={expiry} onChange={event=>setExpiry(event.target.value)}/></label></div>{error&&<div className="alert alert-warning">{error}</div>}<footer className="dataset-modal-actions"><button className="button button-confirm" disabled={busy}><FilePlus2 size={15}/>{busy?'Saving…':'Create draft'}</button><button type="button" className="button button-secondary" onClick={onClose}>Cancel</button></footer></form></section></div>
}

function EditPublishedVersion({role,dataset,version,onClose,onCreated}:{role:HumanRole;dataset:DataSetSummary;version:DataSetVersion;onClose:()=>void;onCreated:(versionNumber:number,editPlanId?:string,sourceWizardEdit?:boolean)=>Promise<void>}){
  const [name,setName]=useState(dataset.name)
  const [targetUse,setTargetUse]=useState(version.permitted_use[0]||'testing')
  const [maximumRecords,setMaximumRecords]=useState(Math.max(Number(version.generation_settings.maximum_records||1),version.record_count||0))
  const [outputFormat,setOutputFormat]=useState<'json'|'csv'>(version.generation_settings.output_format==='csv'?'csv':'json')
  const [expiry,setExpiry]=useState(()=>version.expiry_at?toLocalDateTime(version.expiry_at):'')
  const [outputNames,setOutputNames]=useState(()=>version.schema_scope.map((_,index)=>version.selected_fields[index]||version.schema_scope[index].field))
  const [error,setError]=useState('')
  const [busy,setBusy]=useState(false)
  const scenarioBacked=Boolean(version.provenance.scenario_id)
  const sourceBacked=version.provenance.origin==='source_backed'
  const quarantinedBase=version.lifecycle_state==='quarantined'
  const failedBase=version.lifecycle_state==='failed'
  const willRegenerate=quarantinedBase||sourceBacked||scenarioBacked
  const outputNamesChanged=outputNames.some((field,index)=>field.trim()!==(version.selected_fields[index]||version.schema_scope[index].field))
  const duplicateName=outputNames.some((field,index)=>outputNames.indexOf(field)===index&&outputNames.lastIndexOf(field)!==index)
  async function submit(event:FormEvent){
    event.preventDefault();setBusy(true);setError('')
    if(duplicateName){setError('Output field names must be unique.');setBusy(false);return}
    if(willRegenerate&&outputNamesChanged){setError('Keep output field names unchanged when regenerating this version.');setBusy(false);return}
    try{
      if(scenarioBacked&&version.lifecycle_state==='published'&&version.provenance.scenario_id){
        const sourcePlan=await apiRequest<{id:string;name:string;requirement:string;expected_states:string[];exclusions:string[];target_use:string;volume:number;generation_mode:'synthetic'|'source_backed';source_connection_id:string|null;source_object:string|null;mapping:unknown[];logical_plan:Record<string,unknown>}>(`/scenario-plans/${encodeURIComponent(version.provenance.scenario_id)}`,role)
        const sourceLogical=sourcePlan.logical_plan??{}
        const predicates=(sourceLogical.predicates as Array<{field:string;operator:string;value?:unknown}>|undefined)??[]
        const relationships=(sourceLogical.relationships as unknown[]|undefined)??[]
        const sourceJoins=(sourceLogical.source_joins as unknown[]|undefined)??[]
        const draftData:Record<string,unknown>={...((sourceLogical.draft_data as Record<string,unknown>|undefined)??{}),dataset_name:name.trim(),output_format:outputFormat,expiry_at:expiry?new Date(expiry).toISOString():'',step:0,edit_dataset_id:dataset.id,base_version_number:version.version_number,mapping:sourcePlan.mapping,relationships,source_joins:sourceJoins,filters:predicates.map(item=>({field:item.field,operator:item.operator,value:String(item.value??'')})),query_text:sourceLogical.generated_query??''}
        delete draftData.draft_dataset_id
        const createdPlan=await apiRequest<{id:string}>(`/workspaces/${dataset.workspace_id}/scenario-plans`,role,{method:'POST',body:JSON.stringify({name:name.trim(),requirement:sourcePlan.requirement,expected_states:sourcePlan.expected_states,exclusions:sourcePlan.exclusions,target_use:targetUse.trim()||sourcePlan.target_use,volume:maximumRecords,draft_data:draftData})})
        await apiRequest(`/scenario-plans/${createdPlan.id}`,role,{method:'PATCH',body:JSON.stringify({generation_mode:sourcePlan.generation_mode,source_connection_id:sourcePlan.source_connection_id,source_object:sourcePlan.source_object,mapping:sourcePlan.mapping,predicates,relationships,source_joins:sourceJoins,draft_data:draftData})})
        await onCreated(version.version_number,createdPlan.id)
        return
      }
      if(dataset.creation_pathway==='source_to_data'&&sourceBacked){
        await onCreated(version.version_number,undefined,true)
        return
      }
      // Lock metadata is derived from the current approved connection snapshot
      // by the API. Sending it back from a version response violates the
      // provenance input schema, which intentionally excludes server metadata.
      const acceptedProvenanceFields=new Set(['origin','source_connection_id','source_object','source_schema_version','source_query_id','source_mapping_id','source_predicates','mapping_version','scenario_id','scenario_version','job_id','artifact_reference'])
      const provenance=Object.fromEntries(Object.entries(version.provenance).filter(([key])=>acceptedProvenanceFields.has(key)))
      const created=await apiRequest<DataSetVersion>(`/datasets/${dataset.id}/versions?base_version_number=${version.version_number}`,role,{
        method:'POST',body:JSON.stringify({
          name:name.trim(),description:targetUse.trim()||null,creation_pathway:dataset.creation_pathway,
          maximum_records:maximumRecords,output_format:outputFormat,schema_scope:version.schema_scope,
          selected_fields:outputNames.map(field=>field.trim()),provenance,
          expiry_at:expiry?new Date(expiry).toISOString():null,permitted_use:[targetUse.trim()||'testing'],
        }),
      })
      await onCreated(created.version_number)
    }catch(cause){setError(cause instanceof Error?cause.message:'Unable to create a new data set version')}
    finally{setBusy(false)}
  }
  const fieldRows=version.schema_scope.map((field,index)=>({field,output:outputNames[index]||'',index}))
  return <div className="dataset-overlay dataset-edit-overlay" role="presentation" onMouseDown={event=>{if(event.target===event.currentTarget)onClose()}}><section className="dataset-modal dataset-create-modal dataset-edit-modal" role="dialog" aria-modal="true" aria-labelledby="edit-version-title"><header className="dataset-modal-header"><div><div className="eyebrow">{failedBase?'FAILED':quarantinedBase?'QUARANTINED':'PUBLISHED'} VERSION · v{version.version_number} · {generationModeLabel(version.provenance,dataset.creation_pathway)}</div><h2 id="edit-version-title">{failedBase?'Review failed version': 'Edit as a new version'}</h2><p>{failedBase?'A retry creates a fresh version. Review the source and run generation from Step 4.':'Earlier versions and their lineage remain unchanged.'}</p></div><button className="icon-button" aria-label="Close" onClick={onClose}><X size={18}/></button></header><form onSubmit={event=>void submit(event)}><div className="dataset-form-grid"><label className="form-field dataset-span-two">Data set name<input required minLength={2} maxLength={180} value={name} onChange={event=>setName(event.target.value)}/></label><label className="form-field dataset-span-two">Target use<input required maxLength={80} value={targetUse} onChange={event=>setTargetUse(event.target.value)}/></label><label className="form-field">Record Count<input required type="number" inputMode="numeric" step={1} min={scenarioBacked||quarantinedBase?1:(version.record_count||1)} max={10000} value={maximumRecords} onChange={event=>setMaximumRecords(Number(event.target.value))}/></label><label className="form-field">Output format<select value={outputFormat} onChange={event=>setOutputFormat(event.target.value as 'json'|'csv')}><option value="json">JSON</option><option value="csv">CSV</option></select></label><label className="form-field dataset-span-two">Expiry (optional)<input type="datetime-local" value={expiry} onChange={event=>setExpiry(event.target.value)}/></label></div><div className="dataset-edit-copy-note">{willRegenerate?scenarioBacked?`The linked scenario will run again to generate ${maximumRecords} records.`:`The saved source mapping will run again to generate up to ${maximumRecords} records.`:`This edit will carry forward ${version.record_count?.toLocaleString()??0} existing records. For non-scenario data sets, the Record Count is a limit and does not create additional rows.`}</div><section className="dataset-edit-fields"><div className="dataset-schema-heading"><div><h3>Output fields</h3><p>{willRegenerate?'Source fields, output names, and types stay fixed for regeneration.':'Rename output fields. Source fields and types stay fixed when carrying records forward.'}</p></div><span>{fieldRows.length} fields</span></div><div className="table-shell"><table><thead><tr><th>Source field</th><th>Output field</th><th>Type</th></tr></thead><tbody>{fieldRows.map(({field,output,index})=><tr key={`${field.entity||''}.${field.source_field||field.field}.${index}`}><td><strong>{field.entity?`${field.entity}.`:''}{field.source_field||field.field}</strong></td><td><input aria-label={`Output field for ${field.field}`} required maxLength={180} disabled={busy||willRegenerate} value={output} onChange={event=>setOutputNames(current=>current.map((name,row)=>row===index?event.target.value: name))}/></td><td><code>{field.data_type}</code></td></tr>)}</tbody></table></div></section>{error&&<div className="alert alert-warning">{error}</div>}<footer className="dataset-modal-actions"><button className="button button-confirm" disabled={busy||duplicateName}><FilePlus2 size={15}/>{busy?'Opening…':failedBase&&sourceBacked?'Review & retry in Source to Data':sourceBacked?'Review in Source to Data':willRegenerate?'Generate new version':'Create new draft version'}</button><button type="button" className="button button-secondary" onClick={onClose} disabled={busy}>Cancel</button></footer></form></section></div>
}

function toLocalDateTime(value:string){const date=new Date(value);date.setMinutes(date.getMinutes()-date.getTimezoneOffset());return date.toISOString().slice(0,16)}

function ReservationRequestForm({datasetId,workspaceId,versionNumber,role,onClose}:{datasetId:string;workspaceId:string;versionNumber:number;role:HumanRole;onClose:()=>void}){
  const [startsAt,setStartsAt]=useState(()=>toLocalDateTime(new Date(Date.now()+10*60_000).toISOString()))
  const [endsAt,setEndsAt]=useState(()=>toLocalDateTime(new Date(Date.now()+70*60_000).toISOString()))
  const [purpose,setPurpose]=useState('Planned QA test execution')
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[submitted,setSubmitted]=useState(false)
  const [pendingRequest,setPendingRequest]=useState<{starts_at:string;ends_at:string}|null>(null)
  const [checkingPending,setCheckingPending]=useState(true),[pendingCheckError,setPendingCheckError]=useState('')
  const [pendingRefresh,setPendingRefresh]=useState(0)
  useEffect(()=>{
    let active=true
    setCheckingPending(true);setPendingCheckError('')
    apiRequest<Array<{dataset_id:string;status:string;starts_at:string;ends_at:string}>>(`/workspaces/${workspaceId}/data-requests`,role)
      .then(requests=>{if(active){setPendingRequest(requests.find(request=>request.dataset_id===datasetId&&request.status==='pending')??null);setError('')}})
      .catch(()=>{if(active)setPendingCheckError('Could not check for another pending request. Review Requests before submitting again.')})
      .finally(()=>{if(active)setCheckingPending(false)})
    return()=>{active=false}
  },[datasetId,workspaceId,role,pendingRefresh])
  async function submit(event:FormEvent){
    event.preventDefault()
    if(checkingPending||pendingRequest||pendingCheckError)return
    setBusy(true);setError('')
    try{
      await apiRequest(`/datasets/${datasetId}/versions/${versionNumber}/data-requests`,role,{method:'POST',body:JSON.stringify({starts_at:new Date(startsAt).toISOString(),ends_at:new Date(endsAt).toISOString(),purpose})})
      setSubmitted(true)
    }catch(cause){const message=cause instanceof Error?cause.message:'Unable to request a reservation';setError(message);if(message.includes('already pending approval'))setPendingRefresh(value=>value+1)}
    finally{setBusy(false)}
  }
  return <section className="dataset-reservation-request" aria-label="Request a data set reservation">{submitted?<div className="dataset-reservation-success"><strong>Reservation requested</strong><span>An Admin or Test Data Engineer must approve this time window before it is booked.</span><button type="button" className="button button-secondary compact" onClick={onClose}>Done</button></div>:<><div><h3>Request a reservation</h3><p>Choose a time window for QA use. Approval will book the reservation.</p></div>{checkingPending&&<div className="scenario-notice" role="status">Checking for an existing pending request…</div>}{pendingRequest&&<div className="alert alert-warning" role="status"><strong>You already have a request pending approval for this data set.</strong><span>Requested window: {new Date(pendingRequest.starts_at).toLocaleString()} – {new Date(pendingRequest.ends_at).toLocaleString()}. You can request again after it is approved or denied.</span><div className="dataset-reservation-actions"><button type="button" className="button button-secondary compact" disabled={checkingPending} onClick={()=>setPendingRefresh(value=>value+1)}>{checkingPending?'Checking…':'Check status again'}</button><button type="button" className="button button-secondary compact" onClick={onClose}>Close</button></div></div>}{pendingCheckError&&<div className="alert alert-warning" role="alert"><span>{pendingCheckError}</span><button type="button" className="button button-secondary compact" onClick={()=>setPendingRefresh(value=>value+1)}>Retry check</button></div>}{!pendingRequest&&<form onSubmit={event=>void submit(event)}><label className="form-field">Starts<input type="datetime-local" required value={startsAt} onChange={event=>setStartsAt(event.target.value)}/></label><label className="form-field">Ends<input type="datetime-local" required min={startsAt} value={endsAt} onChange={event=>setEndsAt(event.target.value)}/></label><label className="form-field dataset-reservation-purpose">Purpose<input required minLength={3} maxLength={500} value={purpose} onChange={event=>setPurpose(event.target.value)}/></label><div className="dataset-reservation-actions"><button type="submit" className="button button-primary privacy-publish-link" disabled={busy||checkingPending||!!pendingCheckError} title={pendingCheckError?'Check request status before submitting again.':undefined}>{busy?'Sending…':'Submit request'}</button><button type="button" className="button button-secondary compact" onClick={onClose} disabled={busy}>Cancel</button></div></form>}{error&&<div className="alert alert-warning" role="alert">{error}</div>}</>}</section>
}

function DatasetModal({role,piiMaskingEnabled,selection,detail,error,onClose,onTab,onVersion,onPublish,onDownload,onExpiryUpdated,onEditVersion,onReviewPrivacy,onResume}:{role:HumanRole;piiMaskingEnabled:boolean;selection:{id:string;version?:number;tab:Tab};detail:DatasetDetail|null;error:string;onClose:()=>void;onTab:(tab:Tab)=>void;onVersion:(version:number)=>void;onPublish:()=>void;onDownload:()=>void;onExpiryUpdated:(version:DataSetVersion)=>void;onEditVersion:(version:DataSetVersion)=>void;onReviewPrivacy:()=>void;onResume:()=>void}){
  const tabs:Array<{id:Tab;label:string;icon:typeof Database}>=[{id:'overview',label:'Overview',icon:Database},{id:'lineage',label:'Lineage',icon:GitBranch},{id:'validation',label:'Validation',icon:ShieldCheck},{id:'access',label:'Access',icon:LockKeyhole}]
  const current=detail?.selected_version
  const [reservationOpen,setReservationOpen]=useState(false)
  return <div className="dataset-overlay" role="presentation" onMouseDown={event=>{if(event.target===event.currentTarget)onClose()}}><section className="dataset-modal dataset-detail-modal" role="dialog" aria-modal="true" aria-label="Data set details"><header className="dataset-modal-header"><div><div className="eyebrow">{creationPathwayLabel(detail?.data_set.creation_pathway).toUpperCase()} · {generationModeLabel(current?.provenance,detail?.data_set.creation_pathway).toUpperCase()}</div><h2>{detail?.data_set.name||'Data set details'}<DatasetActivityIcons datasetId={detail?.data_set.id}/></h2><p>{detail?.data_set.description||'Versioned data set metadata and governance evidence.'}</p></div><div className="dataset-modal-header-actions">{role==='QA Engineer'&&detail?.data_set.visibility!=='private'&&current?.lifecycle_state==='published'&&current.version_number===detail?.data_set.latest_version.version_number&&current.privacy_state==='compliant'&&current.validation_state==='passed'&&<button type="button" className="button button-primary privacy-publish-link dataset-reserve-button" onClick={()=>setReservationOpen(value=>!value)}><CalendarPlus size={15}/>{reservationOpen?'Close request':'Reserve'}</button>}{role!=='Admin'&&<button type="button" className="button button-secondary dataset-artifact-download" disabled={!current||!downloadEligible(current)} title={current?(downloadUnavailableReason(current)??'Download this version as '+artifactFormat(current).toUpperCase()):'Select a data set version to download.'} onClick={onDownload}><Download size={15}/>Download {current?artifactFormat(current).toUpperCase():'file'}</button>}<button className="icon-button" aria-label="Close data set details" onClick={onClose}><X size={18}/></button></div></header>
    {detail&&<div className="dataset-version-picker"><span className={`status-badge dataset-version-status lifecycle-${current?.lifecycle_state}`}><span>v{current?.version_number}</span><span className="dataset-version-separator" aria-hidden="true">·</span><span>{current?stateLabel[current.lifecycle_state]:''}</span></span><span className={`dataset-mode-value ${generationModeClass(current?.provenance,detail?.data_set.creation_pathway)}`}>{generationModeLabel(current?.provenance,detail?.data_set.creation_pathway)}</span>{detail.versions.length>1&&<select aria-label="Selected data set version" value={current?.version_number??selection.version??''} onChange={event=>onVersion(Number(event.target.value))}>{detail.versions.map(version=><option key={version.id} value={version.version_number}>v{version.version_number} · {stateLabel[version.lifecycle_state]} · {generationModeLabel(version.provenance,detail.data_set.creation_pathway)}</option>)}</select>}</div>}
    <nav className="dataset-tabs" aria-label="Data set detail sections">{tabs.map(({id,label,icon:Icon})=><button key={id} className={selection.tab===id?'active':''} onClick={()=>onTab(id)}><Icon size={15}/>{label}</button>)}</nav>
    <div className="dataset-detail-content">{reservationOpen&&detail&&current&&<ReservationRequestForm datasetId={detail.data_set.id} workspaceId={detail.data_set.workspace_id} versionNumber={current.version_number} role={role} onClose={()=>setReservationOpen(false)}/>}{error&&<div className="alert alert-warning">{error}</div>}{!detail&&!error&&<div className="dataset-loading">Loading versioned metadata…</div>}{detail&&current&&selection.tab==='overview'&&<OverviewTab role={role} dataset={detail.data_set} version={current} failureMessage={detail.failure_message} sampleRecords={detail.sample_records} blockedRecordCount={detail.blocked_record_count} identityUnavailableRecordCount={detail.identity_unavailable_record_count} identityUnavailableGuidance={detail.identity_unavailable_guidance} maskedFields={detail.masked_fields} entityKeyFields={detail.entity_key_fields} testDataRequirements={detail.test_data_requirements} sourceSql={detail.source_sql} piiMaskingEnabled={piiMaskingEnabled} onExpiryUpdated={onExpiryUpdated}/>}{detail&&current&&selection.tab==='lineage'&&<LineageTab events={detail.lineage} versions={detail.versions} version={current}/>}{detail&&current&&selection.tab==='validation'&&<ValidationTab piiMaskingEnabled={piiMaskingEnabled} version={current} runs={detail.validation_runs} history={detail.status_history}/>}{detail&&current&&selection.tab==='access'&&<AccessTab access={detail.access} version={current} visibility={detail.data_set.visibility}/>}</div>
    <footer className="dataset-modal-footer"><span className="dataset-next-action">{role!=='Admin'&&<><strong>Next Action:</strong> {current?nextActionText(current,piiMaskingEnabled):'Loading data set status…'}</>}</span><div className="inline-actions">{role==='Test Data Engineer'&&current?.lifecycle_state==='draft'&&current.version_number===detail?.data_set.latest_version.version_number&&<button className="button button-confirm" onClick={onResume}><ArrowRight size={15}/>Resume</button>}{piiMaskingEnabled&&role==='Test Data Engineer'&&current?.lifecycle_state==='draft'&&current.validation_state==='passed'&&['requires_masking','not_assessed'].includes(current.privacy_state)&&<button className="button button-primary" onClick={onReviewPrivacy}><ShieldCheck size={15}/>Open PII &amp; Masking</button>}{current&&(role==='Test Data Engineer'&&current.lifecycle_state==='published'||role==='Test Data Engineer'&&current.lifecycle_state==='quarantined'||role==='Test Data Engineer'&&current.lifecycle_state==='failed'&&current.provenance.origin==='source_backed')&&<button className="button button-secondary" onClick={()=>onEditVersion(current)}><FilePlus2 size={15}/>{current.lifecycle_state==='failed'?'Retry generation':current.lifecycle_state==='quarantined'?'Create replacement version':'Edit as new version'}</button>}{role==='Test Data Engineer'&&current?.lifecycle_state==='ready'&&recordPrivacyComplete(current)&&<button className="button button-confirm" onClick={onPublish}><Check size={15}/>Publish version</button>}<button className="button button-secondary" onClick={onClose}>Close</button></div></footer>
  </section></div>
}

function ExpiryEditor({datasetId,version,onSaved}:{datasetId:string;version:DataSetVersion;onSaved:(version:DataSetVersion)=>void}){
  const [editing,setEditing]=useState(false)
  const [expiry,setExpiry]=useState(()=>version.expiry_at?toLocalDateTime(version.expiry_at):'')
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  useEffect(()=>{setExpiry(version.expiry_at?toLocalDateTime(version.expiry_at):'')},[version.id,version.expiry_at])
  async function save(){
    setBusy(true);setError('')
    try{
      const updated=await apiRequest<DataSetVersion>(`/datasets/${datasetId}/versions/${version.version_number}/expiry`,'Test Data Engineer',{
        method:'PATCH',body:JSON.stringify({expiry_at:expiry?new Date(expiry).toISOString():null}),
      })
      setEditing(false);onSaved(updated)
    }catch(cause){setError(cause instanceof Error?cause.message:'Unable to update the data set expiry')}
    finally{setBusy(false)}
  }
  return <div className="dataset-info dataset-expiry-editor"><span>Expires</span>{editing?<div className="dataset-expiry-controls"><label>Expiry date and time<input aria-label="Data set expiry date and time" type="datetime-local" value={expiry} onChange={event=>setExpiry(event.target.value)} disabled={busy}/></label><small>Use your local time. Clear the field to remove the expiry date.</small><div className="dataset-expiry-actions"><button type="button" className="button button-confirm compact" disabled={busy} onClick={()=>void save()}>{busy?<RefreshCw size={14} className="spin-icon"/>:<Save size={14}/>}{busy?'Saving…':'Save expiry'}</button><button type="button" className="button button-secondary compact" disabled={busy} onClick={()=>{setExpiry(version.expiry_at?toLocalDateTime(version.expiry_at):'');setEditing(false);setError('')}}><X size={14}/>Cancel</button></div></div>:<div className="dataset-expiry-display"><strong>{version.expiry_at?new Date(version.expiry_at).toLocaleString():'No expiry set'}</strong><button type="button" className="button button-secondary compact" onClick={()=>{setEditing(true);setError('')}}><Pencil size={14}/>Edit expiry</button></div>}{error&&<small className="dataset-expiry-error" role="alert">{error}</small>}</div>
}

function OverviewTab({role,dataset,version,failureMessage,sampleRecords,blockedRecordCount,identityUnavailableRecordCount,identityUnavailableGuidance,maskedFields,entityKeyFields,testDataRequirements,sourceSql,piiMaskingEnabled,onExpiryUpdated}:{role:HumanRole;dataset:DataSetSummary;version:DataSetVersion;failureMessage:string|null;sampleRecords:Array<Record<string,string|number|null>>;blockedRecordCount:number;identityUnavailableRecordCount:number;identityUnavailableGuidance:string|null;maskedFields:string[];entityKeyFields:string[];testDataRequirements:string|null;sourceSql:string|null;piiMaskingEnabled:boolean;onExpiryUpdated:(version:DataSetVersion)=>void}){
  const [expiryReached,setExpiryReached]=useState(()=>Boolean(version.expiry_at&&new Date(version.expiry_at).getTime()<=Date.now()))
  useEffect(()=>{
    if(!version.expiry_at){setExpiryReached(false);return}
    const delay=new Date(version.expiry_at).getTime()-Date.now()
    if(delay<=0){setExpiryReached(true);return}
    setExpiryReached(false)
    const timer=window.setTimeout(()=>setExpiryReached(true),Math.min(delay+25,2_147_483_647))
    return()=>window.clearTimeout(timer)
  },[version.id,version.expiry_at])
  const provenanceEntries=Object.entries(version.provenance).filter(([key])=>piiMaskingEnabled||key!=='pii_masking_required')
  const fields=version.fields?.length?version.fields:version.schema_scope.map((field,ordinal)=>({id:`legacy-${ordinal}`,field_name:field.field,field_path:field.json_path??(field.entity?`${field.entity}.${field.field}`:field.field),data_type:field.data_type,required:field.required,target_field_path:null,constraint_spec:{},ordinal}))
  const groupedFields=fields.reduce<Record<string,typeof fields>>((groups,field)=>{
    const scopeField=version.schema_scope.find(item=>item.field===field.field_name||item.json_path===field.field_path)
    const pathGroup=field.field_path.includes('.')&&!field.field_path.startsWith('$')?field.field_path.split('.')[0]:null
    const groupName=scopeField?.entity||pathGroup||'Other output fields'
    groups[groupName]=[...(groups[groupName]??[]),field]
    return groups
  },{})
  const previewFields=version.selected_fields?.length?version.selected_fields:fields.map(field=>field.field_name)
  const sourcePreviewReady=recordPrivacyComplete(version)&&version.privacy_state==='compliant'&&version.validation_state==='passed'
  return <div className="dataset-overview">
    {failureMessage&&<div className="alert alert-warning dataset-failure-callout" role="alert"><strong>{version.lifecycle_state==='quarantined'?'Processing failed · output quarantined':'Generation failed'}</strong><span>{failureMessage}</span></div>}
    <div className="dataset-overview-grid">
      <Info label="Owner" value={dataset.owner_id}/>
      <Info label="Data set ID" value={dataset.reference_code}/>
      <Info label="Created" value={new Date(dataset.created_at).toLocaleString()}/>
      <Info label="Creation pathway" value={creationPathwaySourceLabel(dataset,version)}/>
      <Info label="Generation mode" value={generationModeLabel(version.provenance,dataset.creation_pathway)}/>
      <Info label="Record Count" value={version.record_count?.toLocaleString()??'Not recorded'}/>
      {blockedRecordCount>0&&<Info label="Unavailable records" value={`${blockedRecordCount.toLocaleString()} · active lock conflict or protected identity needs regeneration`}/>}
      {piiMaskingEnabled&&<Info label="Privacy state" value={humanize(version.privacy_state)}/>}
      <Info label="Publication state" value={humanize(version.publication_state)}/>
      {role==='Test Data Engineer'&&version.publication_state==='published'&&(version.lifecycle_state==='published'||version.lifecycle_state==='expired'&&!version.expired_at)?<ExpiryEditor datasetId={dataset.id} version={version} onSaved={onExpiryUpdated}/>:<Info label="Expires" value={version.expiry_at?new Date(version.expiry_at).toLocaleString():'No expiry set'}/>}
      <Info label="Permitted use" value={version.permitted_use.join(', ')||'Not specified'}/>
    </div>
    {testDataRequirements&&<details className="dataset-requirements-panel"><summary>Test data requirements</summary><p>{testDataRequirements}</p></details>}
    {identityUnavailableRecordCount>0&&<div className="alert alert-warning dataset-identity-warning" role="status"><strong>{identityUnavailableRecordCount.toLocaleString()} records could not be matched to approved source identities.</strong> These records are hidden from preview and downloads. {identityUnavailableGuidance||'Review the approved entity key and source field mapping, then create and regenerate a new data set version.'}</div>}
     {expiryReached&&<div className="alert alert-warning" role="status">This version has expired. Record previews and data access are unavailable until a TDE sets a new future expiry date.</div>}
     {sampleRecords.length>0&&sourcePreviewReady&&!expiryReached&&<section className="dataset-provenance"><h3>Record preview</h3><p>{piiMaskingEnabled?'Preview rows are available after the required privacy checks complete.':'Preview rows come from records stored for this data-set version.'}</p><div className="table-shell"><table><thead><tr>{previewFields.map(field=><th key={field}><FieldName value={humanize(field)} masked={piiMaskingEnabled&&maskedFields.includes(field)} entityKey={entityKeyFields.includes(field)}/></th>)}</tr></thead><tbody>{sampleRecords.map((row,index)=><tr key={index}>{previewFields.map(field=><td key={field}>{String(row[field]??'—')}</td>)}</tr>)}</tbody></table></div></section>}
    {piiMaskingEnabled&&version.provenance.origin==='source_backed'&&!sourcePreviewReady&&<section className="dataset-provenance dataset-preview-gated"><h3>Record preview</h3><p>Source-backed record values are available here only after PII masking completes. No source records are stored in TIGR before then.</p></section>}
    <section className="dataset-schema-block"><div className="dataset-schema-heading"><div><h3>Schema and output fields</h3><p>Fields are grouped by source object. Expand a group to view its fields.</p></div><span>{fields.length} fields</span></div>{fields.length?<div className="dataset-schema-accordions">{Object.entries(groupedFields).map(([groupName,groupFields])=><details className="dataset-schema-accordion" key={groupName}><summary><strong>{groupName}</strong><span>{groupFields.length} fields</span></summary><div className="dataset-schema-accordion-content"><table><thead><tr><th>Schema field</th><th>Output field</th><th>Type</th><th>Requirement</th></tr></thead><tbody>{groupFields.map(field=>{const outputField=field.target_field_path||field.field_name;const scopeField=version.schema_scope.find(item=>item.field===field.field_name||item.json_path===field.field_path);const sourceField=scopeField?.source_field&&scopeField.entity?`${scopeField.entity}.${scopeField.source_field}`:field.field_path;return <tr key={field.id}><td><strong>{sourceField}</strong></td><td><FieldName value={outputField} masked={piiMaskingEnabled&&maskedFields.includes(outputField)}/></td><td><code>{field.data_type}</code></td><td><span className={`dataset-field-requirement ${field.required?'required':'optional'}`}>{field.required?'Required':'Optional'}</span></td></tr>})}</tbody></table></div></details>)}</div>:<div className="dataset-no-fields">No schema fields have been recorded for this draft.</div>}</section>
    <SqlSection sql={sourceSql} versionNumber={version.version_number}/>
    {role!=='QA Engineer'&&<details className="dataset-provenance dataset-provenance-accordion"><summary><h3>Provenance references</h3></summary><div className="dataset-provenance-accordion-content">{provenanceEntries.length?<div className="dataset-meta-list">{provenanceEntries.map(([key,value])=><Info key={key} label={humanize(key)} value={value}/>)}</div>:<p>No source, scenario, job, or artifact references have been attached yet.</p>}</div></details>}
  </div>
}
const sqlKeywords=new Set('SELECT DISTINCT FROM WHERE AND OR NOT IN IS NULL LIKE ILIKE BETWEEN ORDER BY ASC DESC LIMIT OFFSET JOIN INNER LEFT RIGHT FULL OUTER ON AS GROUP HAVING UNION ALL CASE WHEN THEN ELSE END EXISTS TRUE FALSE'.split(' '))
const sqlFunctions=new Set('COUNT SUM AVG MIN MAX COALESCE NULLIF LOWER UPPER DATE_TRUNC NOW'.split(' '))
function SqlSection({sql,versionNumber}:{sql:string|null;versionNumber:number}){
  const [copied,setCopied]=useState(false)
  const [copyError,setCopyError]=useState('')
  async function copySql(){
    if(!sql)return
    try{
      await navigator.clipboard.writeText(sql)
      setCopied(true);setCopyError('')
      window.setTimeout(()=>setCopied(false),2000)
    }catch{
      setCopied(false);setCopyError('Clipboard access is unavailable in this browser.')
      window.setTimeout(()=>setCopyError(''),3500)
    }
  }
  return <section className="dataset-sql-panel"><div className="dataset-sql-heading"><span><Code size={17}/></span><div><h3>SQL used to generate this version</h3><p>Saved source query · version {versionNumber}</p></div>{sql&&<button type="button" className="button button-secondary dataset-sql-copy" onClick={()=>void copySql()} aria-label={copied?'SQL copied':'Copy SQL'} title={copied?'Copied':'Copy SQL'}>{copied?<Check size={14}/>:<Copy size={14}/>}<span>{copied?'Copied':'Copy SQL'}</span></button>}</div>{copyError&&<div className="dataset-sql-copy-error" role="status">{copyError}</div>}{sql?<pre className="dataset-sql-code"><code>{highlightSql(sql)}</code></pre>:<div className="dataset-no-fields">No saved SQL is associated with this version.</div>}</section>
}
function highlightSql(sql:string){const tokens=sql.match(/--[^\n]*|\/\*[\s\S]*?\*\/|'(?:''|[^'])*'|"(?:""|[^"])*"|\b\d+(?:\.\d+)?\b|[A-Za-z_][\w$]*|<=|>=|<>|!=|:=|[-+*/%=<>]|\s+|./g)??[];return tokens.map((token,index)=>{let className='';const upper=token.toUpperCase();if(token.startsWith('--')||token.startsWith('/*'))className='sql-token-comment';else if(token.startsWith("'")||token.startsWith('"'))className='sql-token-string';else if(/^\d/.test(token))className='sql-token-number';else if(sqlKeywords.has(upper))className='sql-token-keyword';else if(sqlFunctions.has(upper))className='sql-token-function';else if(/^(<=|>=|<>|!=|:=|[-+*/%=<>])$/.test(token))className='sql-token-operator';return className?<span className={className} key={index}>{token}</span>:token})}
function FieldName({value,masked,entityKey=false}:{value:string;masked:boolean;entityKey?:boolean}){return <span className="dataset-field-name-label">{masked&&<span className="dataset-field-privacy-indicator" role="img" aria-label="PII masking applied" title="PII masking applied"><ShieldCheck className="dataset-field-privacy-icon" size={14}/></span>}{entityKey&&<span className="dataset-field-entity-key-indicator" role="img" aria-label="Entity key used for record locking" title="Entity key used for record locking"><KeyRound className="dataset-field-entity-key-icon" size={14}/></span>}{value}</span>}
function LineageTab({events,versions,version}:{events:LineageEvent[];versions:DataSetVersion[];version:DataSetVersion}){
  const eventsByVersion=events.reduce<Record<number,LineageEvent[]>>((groups,event)=>{
    groups[event.version_number]=[...(groups[event.version_number]??[]),event]
    return groups
  },{})
  const orderedVersions=[...versions].sort((left,right)=>right.version_number-left.version_number)
  return <div className="lineage-tab"><div className="lineage-intro"><div><h3>Version lineage</h3><p>Each version has its own event timeline. Expand a version to inspect its lineage and evidence.</p></div><span>{events.length} events · {versions.length} versions</span></div>{orderedVersions.length?<div className="lineage-version-accordions">{orderedVersions.map(lineageVersion=>{
    const versionEvents=eventsByVersion[lineageVersion.version_number]??[]
    return <details className="lineage-version-accordion" key={lineageVersion.id} open={lineageVersion.version_number===version.version_number}>
      <summary><span><strong><span className={`status-badge dataset-version-status lifecycle-${lineageVersion.lifecycle_state}`}><span>v{lineageVersion.version_number}</span><span className="dataset-version-separator" aria-hidden="true">·</span><span>{stateLabel[lineageVersion.lifecycle_state]}</span></span></strong><small>{lineageVersion.record_count?.toLocaleString()??'—'} records</small></span><span className="lineage-version-count">{versionEvents.length} {versionEvents.length===1?'event':'events'}</span></summary>
      <div className="lineage-version-content">{versionEvents.length?<div className="lineage-timeline">{versionEvents.map((event,index)=><article className="lineage-event" key={event.id}><div className="lineage-marker">{index===versionEvents.length-1?<Check size={14}/>:<span>{event.sequence_number}</span>}</div><div className="lineage-card"><div className="lineage-card-head"><div><strong>{humanize(event.event_type)}</strong><small>Sequence {event.sequence_number} · {humanize(event.stage)}</small></div><span className={`status-badge ${event.outcome==='succeeded'?'status-success':event.outcome==='warning'?'status-warning':'status-neutral'}`}><i/>{event.outcome}</span></div><div className="lineage-ref-grid">{event.source_reference&&<Info label="Source reference" value={event.source_reference}/ >}{event.artifact_reference&&<Info label="Artifact reference" value={event.artifact_reference}/ >}{event.mapping_version&&<Info label="Mapping version" value={event.mapping_version}/ >}{event.scenario_version&&<Info label="Scenario version" value={event.scenario_version}/ >}{event.job_id&&<Info label="Job reference" value={event.job_id}/ >}{event.policy_version&&<Info label="Policy version" value={event.policy_version}/ >}{event.validation_version&&<Info label="Validation version" value={event.validation_version}/ >}</div>{Object.keys(event.safe_evidence).length>0&&<div className="lineage-evidence"><span>Safe evidence</span><code>{Object.entries(event.safe_evidence).map(([key,value])=>`${humanize(key)}: ${value??'—'}`).join(' · ')}</code></div>}<div className="lineage-links"><span>Parents: {event.parent_event_ids.length?event.parent_event_ids.map(id=>id.slice(0,8)).join(', '):'Origin'}</span><span>{new Date(event.occurred_at).toLocaleString()}</span></div></div></article>)}</div>:<div className="dataset-no-fields">No lineage events are recorded for this version.</div>}</div>
    </details>
  })}</div>:<div className="dataset-no-fields">No data set versions are available.</div>}</div>
}

function ValidationTab({piiMaskingEnabled,version,runs,history}:{piiMaskingEnabled:boolean;version:DataSetVersion;runs:DatasetDetail['validation_runs'];history:DatasetDetail['status_history']}){return <div className="dataset-validation"><div className="validation-state-grid">{piiMaskingEnabled&&<StateTile icon={ShieldCheck} label="Privacy" value={humanize(version.privacy_state)} tone={version.privacy_state==='compliant'?'good':version.privacy_state==='blocked'?'bad':'neutral'}/>}<StateTile icon={Check} label="Validation" value={humanize(version.validation_state)} tone={version.validation_state==='passed'?'good':version.validation_state==='failed'?'bad':'neutral'}/><StateTile icon={Activity} label="Readiness" value={stateLabel[version.lifecycle_state]} tone={version.lifecycle_state==='ready'||version.lifecycle_state==='published'?'good':version.lifecycle_state==='failed'||version.lifecycle_state==='quarantined'?'bad':'neutral'}/></div><div className="validation-evidence-card"><div><h3>Validation runs</h3><p>Structured checks and safe summaries recorded for this exact version.</p></div>{runs.length?<div className="dataset-meta-list">{runs.map(run=><article className="dataset-info" key={run.id}><span>{humanize(run.validation_type)} · {humanize(run.status)} · {new Date(run.started_at).toLocaleString()}</span><strong>{String(run.summary.summary??'Validation completed')}</strong>{run.results.map((result,index)=><small key={`${result.check_code}-${index}`}>{humanize(result.check_code)} · {humanize(result.status)}{result.field_path?` · ${result.field_path}`:''} · {result.safe_message}</small>)}</article>)}</div>:<div className="dataset-no-fields">No structured validation run has been recorded.</div>}</div><div className="validation-evidence-card"><div><h3>Version status history</h3><p>Append-only lifecycle changes for this version.</p></div>{history.length?<div className="dataset-meta-list">{history.map((row,index)=><div className="dataset-info" key={`${row.changed_at}-${index}`}><span>{row.from_status?`${humanize(row.from_status)} → `:''}{humanize(row.to_status)} · {new Date(row.changed_at).toLocaleString()}</span><strong>{row.reason||'Status recorded'}</strong><small>Changed by {row.changed_by}</small></div>)}</div>:<div className="dataset-no-fields">No status transitions have been recorded.</div>}</div>{Object.keys(version.validation_evidence).length>0&&<div className="validation-evidence-card"><div><h3>Additional safe evidence</h3><p>Operational metadata supplied by the generation or validation workflow.</p></div><dl>{Object.entries(version.validation_evidence).map(([key,value])=><div key={key}><dt>{humanize(key)}</dt><dd>{String(value??'—')}</dd></div>)}</dl></div>}{version.lifecycle_state==='ready'&&<div className="validation-ready-note"><Check size={16}/> All publication gates are satisfied. A Test Data Engineer can publish this version.</div>}</div>}

function AccessTab({access,version,visibility}:{access:DatasetDetail['access'];version:DataSetVersion;visibility?:'private'|'public'|null}){return <div className="dataset-access"><div className="access-context-card"><span><LockKeyhole size={18}/></span><div><strong>Workspace-scoped access</strong><p>{visibility==='private'?'Private data sets are available to their owner without a reservation.':'Shared data sets are available under workspace membership and publication rules.'} Record-level locks can still protect rows held by another QA user.</p></div></div><div className="dataset-overview-grid"><Info label="Owner" value={access.owner_id}/><Info label="Workspace" value={access.workspace_id}/><Info label="Permitted use" value={version.permitted_use.join(', ')||'Not specified'}/><Info label="Expiry" value={version.expiry_at?new Date(version.expiry_at).toLocaleString():'No expiry set'}/></div><div className="access-privacy-note"><ShieldCheck size={16}/>{visibility==='private'?'The private owner can view and download published data without a reservation.':'QA access to shared data requires an approved active reservation and lock.'}</div></div>}

function StateTile({icon:Icon,label,value,tone}:{icon:typeof Check;label:string;value:string;tone:string}){return <div className={`state-tile state-${tone}`}><span><Icon size={17}/></span><small>{label}</small><strong>{value}</strong></div>}
function Info({label,value}:{label:string;value:ReactNode}){return <div className="dataset-info"><span>{label}</span><strong>{value==null?'—':isValidElement(value)?value:metadataDisplay(value)}</strong></div>}
function metadataDisplay(value:unknown):string{
  if(value===null||value===undefined||value==='')return '—'
  if(typeof value==='string'||typeof value==='number'||typeof value==='boolean')return String(value)
  if(Array.isArray(value))return value.map(metadataDisplay).join(' · ')
  if(typeof value==='object')return Object.entries(value as Record<string,unknown>).map(([key,item])=>`${humanize(key)}: ${metadataDisplay(item)}`).join(' · ')
  return String(value)
}
function humanize(value:string){return String(value??'').replaceAll('_',' ').replaceAll('.', ' · ').replace(/\b\w/g,char=>char.toUpperCase())}
