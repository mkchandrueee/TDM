import { Component, useCallback, useEffect, useRef, useState, type ErrorInfo, type FormEvent, type ReactNode } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Activity, ArrowRight, Check, Database, Download, Eye, FilePlus2, GitBranch, LockKeyhole, Plus, Search, ShieldCheck, X } from 'lucide-react'
import { apiDownload, apiRequest, type HumanRole } from './api'
import { creationPathwayLabel, generationModeClass, generationModeLabel } from './datasetLabels'

type Lifecycle = 'draft'|'ready'|'published'|'failed'|'quarantined'|'expired'
type DataSetVersion = {id:string;dataset_id:string;workspace_id:string;version_number:number;lifecycle_state:Lifecycle;schema_scope:Array<{field:string;data_type:string;entity?:string|null;json_path?:string|null;required:boolean}>;fields:Array<{id:string;field_name:string;field_path:string;data_type:string;required:boolean;target_field_path:string|null;constraint_spec:Record<string,unknown>;ordinal:number}>;selected_fields:string[];generation_settings:Record<string,string|number>;record_count:number|null;provenance:Record<string,string>;privacy_state:string;validation_state:string;publication_state:string;expiry_at:string|null;permitted_use:string[];validation_evidence:Record<string,string|number|boolean|null>;published_at:string|null;expired_at:string|null;created_by:string;created_at:string}
type DataSetSummary = {id:string;reference_code:string;workspace_id:string;name:string;description:string|null;owner_id:string;creation_pathway:'scenario_to_data'|'source_to_data';latest_version:DataSetVersion;created_at:string;updated_at:string}
type LineageEvent = {id:string;dataset_id:string;version_id:string;version_number:number;sequence_number:number;event_type:string;stage:string;source_reference:string|null;artifact_reference:string|null;mapping_version:string|null;scenario_version:string|null;job_id:string|null;policy_version:string|null;validation_version:string|null;actor_id:string;actor_type:string;occurred_at:string;outcome:string;safe_evidence:Record<string,string|number|boolean|null>;parent_event_ids:string[];child_event_ids:string[]}
type DatasetDetail = {data_set:DataSetSummary;selected_version:DataSetVersion;versions:DataSetVersion[];test_data_requirements:string|null;masked_fields:string[];lineage:LineageEvent[];blocked_record_count:number;status_history:Array<{from_status:string|null;to_status:string;changed_by:string;changed_at:string;reason:string|null}>;validation_runs:Array<{id:string;validation_type:string;status:string;started_at:string;completed_at:string|null;summary:Record<string,unknown>;results:Array<{check_code:string;severity:string;status:string;field_path:string|null;safe_message:string;observed_count:number|null}>}>;access:{owner_id:string;permitted_use:string[];workspace_id:string};sample_records:Array<Record<string,string|number|null>>}
type Tab = 'overview'|'lineage'|'validation'|'access'
const stateLabel:Record<Lifecycle,string>={draft:'Draft',ready:'Ready',published:'Published',failed:'Failed',quarantined:'Quarantined',expired:'Expired'}

class DatasetModalBoundary extends Component<{children:ReactNode;onClose:()=>void},{failed:boolean;message:string}>{
  state={failed:false,message:''}
  static getDerivedStateFromError(error:Error){return {failed:true,message:error.message||'Unknown rendering error'}}
  componentDidCatch(error:Error,info:ErrorInfo){console.error('Data set details failed to render',error,info.componentStack)}
  render(){
    if(this.state.failed)return <div className="dataset-overlay"><section className="dataset-modal dataset-detail-modal" role="alertdialog" aria-modal="true" aria-label="Data set details unavailable"><header className="dataset-modal-header"><div><div className="eyebrow">DATA SET DETAILS</div><h2>Unable to display this data set</h2><p>The details view encountered a display error.</p></div><button type="button" className="icon-button" aria-label="Close data set details" onClick={this.props.onClose}><X size={18}/></button></header><div className="dataset-detail-content"><div className="alert alert-warning">{this.state.message}</div></div><footer className="dataset-modal-footer"><span>Close this message to return to the catalog.</span><button type="button" className="button button-secondary" onClick={this.props.onClose}>Close</button></footer></section></div>
    return this.props.children
  }
}

export default function DataSetsPage({role,workspaceId}:{role:HumanRole;workspaceId:string}){
  const navigate=useNavigate()
  const [searchParams]=useSearchParams()
  const requestedDatasetId=searchParams.get('dataset_id'),requestedVersion=searchParams.get('version_number')
  const [items,setItems]=useState<DataSetSummary[]>([])
  const [loading,setLoading]=useState(false)
  const [error,setError]=useState('')
  const [search,setSearch]=useState('')
  const [query,setQuery]=useState('')
  const [state,setState]=useState('')
  const [pathway,setPathway]=useState('')
  const [showCreate,setShowCreate]=useState(false)
  const [selection,setSelection]=useState<{id:string;version?:number;tab:Tab}|null>(null)
  const [editSource,setEditSource]=useState<DataSetVersion|null>(null)
  const [detail,setDetail]=useState<DatasetDetail|null>(null)
  const [detailError,setDetailError]=useState('')
  const [canManage,setCanManage]=useState(role!=='QA Engineer')

  const load=useCallback(async()=>{
    if(!workspaceId){setItems([]);return}
    setLoading(true);setError('')
    const params=new URLSearchParams({workspace_id:workspaceId,page:'1',page_size:'100'})
    if(query.trim())params.set('search',query.trim())
    if(state)params.set('lifecycle_state',state)
    if(pathway)params.set('creation_pathway',pathway)
    try{const result=await apiRequest<{items:DataSetSummary[];total:number}>(`/datasets?${params}`,role);setItems(result.items)}
    catch(cause){setError(cause instanceof Error?cause.message:'Unable to load data sets')}
    finally{setLoading(false)}
  },[workspaceId,role,query,state,pathway])
  useEffect(()=>{void load()},[load])
  useEffect(()=>{setCanManage(role!=='QA Engineer')},[role])
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
    apiRequest<DatasetDetail>(`/datasets/${selection.id}${version}`,role)
      .then(value=>{if(active){setDetail(value);if(!selection.version)setSelection(current=>current?.id===selection.id?{...current,version:value.selected_version.version_number}:current)}})
      .catch(cause=>{if(active)setDetailError(cause instanceof Error?cause.message:'Unable to load data set details')})
    return()=>{active=false}
  },[selection?.id,selection?.version,role])

  function open(item:DataSetSummary,tab:Tab='overview'){setSelection({id:item.id,version:item.latest_version.version_number,tab})}
  async function publishCurrent(){
    if(!detail||!selection)return
    setDetailError('')
    try{await apiRequest(`/datasets/${detail.data_set.id}/versions/${detail.selected_version.version_number}/publish`,role,{method:'POST'});await load();setDetail(await apiRequest<DatasetDetail>(`/datasets/${detail.data_set.id}?version_number=${detail.selected_version.version_number}`,role))}
    catch(cause){setDetailError(cause instanceof Error?cause.message:'Unable to publish this version')}
  }
  async function createEditedVersion(versionNumber:number){
    if(!selection)return
    const datasetId=selection.id
    setEditSource(null)
    await load()
    setSelection({id:datasetId,version:versionNumber,tab:'overview'})
  }
  async function downloadCurrent(){
    if(!detail)return
    setDetailError('')
    try{const format=artifactFormat(detail.selected_version);const blob=await apiDownload(`/datasets/${detail.data_set.id}/versions/${detail.selected_version.version_number}/artifact`,role);saveDownload(blob,`dataset-${detail.data_set.reference_code}-v${detail.selected_version.version_number}.${format}`)}
    catch(cause){setDetailError(cause instanceof Error?cause.message:'Unable to download this artifact')}
  }
  async function downloadRow(item:DataSetSummary){
    setError('')
    try{const format=artifactFormat(item.latest_version);const blob=await apiDownload(`/datasets/${item.id}/versions/${item.latest_version.version_number}/artifact`,role);saveDownload(blob,`dataset-${item.reference_code}-v${item.latest_version.version_number}.${format}`)}
    catch(cause){setError(cause instanceof Error?cause.message:'Unable to download this data set')}
  }
  const publishedCount=items.filter(item=>item.latest_version.lifecycle_state==='published').length
  if(!workspaceId)return <section className="panel dataset-empty"><Database size={24}/><h2>Select a workspace</h2><p>Data sets are scoped to a workspace. Select one in the top bar to browse its catalog.</p></section>
  return <div className="datasets-page">
    <section className="dataset-intro panel"><div><div className="eyebrow">VERSIONED CATALOG</div><h2>Data sets</h2><p>Browse workspace data sets, release state, validation, and lineage.</p></div>{canManage&&<button className="button button-confirm" onClick={()=>setShowCreate(true)}><Plus size={16}/>Create draft</button>}</section>
    <section className="dataset-summary-row"><div className="dataset-summary-card"><span><Database size={17}/></span><div><strong>{items.length}</strong><small>Visible data sets</small></div></div><div className="dataset-summary-card"><span><Check size={17}/></span><div><strong>{publishedCount}</strong><small>Published latest versions</small></div></div><div className="dataset-summary-card"><span><ShieldCheck size={17}/></span><div><strong>Role scoped</strong><small>Filtered before totals and search</small></div></div></section>
    <section className="panel dataset-catalog"><div className="dataset-toolbar"><div><h2>Workspace catalog</h2><p>Latest version for each data set visible to your role.</p></div><div className="inline-actions"><button className="icon-button" aria-label="Refresh data sets" onClick={()=>void load()}><Activity size={17}/></button></div></div>
      <form className="dataset-filters" onSubmit={event=>{event.preventDefault();setQuery(search)}}><label className="dataset-search"><Search size={16}/><input aria-label="Search data sets" value={search} onChange={event=>setSearch(event.target.value)} placeholder="Search name, ID, description, or owner"/></label><select aria-label="Filter by lifecycle" value={state} onChange={event=>setState(event.target.value)}><option value="">All states</option>{Object.entries(stateLabel).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select><select aria-label="Filter by creation pathway" value={pathway} onChange={event=>setPathway(event.target.value)}><option value="">All pathways</option><option value="source_to_data">Source to Data</option><option value="scenario_to_data">Scenario to Data</option></select><button className="button button-secondary">Filter</button></form>
      {error&&<div className="alert alert-warning">{error}</div>}
      <div className="table-shell dataset-table"><table><thead><tr><th>Data set</th><th>Generation</th><th>Latest version</th><th>Record Count</th><th>Privacy</th><th>Validation</th><th>Updated</th><th/></tr></thead><tbody>{items.map(item=><tr key={item.id}><td><button className="dataset-name-button" onClick={()=>open(item)}><strong>{item.name}</strong><small><em>{catalogRequirementLabel(item)}</em></small><small className="dataset-reference-code">{item.reference_code}</small></button></td><td><span className={`dataset-mode-value ${generationModeClass(item.latest_version.provenance,item.creation_pathway)}`}>{generationModeLabel(item.latest_version.provenance,item.creation_pathway)}</span></td><td><span className={`status-badge dataset-version-status lifecycle-${item.latest_version.lifecycle_state}`}><span>v{item.latest_version.version_number}</span><span className="dataset-version-separator" aria-hidden="true">·</span><span>{stateLabel[item.latest_version.lifecycle_state]}</span></span></td><td>{item.latest_version.record_count?.toLocaleString()??'—'}</td><td>{humanize(item.latest_version.privacy_state)}</td><td>{humanize(item.latest_version.validation_state)}</td><td>{new Date(item.updated_at).toLocaleDateString()}</td><td><div className="dataset-row-actions"><button type="button" className="icon-button dataset-row-icon-button dataset-lineage-button" aria-label="View lineage" title="View lineage" onClick={()=>open(item,'lineage')}><GitBranch size={16}/></button><button type="button" className="icon-button dataset-row-icon-button dataset-view-button" aria-label="View details" title="View details" onClick={()=>open(item)}><Eye size={16}/></button><button className="text-button dataset-download-button" disabled={!downloadEligible(item.latest_version)} title={downloadUnavailableReason(item.latest_version)??'Download this version as '+artifactFormat(item.latest_version).toUpperCase()} onClick={()=>void downloadRow(item)}><Download size={14}/>{artifactFormat(item.latest_version).toUpperCase()}</button></div></td></tr>)}{!loading&&!items.length&&<tr><td colSpan={8} className="table-empty">No data sets match this workspace and filter.</td></tr>}{loading&&<tr><td colSpan={8} className="table-empty">Loading authorized data sets…</td></tr>}</tbody></table></div>
    </section>
    {showCreate&&<CreateDraft role={role} workspaceId={workspaceId} onClose={()=>setShowCreate(false)} onCreated={async(id,pathway)=>{setShowCreate(false);navigate(`${pathway==='source_to_data'?'/source-to-data':'/scenario-to-data'}?draft_id=${encodeURIComponent(id)}`)}}/>}
    {selection&&<DatasetModalBoundary key={`${selection.id}:${selection.version??'latest'}`} onClose={()=>setSelection(null)}><DatasetModal role={role} selection={selection} detail={detail} error={detailError} onClose={()=>setSelection(null)} onTab={tab=>setSelection(current=>current?{...current,tab}:current)} onVersion={version=>setSelection(current=>current?{...current,version}:current)} onPublish={()=>void publishCurrent()} onDownload={()=>void downloadCurrent()} onEditVersion={version=>setEditSource(version)} onReviewPrivacy={()=>{if(detail)navigate(`/privacy?dataset_id=${encodeURIComponent(detail.data_set.id)}`)}} onResume={()=>{if(!detail)return;const path=detail.data_set.creation_pathway==='source_to_data'?'/source-to-data':'/scenario-to-data';navigate(`${path}?draft_id=${encodeURIComponent(detail.data_set.id)}`);setSelection(null)}}/></DatasetModalBoundary>}
    {editSource&&detail&&<EditPublishedVersion role={role} dataset={detail.data_set} version={editSource} onClose={()=>setEditSource(null)} onCreated={createEditedVersion}/>}
  </div>
}

export function DataSetDetailsDialog({role,datasetId,versionNumber,onClose,onPublished}:{role:HumanRole;workspaceId:string;datasetId:string;versionNumber?:number;onClose:()=>void;onPublished?:()=>void}){
  const navigate=useNavigate()
  const [selection,setSelection]=useState<{id:string;version?:number;tab:Tab}>({id:datasetId,version:versionNumber,tab:'overview'})
  const [detail,setDetail]=useState<DatasetDetail|null>(null)
  const [error,setError]=useState('')
  const [editSource,setEditSource]=useState<DataSetVersion|null>(null)
  useEffect(()=>{
    let active=true
    setDetail(null);setError('')
    const version=selection.version??versionNumber
    apiRequest<DatasetDetail>(`/datasets/${selection.id}${version?`?version_number=${version}`:''}`,role)
      .then(value=>{if(active){setDetail(value);if(!version)setSelection(current=>current.id===selection.id?{...current,version:value.selected_version.version_number}:current)}})
      .catch(cause=>{if(active)setError(cause instanceof Error?cause.message:'Unable to load data set details')})
    return()=>{active=false}
  },[selection.id,selection.version,role,versionNumber])
  async function publishCurrent(){
    if(!detail)return
    try{await apiRequest(`/datasets/${detail.data_set.id}/versions/${detail.selected_version.version_number}/publish`,role,{method:'POST'});setDetail(await apiRequest<DatasetDetail>(`/datasets/${detail.data_set.id}?version_number=${detail.selected_version.version_number}`,role));onPublished?.()}
    catch(cause){setError(cause instanceof Error?cause.message:'Unable to publish this version')}
  }
  async function downloadCurrent(){
    if(!detail)return
    try{const current=detail.selected_version;const blob=await apiDownload(`/datasets/${detail.data_set.id}/versions/${current.version_number}/artifact`,role);saveDownload(blob,`dataset-${detail.data_set.id}-v${current.version_number}.${artifactFormat(current)}`)}
    catch(cause){setError(cause instanceof Error?cause.message:'Unable to download this artifact')}
  }
  return <>
    <DatasetModal role={role} selection={selection} detail={detail} error={error} onClose={onClose} onTab={tab=>setSelection(current=>({...current,tab}))} onVersion={version=>setSelection(current=>({...current,version}))} onPublish={()=>void publishCurrent()} onDownload={()=>void downloadCurrent()} onEditVersion={version=>setEditSource(version)} onReviewPrivacy={()=>{if(!detail)return;onClose();navigate(`/privacy?dataset_id=${encodeURIComponent(detail.data_set.id)}`)}} onResume={()=>{if(!detail)return;const path=detail.data_set.creation_pathway==='source_to_data'?'/source-to-data':'/scenario-to-data';navigate(`${path}?draft_id=${encodeURIComponent(detail.data_set.id)}`);onClose()}}/>
    {editSource&&detail&&<EditPublishedVersion role={role} dataset={detail.data_set} version={editSource} onClose={()=>setEditSource(null)} onCreated={async version=>{setEditSource(null);setSelection(current=>({...current,version,tab:'overview'}))}}/>}
  </>
}

function recordPrivacyComplete(version:DataSetVersion){return version.provenance.origin!=='source_backed'||Boolean(version.validation_evidence.masking_run_id)}
function downloadUnavailableReason(version:DataSetVersion){
  if(version.lifecycle_state!=='published'||version.publication_state!=='published')return 'Download is available after this version is published.'
  if(version.validation_state!=='passed')return 'Download is unavailable because validation has not passed.'
  if(version.privacy_state!=='compliant')return 'Download is unavailable until privacy review is compliant.'
  if(!recordPrivacyComplete(version))return 'Download is unavailable because this source-backed version has no completed PII masking record.'
  if(version.expiry_at&&new Date(version.expiry_at).getTime()<=Date.now())return 'Download is unavailable because this version has expired.'
  return null
}
function downloadEligible(version:DataSetVersion){return downloadUnavailableReason(version)===null}
function nextActionText(version:DataSetVersion){
  if(version.lifecycle_state==='expired')return 'Expired → Create a new version before use.'
  if(version.lifecycle_state==='quarantined'||version.lifecycle_state==='failed'||version.privacy_state==='blocked')return `${stateLabel[version.lifecycle_state]} → Review validation details, correct the issue, and create a fresh draft.`
  if(version.provenance.origin==='source_backed'&&!version.validation_evidence.masking_run_id)return version.lifecycle_state==='published'?'Published → Create a new version and complete PII masking before use.':`${stateLabel[version.lifecycle_state]} → Complete PII masking before preview or publication.`
  if(version.lifecycle_state==='draft'&&version.validation_state!=='passed')return 'Draft → Complete validation, then scan fields and complete PII masking.'
  if(version.lifecycle_state==='draft'&&['requires_masking','not_assessed'].includes(version.privacy_state))return 'Draft → Scan Fields and complete PII masking.'
  if(version.lifecycle_state==='ready')return 'Ready → Publish this data set.'
  if(version.lifecycle_state==='published'&&version.privacy_state==='compliant')return 'Published → Ready to use.'
  return `${stateLabel[version.lifecycle_state]} → Review the Validation and Privacy tabs for the next required action.`
}
function artifactFormat(version:DataSetVersion):'csv'|'json'{const reference=version.validation_evidence?.artifact_ref;if(typeof reference==='string'&&/\.csv(?:$|[?#])/i.test(reference))return 'csv';return version.generation_settings?.output_format==='csv'?'csv':'json'}
function saveDownload(blob:Blob,filename:string){const url=URL.createObjectURL(blob);const anchor=document.createElement('a');anchor.href=url;anchor.download=filename;anchor.click();URL.revokeObjectURL(url)}
function catalogRequirementLabel(item:DataSetSummary){if(item.creation_pathway==='scenario_to_data')return `Scenario: ${item.description||'No requirement description'}`;return `Source: ${item.latest_version.provenance.source_object||'—'}`}

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
  return <div className="dataset-overlay" role="presentation" onMouseDown={event=>{if(event.target===event.currentTarget)onClose()}}><section className="dataset-modal dataset-create-modal" role="dialog" aria-modal="true" aria-labelledby="create-dataset-title"><header className="dataset-modal-header"><div><div className="eyebrow">NEW DATA SET</div><h2 id="create-dataset-title">Create draft metadata</h2><p>This creates a versioned draft. Generation and validation happen in later workflow phases.</p></div><button className="icon-button" aria-label="Close" onClick={onClose}><X size={18}/></button></header><form onSubmit={submit}><div className="dataset-form-grid"><label className="form-field">Data set name<input required minLength={2} maxLength={180} value={name} onChange={event=>setName(event.target.value)} placeholder="e.g. Customer test data"/></label><label className="form-field">Creation pathway<select value={pathway} onChange={event=>setPathway(event.target.value as typeof pathway)}><option value="source_to_data">Source to Data</option><option value="scenario_to_data">Scenario to Data</option></select></label><label className="form-field">Generation mode<input readOnly value={pathway==='source_to_data'?'Source-backed':'Choose in Scenario to Data'}/><small>{pathway==='source_to_data'?'The selected pathway uses approved source data.':'The mode is chosen in Step 1 of the Scenario to Data workflow.'}</small></label><label className="form-field dataset-span-two">Target use<input required maxLength={80} value={targetUse} onChange={event=>setTargetUse(event.target.value)} placeholder="e.g. Regression testing"/></label><label className="form-field">Record Count<input required type="number" inputMode="numeric" step={1} min={1} max={10000} value={maximumRecords} onChange={event=>setMaximumRecords(Number(event.target.value))}/></label><label className="form-field">Output format<select value={outputFormat} onChange={event=>setOutputFormat(event.target.value as 'json'|'csv')}><option value="json">JSON</option><option value="csv">CSV</option></select></label><label className="form-field dataset-span-two">Expiry (optional)<input type="datetime-local" value={expiry} onChange={event=>setExpiry(event.target.value)}/></label></div>{error&&<div className="alert alert-warning">{error}</div>}<footer className="dataset-modal-actions"><button className="button button-confirm" disabled={busy}><FilePlus2 size={15}/>{busy?'Saving…':'Create draft'}</button><button type="button" className="button button-secondary" onClick={onClose}>Cancel</button></footer></form></section></div>
}

function EditPublishedVersion({role,dataset,version,onClose,onCreated}:{role:HumanRole;dataset:DataSetSummary;version:DataSetVersion;onClose:()=>void;onCreated:(versionNumber:number)=>Promise<void>}){
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
  const willRegenerate=quarantinedBase||sourceBacked||(scenarioBacked&&maximumRecords!==(version.record_count??0))
  const outputNamesChanged=outputNames.some((field,index)=>field.trim()!==(version.selected_fields[index]||version.schema_scope[index].field))
  const duplicateName=outputNames.some((field,index)=>outputNames.indexOf(field)===index&&outputNames.lastIndexOf(field)!==index)
  async function submit(event:FormEvent){
    event.preventDefault();setBusy(true);setError('')
    if(duplicateName){setError('Output field names must be unique.');setBusy(false);return}
    if(willRegenerate&&outputNamesChanged){setError('Keep output field names unchanged when regenerating this version.');setBusy(false);return}
    try{
      const created=await apiRequest<DataSetVersion>(`/datasets/${dataset.id}/versions?base_version_number=${version.version_number}`,role,{
        method:'POST',body:JSON.stringify({
          name:name.trim(),description:targetUse.trim()||null,creation_pathway:dataset.creation_pathway,
          maximum_records:maximumRecords,output_format:outputFormat,schema_scope:version.schema_scope,
          selected_fields:outputNames.map(field=>field.trim()),provenance:version.provenance,
          expiry_at:expiry?new Date(expiry).toISOString():null,permitted_use:[targetUse.trim()||'testing'],
        }),
      })
      await onCreated(created.version_number)
    }catch(cause){setError(cause instanceof Error?cause.message:'Unable to create a new data set version')}
    finally{setBusy(false)}
  }
  const fieldRows=version.schema_scope.map((field,index)=>({field,output:outputNames[index]||'',index}))
  return <div className="dataset-overlay dataset-edit-overlay" role="presentation" onMouseDown={event=>{if(event.target===event.currentTarget)onClose()}}><section className="dataset-modal dataset-create-modal dataset-edit-modal" role="dialog" aria-modal="true" aria-labelledby="edit-version-title"><header className="dataset-modal-header"><div><div className="eyebrow">{quarantinedBase?'QUARANTINED':'PUBLISHED'} VERSION · v{version.version_number} · {generationModeLabel(version.provenance,dataset.creation_pathway)}</div><h2 id="edit-version-title">Edit as a new version</h2><p>Earlier versions and their lineage remain unchanged.</p></div><button className="icon-button" aria-label="Close" onClick={onClose}><X size={18}/></button></header><form onSubmit={event=>void submit(event)}><div className="dataset-form-grid"><label className="form-field dataset-span-two">Data set name<input required minLength={2} maxLength={180} value={name} onChange={event=>setName(event.target.value)}/></label><label className="form-field dataset-span-two">Target use<input required maxLength={80} value={targetUse} onChange={event=>setTargetUse(event.target.value)}/></label><label className="form-field">Record Count<input required type="number" inputMode="numeric" step={1} min={scenarioBacked||quarantinedBase?1:(version.record_count||1)} max={10000} value={maximumRecords} onChange={event=>setMaximumRecords(Number(event.target.value))}/></label><label className="form-field">Output format<select value={outputFormat} onChange={event=>setOutputFormat(event.target.value as 'json'|'csv')}><option value="json">JSON</option><option value="csv">CSV</option></select></label><label className="form-field dataset-span-two">Expiry (optional)<input type="datetime-local" value={expiry} onChange={event=>setExpiry(event.target.value)}/></label></div><div className="dataset-edit-copy-note">{willRegenerate?scenarioBacked?`The linked scenario will run again to generate ${maximumRecords} records.`:`The saved source mapping will run again to generate up to ${maximumRecords} records.`:`This edit will carry forward ${version.record_count?.toLocaleString()??0} existing records. For non-scenario data sets, the Record Count is a limit and does not create additional rows.`}</div><section className="dataset-edit-fields"><div className="dataset-schema-heading"><div><h3>Output fields</h3><p>{willRegenerate?'Source fields, output names, and types stay fixed for regeneration.':'Rename output fields. Source fields and types stay fixed when carrying records forward.'}</p></div><span>{fieldRows.length} fields</span></div><div className="table-shell"><table><thead><tr><th>Source field</th><th>Output field</th><th>Type</th></tr></thead><tbody>{fieldRows.map(({field,output,index})=><tr key={`${field.entity||''}.${field.field}.${index}`}><td><strong>{field.entity?`${field.entity}.`:''}{field.field}</strong></td><td><input aria-label={`Output field for ${field.field}`} required maxLength={180} disabled={busy||willRegenerate} value={output} onChange={event=>setOutputNames(current=>current.map((name,row)=>row===index?event.target.value: name))}/></td><td><code>{field.data_type}</code></td></tr>)}</tbody></table></div></section>{error&&<div className="alert alert-warning">{error}</div>}<footer className="dataset-modal-actions"><button className="button button-confirm" disabled={busy||duplicateName}><FilePlus2 size={15}/>{busy?'Creating version…':willRegenerate?'Generate new version':'Create new draft version'}</button><button type="button" className="button button-secondary" onClick={onClose} disabled={busy}>Cancel</button></footer></form></section></div>
}

function toLocalDateTime(value:string){const date=new Date(value);date.setMinutes(date.getMinutes()-date.getTimezoneOffset());return date.toISOString().slice(0,16)}

function DatasetModal({role,selection,detail,error,onClose,onTab,onVersion,onPublish,onDownload,onEditVersion,onReviewPrivacy,onResume}:{role:HumanRole;selection:{id:string;version?:number;tab:Tab};detail:DatasetDetail|null;error:string;onClose:()=>void;onTab:(tab:Tab)=>void;onVersion:(version:number)=>void;onPublish:()=>void;onDownload:()=>void;onEditVersion:(version:DataSetVersion)=>void;onReviewPrivacy:()=>void;onResume:()=>void}){
  const tabs:Array<{id:Tab;label:string;icon:typeof Database}>=[{id:'overview',label:'Overview',icon:Database},{id:'lineage',label:'Lineage',icon:GitBranch},{id:'validation',label:'Validation',icon:ShieldCheck},{id:'access',label:'Access',icon:LockKeyhole}]
  const current=detail?.selected_version
  return <div className="dataset-overlay" role="presentation" onMouseDown={event=>{if(event.target===event.currentTarget)onClose()}}><section className="dataset-modal dataset-detail-modal" role="dialog" aria-modal="true" aria-label="Data set details"><header className="dataset-modal-header"><div><div className="eyebrow">{creationPathwayLabel(detail?.data_set.creation_pathway).toUpperCase()} · {generationModeLabel(current?.provenance,detail?.data_set.creation_pathway).toUpperCase()}</div><h2>{detail?.data_set.name||'Data set details'}</h2><p>{detail?.data_set.description||'Versioned data set metadata and governance evidence.'}</p></div><div className="dataset-modal-header-actions"><button type="button" className="button button-secondary dataset-artifact-download" disabled={!current||!downloadEligible(current)} title={current?(downloadUnavailableReason(current)??'Download this version as '+artifactFormat(current).toUpperCase()):'Select a data set version to download.'} onClick={onDownload}><Download size={15}/>Download {current?artifactFormat(current).toUpperCase():'file'}</button><button className="icon-button" aria-label="Close data set details" onClick={onClose}><X size={18}/></button></div></header>
    {detail&&<div className="dataset-version-picker"><span className={`status-badge dataset-version-status lifecycle-${current?.lifecycle_state}`}><span>v{current?.version_number}</span><span className="dataset-version-separator" aria-hidden="true">·</span><span>{current?stateLabel[current.lifecycle_state]:''}</span></span><span className={`dataset-mode-value ${generationModeClass(current?.provenance,detail?.data_set.creation_pathway)}`}>{generationModeLabel(current?.provenance,detail?.data_set.creation_pathway)}</span>{detail.versions.length>1&&<select aria-label="Selected data set version" value={current?.version_number??selection.version??''} onChange={event=>onVersion(Number(event.target.value))}>{detail.versions.map(version=><option key={version.id} value={version.version_number}>v{version.version_number} · {stateLabel[version.lifecycle_state]} · {generationModeLabel(version.provenance,detail.data_set.creation_pathway)}</option>)}</select>}</div>}
    <nav className="dataset-tabs" aria-label="Data set detail sections">{tabs.map(({id,label,icon:Icon})=><button key={id} className={selection.tab===id?'active':''} onClick={()=>onTab(id)}><Icon size={15}/>{label}</button>)}</nav>
    <div className="dataset-detail-content">{error&&<div className="alert alert-warning">{error}</div>}{!detail&&!error&&<div className="dataset-loading">Loading versioned metadata…</div>}{detail&&current&&selection.tab==='overview'&&<OverviewTab role={role} dataset={detail.data_set} version={current} sampleRecords={detail.sample_records} blockedRecordCount={detail.blocked_record_count} maskedFields={detail.masked_fields} testDataRequirements={detail.test_data_requirements}/>}{detail&&current&&selection.tab==='lineage'&&<LineageTab events={detail.lineage} versions={detail.versions} version={current}/>}{detail&&current&&selection.tab==='validation'&&<ValidationTab version={current} runs={detail.validation_runs} history={detail.status_history}/>}{detail&&current&&selection.tab==='access'&&<AccessTab access={detail.access} version={current}/>}</div>
    <footer className="dataset-modal-footer"><span className="dataset-next-action"><strong>Next Action:</strong> {current?nextActionText(current):'Loading data set status…'}</span><div className="inline-actions">{role==='Test Data Engineer'&&current?.lifecycle_state==='draft'&&current.version_number===detail?.data_set.latest_version.version_number&&<button className="button button-confirm" onClick={onResume}><ArrowRight size={15}/>Resume</button>}{role==='Test Data Engineer'&&current?.lifecycle_state==='draft'&&current.validation_state==='passed'&&['requires_masking','not_assessed'].includes(current.privacy_state)&&<button className="button button-primary" onClick={onReviewPrivacy}><ShieldCheck size={15}/>Open PII &amp; Masking</button>}{current&&(role==='Test Data Engineer'&&current.lifecycle_state==='published'||role==='Test Data Engineer'&&current.lifecycle_state==='quarantined'||role==='Admin'&&current.lifecycle_state==='quarantined')&&<button className="button button-secondary" onClick={()=>onEditVersion(current)}><FilePlus2 size={15}/>{current.lifecycle_state==='quarantined'?'Create replacement version':'Edit as new version'}</button>}{role==='Test Data Engineer'&&current?.lifecycle_state==='ready'&&recordPrivacyComplete(current)&&<button className="button button-confirm" onClick={onPublish}><Check size={15}/>Publish version</button>}<button className="button button-secondary" onClick={onClose}>Close</button></div></footer>
  </section></div>
}

function OverviewTab({role,dataset,version,sampleRecords,blockedRecordCount,maskedFields,testDataRequirements}:{role:HumanRole;dataset:DataSetSummary;version:DataSetVersion;sampleRecords:Array<Record<string,string|number|null>>;blockedRecordCount:number;maskedFields:string[];testDataRequirements:string|null}){
  const fields=version.fields?.length?version.fields:version.schema_scope.map((field,ordinal)=>({id:`legacy-${ordinal}`,field_name:field.field,field_path:field.json_path??(field.entity?`${field.entity}.${field.field}`:field.field),data_type:field.data_type,required:field.required,target_field_path:null,constraint_spec:{},ordinal}))
  const groupedFields=fields.reduce<Record<string,typeof fields>>((groups,field)=>{
    const scopeField=version.schema_scope.find(item=>item.field===field.field_name||item.json_path===field.field_path)
    const pathGroup=field.field_path.includes('.')&&!field.field_path.startsWith('$')?field.field_path.split('.')[0]:null
    const groupName=scopeField?.entity||pathGroup||'Output fields'
    groups[groupName]=[...(groups[groupName]??[]),field]
    return groups
  },{})
  const previewFields=version.selected_fields?.length?version.selected_fields:fields.map(field=>field.field_name)
  const sourcePreviewReady=version.provenance.origin!=='source_backed'||(
    version.privacy_state==='compliant'&&version.validation_state==='passed'&&Boolean(version.validation_evidence.masking_run_id)
  )
  return <div className="dataset-overview">
    <div className="dataset-overview-grid">
      <Info label="Owner" value={dataset.owner_id}/>
      <Info label="Data set ID" value={dataset.reference_code}/>
      <Info label="Created" value={new Date(dataset.created_at).toLocaleString()}/>
      <Info label="Creation pathway" value={creationPathwayLabel(dataset.creation_pathway)}/>
      <Info label="Generation mode" value={generationModeLabel(version.provenance,dataset.creation_pathway)}/>
      <Info label="Record Count" value={version.record_count?.toLocaleString()??'Not recorded'}/>
      {blockedRecordCount>0&&<Info label="Temporarily unavailable records" value={`${blockedRecordCount.toLocaleString()} · refresh after the active lock expires or is released`}/>}
      <Info label="Privacy state" value={humanize(version.privacy_state)}/>
      <Info label="Publication state" value={humanize(version.publication_state)}/>
      <Info label="Expires" value={version.expiry_at?new Date(version.expiry_at).toLocaleString():'No expiry set'}/>
      <Info label="Permitted use" value={version.permitted_use.join(', ')||'Not specified'}/>
    </div>
    {testDataRequirements&&<details className="dataset-requirements-panel"><summary>Test data requirements</summary><p>{testDataRequirements}</p></details>}
    {sampleRecords.length>0&&sourcePreviewReady&&<section className="dataset-provenance"><h3>Record preview</h3><p>Preview rows come from PostgreSQL records stored for this data-set version after PII masking is complete.</p><div className="table-shell"><table><thead><tr>{previewFields.map(field=><th key={field}><FieldName value={humanize(field)} masked={maskedFields.includes(field)}/></th>)}</tr></thead><tbody>{sampleRecords.map((row,index)=><tr key={index}>{previewFields.map(field=><td key={field}>{String(row[field]??'—')}</td>)}</tr>)}</tbody></table></div></section>}
    {version.provenance.origin==='source_backed'&&!sourcePreviewReady&&<section className="dataset-provenance dataset-preview-gated"><h3>Record preview</h3><p>Source-backed record values are available here only after PII masking completes. No source records are stored in TIGR before then.</p></section>}
    <section className="dataset-schema-block"><div className="dataset-schema-heading"><div><h3>Schema and output fields</h3><p>Fields are grouped by source object where available. Sections open by default.</p></div><span>{fields.length} fields</span></div>{fields.length?<div className="dataset-schema-accordions">{Object.entries(groupedFields).map(([groupName,groupFields])=><details className="dataset-schema-accordion" open key={groupName}><summary><strong>{groupName}</strong><span>{groupFields.length} fields</span></summary><div className="dataset-schema-accordion-content"><table><thead><tr><th>Schema field</th><th>Output field</th><th>Type</th><th>Requirement</th></tr></thead><tbody>{groupFields.map(field=>{const outputField=field.target_field_path||field.field_name;return <tr key={field.id}><td><strong>{field.field_path}</strong></td><td><FieldName value={outputField} masked={maskedFields.includes(outputField)}/></td><td><code>{field.data_type}</code></td><td><span className={`dataset-field-requirement ${field.required?'required':'optional'}`}>{field.required?'Required':'Optional'}</span></td></tr>})}</tbody></table></div></details>)}</div>:<div className="dataset-no-fields">No schema fields have been recorded for this draft.</div>}</section>
    {role!=='QA Engineer'&&<details className="dataset-provenance dataset-provenance-accordion"><summary><h3>Provenance references</h3></summary><div className="dataset-provenance-accordion-content">{Object.entries(version.provenance).length?<div className="dataset-meta-list">{Object.entries(version.provenance).map(([key,value])=><Info key={key} label={humanize(key)} value={value}/>)}</div>:<p>No source, scenario, job, or artifact references have been attached yet.</p>}</div></details>}
  </div>
}
function FieldName({value,masked}:{value:string;masked:boolean}){return <span className="dataset-field-name-label">{masked&&<span className="dataset-field-privacy-indicator" role="img" aria-label="PII masking applied" title="PII masking applied"><ShieldCheck className="dataset-field-privacy-icon" size={14}/></span>}{value}</span>}
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

function ValidationTab({version,runs,history}:{version:DataSetVersion;runs:DatasetDetail['validation_runs'];history:DatasetDetail['status_history']}){return <div className="dataset-validation"><div className="validation-state-grid"><StateTile icon={ShieldCheck} label="Privacy" value={humanize(version.privacy_state)} tone={version.privacy_state==='compliant'?'good':version.privacy_state==='blocked'?'bad':'neutral'}/><StateTile icon={Check} label="Validation" value={humanize(version.validation_state)} tone={version.validation_state==='passed'?'good':version.validation_state==='failed'?'bad':'neutral'}/><StateTile icon={Activity} label="Readiness" value={stateLabel[version.lifecycle_state]} tone={version.lifecycle_state==='ready'||version.lifecycle_state==='published'?'good':version.lifecycle_state==='failed'||version.lifecycle_state==='quarantined'?'bad':'neutral'}/></div><div className="validation-evidence-card"><div><h3>Validation runs</h3><p>Structured checks and safe summaries recorded for this exact version.</p></div>{runs.length?<div className="dataset-meta-list">{runs.map(run=><article className="dataset-info" key={run.id}><span>{humanize(run.validation_type)} · {humanize(run.status)} · {new Date(run.started_at).toLocaleString()}</span><strong>{String(run.summary.summary??'Validation completed')}</strong>{run.results.map((result,index)=><small key={`${result.check_code}-${index}`}>{humanize(result.check_code)} · {humanize(result.status)}{result.field_path?` · ${result.field_path}`:''} · {result.safe_message}</small>)}</article>)}</div>:<div className="dataset-no-fields">No structured validation run has been recorded.</div>}</div><div className="validation-evidence-card"><div><h3>Version status history</h3><p>Append-only lifecycle changes for this version.</p></div>{history.length?<div className="dataset-meta-list">{history.map((row,index)=><div className="dataset-info" key={`${row.changed_at}-${index}`}><span>{row.from_status?`${humanize(row.from_status)} → `:''}{humanize(row.to_status)} · {new Date(row.changed_at).toLocaleString()}</span><strong>{row.reason||'Status recorded'}</strong><small>Changed by {row.changed_by}</small></div>)}</div>:<div className="dataset-no-fields">No status transitions have been recorded.</div>}</div>{Object.keys(version.validation_evidence).length>0&&<div className="validation-evidence-card"><div><h3>Additional safe evidence</h3><p>Operational metadata supplied by the generation or validation workflow.</p></div><dl>{Object.entries(version.validation_evidence).map(([key,value])=><div key={key}><dt>{humanize(key)}</dt><dd>{String(value??'—')}</dd></div>)}</dl></div>}{version.lifecycle_state==='ready'&&<div className="validation-ready-note"><Check size={16}/> All publication gates are satisfied. A Test Data Engineer can publish this version.</div>}</div>}

function AccessTab({access,version}:{access:DatasetDetail['access'];version:DataSetVersion}){return <div className="dataset-access"><div className="access-context-card"><span><LockKeyhole size={18}/></span><div><strong>Workspace-scoped access</strong><p>Data set visibility follows workspace membership and publication state. Downloads remain subject to an approved reservation and active lock.</p></div></div><div className="dataset-overview-grid"><Info label="Owner" value={access.owner_id}/><Info label="Workspace" value={access.workspace_id}/><Info label="Permitted use" value={version.permitted_use.join(', ')||'Not specified'}/><Info label="Expiry" value={version.expiry_at?new Date(version.expiry_at).toLocaleString():'No expiry set'}/></div><div className="access-privacy-note"><ShieldCheck size={16}/> QA Engineers can view published versions in their workspace. Data use still requires reservation approval.</div></div>}

function StateTile({icon:Icon,label,value,tone}:{icon:typeof Check;label:string;value:string;tone:string}){return <div className={`state-tile state-${tone}`}><span><Icon size={17}/></span><small>{label}</small><strong>{value}</strong></div>}
function Info({label,value}:{label:string;value:unknown}){return <div className="dataset-info"><span>{label}</span><strong>{metadataDisplay(value)}</strong></div>}
function metadataDisplay(value:unknown):string{
  if(value===null||value===undefined||value==='')return '—'
  if(typeof value==='string'||typeof value==='number'||typeof value==='boolean')return String(value)
  if(Array.isArray(value))return value.map(metadataDisplay).join(' · ')
  if(typeof value==='object')return Object.entries(value as Record<string,unknown>).map(([key,item])=>`${humanize(key)}: ${metadataDisplay(item)}`).join(' · ')
  return String(value)
}
function humanize(value:string){return String(value??'').replaceAll('_',' ').replaceAll('.', ' · ').replace(/\b\w/g,char=>char.toUpperCase())}
