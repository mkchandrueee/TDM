import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Check, ChevronDown, CircleAlert, Database, FileJson2, FileSpreadsheet, Network, Pencil, Play, Plus, Power, RefreshCw, Save, Trash2, X } from 'lucide-react'
import { apiRequest, type HumanRole } from './api'
import { ConfirmDialog } from './components/ui'

type ConnectorType = 'postgresql' | 'json' | 'csv' | 'delimited_text'
type ConnectionDirection = 'source' | 'target' | 'both'
type OperationScope = 'read_only' | 'write_only' | 'read_write'
type PreviewField = {name: string; type: string; fields?: PreviewField[]}
type StructureKey = {name: string; fields: string[]; evidence: string; validated: boolean | null; enforced: boolean | null}
type StructureEdge = {id: string; name: string | null; source_object: string; source_fields: string[]; target_object: string; target_fields: string[]; evidence: string; validated: boolean | null; enforced: boolean | null}
type StructureObject = {name: string; type: string; fields: PreviewField[]; primary_key?: StructureKey | null; unique_constraints?: StructureKey[]; unique_indexes?: StructureKey[]; foreign_key_edges?: StructureEdge[]}
type ConnectionStructure = {format: string; fields?: PreviewField[]; objects?: StructureObject[]; structure_snapshot_version?: number; [key: string]: unknown}
type Connection = {
  id: string; workspace_id: string; name: string; connector_type: ConnectorType; direction: ConnectionDirection
  environment: string; purpose: string | null; owner: string; operation_scope: OperationScope
  secret_configured: boolean; connector_config: Record<string, unknown>; status: 'draft' | 'tested' | 'enabled' | 'disabled'
  tested_at: string | null; tested_by: string | null; test_result: {succeeded: boolean; message: string} | null
  structure_preview: ConnectionStructure | null; created_at: string
}
type ConnectionTestRun = {id:string;requested_by:string;started_at:string;finished_at:string;outcome:'succeeded'|'failed';safe_error_code:string|null;safe_message:string;correlation_id:string}

type ConnectionForm = {name: string; connector_type: ConnectorType; environment: string; purpose: string; owner: string; secret_reference: string; file_path: string; delimiter: string; has_header: boolean; encoding: string; schema: string}
const defaults: ConnectionForm = { name: '', connector_type: 'csv', environment: 'Development', purpose: '', owner: '', secret_reference: '', file_path: 'customers.csv', delimiter: ',', has_header: true, encoding: 'utf-8-sig', schema: 'public' }

export default function ConnectionsPage({role, workspaceId, ownerName}:{role:HumanRole;workspaceId:string;ownerName:string}) {
  const [items,setItems]=useState<Connection[]>([])
  const [form,setForm]=useState({...defaults})
  const [editing,setEditing]=useState<string|null>(null)
  const [showForm,setShowForm]=useState(false)
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const [notice,setNotice]=useState('')
  const [selected,setSelected]=useState<string|null>(null)
  const [deleting,setDeleting]=useState<Connection|null>(null)
  const [testHistory,setTestHistory]=useState<ConnectionTestRun[]>([])
  const editingConnection=items.find(item=>item.id===editing)

  const load=useCallback(async()=>{
    if(!workspaceId){setItems([]);return}
    try{const connections=await apiRequest<Connection[]>(`/workspaces/${workspaceId}/connections?include_disabled=true`,role);setItems(connections.sort((a,b)=>Date.parse(b.created_at)-Date.parse(a.created_at)));setError('')}
    catch(cause){setError(cause instanceof Error?cause.message:'Unable to load workspace connections')}
  },[workspaceId,role])
  useEffect(()=>{void load()},[load])
  useEffect(()=>{
    if(!selected||!workspaceId){setTestHistory([]);return}
    let active=true
    apiRequest<ConnectionTestRun[]>(`/workspaces/${workspaceId}/connections/${selected}/tests`,role)
      .then(rows=>{if(active)setTestHistory(rows)})
      .catch(()=>{if(active)setTestHistory([])})
    return()=>{active=false}
  },[selected,workspaceId,role])

  function resetForm(){setForm({...defaults,owner:ownerName});setEditing(null);setShowForm(false)}
  useEffect(()=>{if(!editing)setForm(current=>({...current,owner:ownerName}))},[ownerName,editing])
  function edit(item:Connection){
    const config=item.connector_config
    setForm({...defaults,name:item.name,connector_type:item.connector_type,environment:item.environment,purpose:item.purpose??'',owner:item.owner,
      secret_reference:'',file_path:String(config.file_path??''),delimiter:String(config.delimiter??(item.connector_type==='delimited_text'?'\t':',')),has_header:Boolean(config.has_header??true),encoding:String(config.encoding??'utf-8-sig'),schema:String(config.schema??'public')})
    setEditing(item.id);setShowForm(true)
  }
  function connectorConfig(){
    if(form.connector_type==='postgresql')return {schema:form.schema}
    if(form.connector_type==='json')return {file_path:form.file_path,encoding:form.encoding}
    return {file_path:form.file_path,delimiter:form.delimiter,has_header:form.has_header,encoding:form.encoding}
  }
  async function save(event:FormEvent){
    event.preventDefault();setBusy(true);setError('');setNotice('')
    const intent=(event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement|null
    const shouldTest=intent?.value==='test'
    const payload={name:form.name,connector_type:form.connector_type,direction:'source',environment:form.environment,purpose:form.purpose||null,owner:form.owner||ownerName,operation_scope:'read_only',secret_reference:form.connector_type==='postgresql'?form.secret_reference||undefined:(editing?null:undefined),connector_config:connectorConfig()}
    let testOutcome:{succeeded:boolean;message:string}|null=null
    try{
      const saved=editing
        ? await apiRequest<Connection>(`/workspaces/${workspaceId}/connections/${editing}`,role,{method:'PATCH',body:JSON.stringify(payload)})
        : await apiRequest<Connection>(`/workspaces/${workspaceId}/connections`,role,{method:'POST',body:JSON.stringify(payload)})
      setSelected(saved.id)
      if(shouldTest){
        const tested=await apiRequest<Connection>(`/workspaces/${workspaceId}/connections/${saved.id}/test`,role,{method:'POST'})
        testOutcome={succeeded:Boolean(tested.test_result?.succeeded),message:tested.test_result?.message??(tested.test_result?.succeeded?'Connection test succeeded.':'Connection test failed.')}
      }else setNotice(editing?'Connection updated. Test the connection again before enabling it.':'Draft saved. Test the connection to verify access to the source.')
      resetForm();await load()
      if(testOutcome?.succeeded)setNotice(testOutcome.message)
      else if(testOutcome)setError(testOutcome.message)
    }catch(cause){setError(cause instanceof Error?cause.message:'Unable to save connection')}
    finally{setBusy(false)}
  }
  async function transition(item:Connection,action:'test'|'enable'|'disable'){
    setBusy(true);setError('');setNotice('')
    try{
      const result=await apiRequest<Connection>(`/workspaces/${workspaceId}/connections/${item.id}/${action}`,role,{method:'POST'})
      setSelected(item.id);await load()
      if(action==='test'){
        const message=result.test_result?.message??(result.test_result?.succeeded?'Connection test succeeded.':'Connection test failed.')
        if(result.test_result?.succeeded)setNotice(message)
        else setError(message)
      }
    }
    catch(cause){setError(cause instanceof Error?cause.message:'Connection action failed')}
    finally{setBusy(false)}
  }
  async function removeConnection(item:Connection){
    setBusy(true);setError('');setNotice('')
    try{
      await apiRequest<void>(`/workspaces/${workspaceId}/connections/${item.id}`,role,{method:'DELETE'})
      setDeleting(null)
      if(selected===item.id)setSelected(null)
      setNotice(`Connection “${item.name}” was deleted.`)
      await load()
    }catch(cause){setDeleting(null);setError(cause instanceof Error?cause.message:'Unable to delete connection')}
    finally{setBusy(false)}
  }
  if(!workspaceId)return <section className="panel connection-empty"><Network size={22}/><h2>Select a workspace</h2><p>Connections are kept inside the active workspace. Choose a workspace in the top bar to continue.</p></section>
  return <div className="connections-page">
    {error&&<div className="alert alert-warning" role="alert"><CircleAlert size={17}/>{error}</div>}
    {notice&&<div className="alert alert-info" role="status"><Check size={17}/>{notice}</div>}
    <section className="panel connection-intro"><div><div className="eyebrow">ESTABLISH CONNECTION</div><h2>Source connections</h2><p>Configure a source, test its connection, review the discovered structure, then enable it for future workflows.</p></div><button className="button button-confirm" onClick={()=>{resetForm();setShowForm(true)}}><Plus size={16}/>Add connection</button></section>
    {showForm&&<div className="connection-modal-overlay" onClick={event=>{if(event.target===event.currentTarget)resetForm()}}><section className="panel connection-editor" role="dialog" aria-modal="true" aria-labelledby="connection-modal-title"><div className="panel-heading"><div><h2 id="connection-modal-title">{editing?'Edit connection':'Add a connection'}</h2><p>{form.connector_type==='postgresql'?'Provide a secret reference for the PostgreSQL connection.':'Set the source file path and format details.'}</p></div><button type="button" className="icon-button" aria-label="Close connection form" onClick={resetForm}><X size={18}/></button></div>
      <form className="connection-form" onSubmit={save}>
        <div className="connection-form-grid">
          <label className="form-field">Connection name<input required minLength={2} maxLength={180} value={form.name} onChange={e=>setForm({...form,name:e.target.value})} placeholder="e.g. Customer source"/></label>
          <label className="form-field">Connector type<select value={form.connector_type} onChange={e=>{const connector_type=e.target.value as ConnectorType;setForm({...form,connector_type,delimiter:connector_type==='delimited_text'?'\t':connector_type==='csv'?',':form.delimiter})}}><option value="postgresql">PostgreSQL</option><option value="json">JSON file</option><option value="csv">CSV file</option><option value="delimited_text">Delimited text</option></select></label>
          <label className="form-field">Environment<input required maxLength={40} value={form.environment} onChange={e=>setForm({...form,environment:e.target.value})} placeholder="e.g. Development"/></label>
          <label className="form-field">Owner<input required maxLength={180} value={form.owner||ownerName} readOnly aria-readonly="true"/><small>Set to your username.</small></label>
          {form.connector_type==='postgresql'?<>
            <label className="form-field span-two">Database credentials reference<input required={!editing||!editingConnection?.secret_configured||editingConnection?.connector_type!=='postgresql'} maxLength={500} value={form.secret_reference} onChange={e=>setForm({...form,secret_reference:e.target.value})} placeholder={editingConnection?.connector_type==='postgresql'&&editingConnection.secret_configured?'Stored reference is hidden; enter a replacement only if needed':'e.g. env://TIGR_SECRET_CUSTOMER_DB'}/><small>Required for PostgreSQL. The reference points to a secret containing the connection URL; credentials remain in the configured secret store.</small></label>
            <label className="form-field">Schema<input maxLength={63} value={form.schema} onChange={e=>setForm({...form,schema:e.target.value})}/><small>Schema to inspect when testing the connection.</small></label>
          </>:<>
            <label className="form-field span-two">File path<input required maxLength={500} value={form.file_path} onChange={e=>setForm({...form,file_path:e.target.value})} placeholder="e.g. customers.csv"/><small>Path is relative to the configured file root; absolute paths and parent traversal are rejected.</small></label>
            {(form.connector_type==='csv'||form.connector_type==='delimited_text')&&<>
              <label className="form-field">Delimiter<input maxLength={1} required value={form.delimiter} onChange={e=>setForm({...form,delimiter:e.target.value})}/></label>
              <label className="form-field"><span>Header row</span><span className="check-field"><input type="checkbox" checked={form.has_header} onChange={e=>setForm({...form,has_header:e.target.checked})}/> First row contains column names</span></label>
            </>}
            <label className="form-field">Encoding<select value={form.encoding} onChange={e=>setForm({...form,encoding:e.target.value})}><option>utf-8-sig</option><option>utf-8</option><option>latin-1</option></select></label>
          </>}
          <label className="form-field span-two">Purpose<textarea maxLength={2000} rows={2} value={form.purpose} onChange={e=>setForm({...form,purpose:e.target.value})} placeholder="What this connection is used for"/></label>
        </div>
        <div className="connection-form-actions"><button type="submit" value="save" className="button button-confirm" disabled={busy}><Save size={15}/>{editing?'Save changes':'Save draft'}</button><button type="submit" value="test" className="button button-secondary" disabled={busy}><Play size={15}/>{busy?'Testing connection':'Test connection'}</button><button type="button" className="button button-secondary" onClick={resetForm}>Cancel</button></div>
      </form>
    </section></div>}

    <section className="panel connection-inventory">
      <div className="connection-inventory-heading"><div><span className="eyebrow">SAVED SOURCES</span><h2>Connection inventory</h2><p>Test each source before enabling it for data workflows.</p></div><div className="connection-inventory-tools"><span className="connection-count">{items.length} connection{items.length===1?'':'s'}</span><button className="icon-button" aria-label="Refresh connections" onClick={()=>void load()}><RefreshCw size={17}/></button></div></div>
      {!items.length?<div className="connection-empty"><span className="connection-empty-icon"><Network size={22}/></span><strong>No source connections yet</strong><p>Add a source, then test it to verify access and inspect its structure. File previews show column names without exposing cell values.</p><button className="button button-secondary" onClick={()=>{resetForm();setShowForm(true)}}><Plus size={15}/>Add connection</button></div>:<div className="connection-list">{items.map(item=><article key={item.id} className={`connection-card ${selected===item.id?'is-selected':''}`}>
        <div className="connection-card-main"><span className={`connection-type-icon connector-${item.connector_type}`}>{item.connector_type==='postgresql'?<Database size={20}/>:item.connector_type==='json'?<FileJson2 size={20}/>:<FileSpreadsheet size={20}/>}</span><div className="connection-card-title"><div><h3>{item.name}</h3><span className={`status-badge ${item.status==='enabled'?'status-success':item.status==='tested'?'status-info':item.status==='disabled'?'status-neutral':'status-warning'}`}><i/>{item.status}</span></div><p>{item.connector_type.toUpperCase().replace('_',' ')}<span>·</span>{item.environment}</p><small>{item.purpose||`Owner: ${item.owner}`}</small></div></div>
        <div className="connection-card-footer"><div className={`connection-test-summary ${item.test_result?.succeeded?'is-passed':item.test_result?'is-failed':'is-untested'}`}>{item.test_result?.succeeded?<Check size={15}/>:item.test_result?<CircleAlert size={15}/>:<Network size={15}/>}<span>{item.test_result?.message??'Not tested yet'}</span><small>{item.tested_at?new Date(item.tested_at).toLocaleString():'Run a test to verify source access'}</small></div><div className="connection-actions"><button className="button button-secondary compact" disabled={busy} onClick={()=>edit(item)}><Pencil size={14}/>Edit</button><button className="button button-secondary compact" disabled={busy} onClick={()=>transition(item,'test')}><Play size={14}/>{busy&&selected===item.id?'Testing…':'Test connection'}</button>{item.status==='enabled'?<button className="button button-secondary compact" disabled={busy} onClick={()=>transition(item,'disable')}><Power size={14}/>Disable</button>:<button className="button button-confirm compact" disabled={busy||!item.test_result?.succeeded} onClick={()=>transition(item,'enable')}><Check size={14}/>Enable</button>}<button className="connection-details-toggle" onClick={()=>setSelected(selected===item.id?null:item.id)} aria-expanded={selected===item.id}>{selected===item.id?'Hide details':'Details'}</button><button className="connection-delete-button" disabled={busy} onClick={()=>setDeleting(item)}><Trash2 size={14}/>Delete</button></div></div>
        {selected===item.id&&<div className="connection-details"><div className={`test-result ${item.test_result?.succeeded?'test-success':item.test_result?'test-failed':'test-pending'}`}>{item.test_result?(item.test_result.succeeded?<Check size={16}/>:<CircleAlert size={16}/>):<CircleAlert size={16}/>}<span>{item.test_result?.message??'Run a connection test to discover structure and confirm access.'}</span>{item.tested_by&&<small> · by {item.tested_by}</small>}</div>{item.structure_preview&&<StructureView structure={item.structure_preview}/>}{item.connector_type==='postgresql'&&<LockConfigurationEditor workspaceId={workspaceId} connectionId={item.id} role={role}/>}<section className="connection-test-history"><div><h3>Connection test history</h3><p>Recent safe outcomes retained in PostgreSQL.</p></div>{testHistory.length?<div className="dataset-meta-list">{testHistory.map(run=><div className="dataset-info" key={run.id}><span>{run.outcome==='succeeded'?'Passed':'Failed'} · {new Date(run.started_at).toLocaleString()}</span><strong>{run.safe_message}</strong><small>Requested by {run.requested_by} · {run.safe_error_code||'No error code'} · {run.correlation_id}</small></div>)}</div>:<p>No test history is recorded.</p>}</section><div className="connection-meta"><span>Secret configured: <b>{item.secret_configured?'Yes':'No'}</b></span><span>Owner: <b>{item.owner}</b></span></div></div>}
      </article>)}</div>}
    </section>
    {deleting&&<ConfirmDialog title="Delete connection?" confirmLabel={busy?'Deleting…':'Delete connection'} destructive onClose={()=>setDeleting(null)} onConfirm={()=>{if(!busy)void removeConnection(deleting)}}><p>Delete <strong>{deleting.name}</strong> and its connection test history? This cannot be undone. Connections referenced by data workflows cannot be deleted.</p></ConfirmDialog>}
  </div>
}

function StructureView({structure}:{structure:ConnectionStructure}){
  const fields=structure.fields??[]
  const objects=structure.objects??[]
  return <div className="structure-preview"><div className="structure-heading"><div><h3>Discovered structure</h3><p>{structure.format} · structure only, no row values{structure.structure_snapshot_version?` · snapshot v${structure.structure_snapshot_version}`:''}</p></div><span>{fields.length+objects.reduce((sum,object)=>sum+object.fields.length,0)} fields</span></div>{objects.length?objects.map(object=><details className="structure-object" key={object.name}><summary><span className="structure-object-name">{object.name}</span><span className="structure-object-meta">{object.type} · {object.fields.length} fields</span><ChevronDown size={16} aria-hidden="true"/></summary><FieldRows fields={object.fields}/>{object.primary_key&&<p className="quiet-message">Primary key: {object.primary_key.fields.join(' + ')}</p>}{!!(object.unique_constraints?.length||object.unique_indexes?.length)&&<p className="quiet-message">Unique keys/indexes: {[...(object.unique_constraints??[]),...(object.unique_indexes??[])].map(key=>key.fields.join(' + ')).join(' · ')}</p>}{!!object.foreign_key_edges?.length&&<div className="dataset-meta-list">{object.foreign_key_edges.map(edge=><div className="dataset-info" key={edge.id}><strong>{edge.source_object}.{edge.source_fields.join(' + ')} → {edge.target_object}.{edge.target_fields.join(' + ')}</strong><small>{edge.evidence.replaceAll('_',' ')} · {edge.validated===false?'not validated':edge.validated===true?'validated':'validation state unavailable'}</small></div>)}</div>}</details>):<FieldRows fields={fields}/>}</div>
}
function FieldRows({fields}:{fields:PreviewField[]}){return <div className="structure-fields">{fields.map(field=><div className="structure-field" key={field.name}><span>{field.name}</span><code>{field.type}</code>{field.fields&&<div className="nested-fields">{field.fields.map(child=><div key={child.name}><span>{child.name}</span><code>{child.type}</code></div>)}</div>}</div>)}{!fields.length&&<p className="quiet-message">No fields were discovered.</p>}</div>}

type LockStep={relationship_id:string;direction:'forward'|'reverse'}
type LockPath={name:string;anchor_object:string;steps:LockStep[];max_depth:number}
type LockAnchor={object_name:string;key_kind:'primary'|'unique';key_name:string;key_fields:string[]}
type ManualEdge={relationship_id:string;source_object:string;source_fields:string[];target_object:string;target_fields:string[];join_semantics:'equals';reason:string}
type LockSchemaState={snapshot:{id:string;version_number:number;structure:ConnectionStructure}|null;configuration:{version_number:number;matches_current_snapshot:boolean;configuration:{anchors:LockAnchor[];paths:LockPath[];manual_relationships:ManualEdge[]}}|null}

function LockConfigurationEditor({workspaceId,connectionId,role}:{workspaceId:string;connectionId:string;role:HumanRole}){
  const [state,setState]=useState<LockSchemaState|null>(null)
  const [anchors,setAnchors]=useState<LockAnchor[]>([])
  const [paths,setPaths]=useState<LockPath[]>([])
  const [manual,setManual]=useState<ManualEdge[]>([])
  const [draft,setDraft]=useState({source_object:'',source_fields:'',target_object:'',target_fields:'',reason:''})
  const [busy,setBusy]=useState(false)
  const [message,setMessage]=useState('')
  const [error,setError]=useState('')
  const canEdit=role==='Test Data Engineer'

  const load=useCallback(async()=>{
    try{
      const result=await apiRequest<LockSchemaState>(`/workspaces/${workspaceId}/connections/${connectionId}/lock-schema`,role)
      setState(result)
      const approved=result.configuration?.configuration
      setAnchors(approved?.anchors??[])
      setPaths(approved?.paths??[])
      setManual(approved?.manual_relationships??[])
      setError('')
    }catch(cause){setError(cause instanceof Error?cause.message:'Unable to load lock relationship metadata')}
  },[workspaceId,connectionId,role])
  useEffect(()=>{void load()},[load])

  const objects=state?.snapshot?.structure.objects??[]
  const needsRefresh=!!state?.snapshot&&objects.some(object=>object.primary_key===undefined||object.foreign_key_edges===undefined)
  const discovered=objects.flatMap(object=>object.foreign_key_edges??[])
  const allEdges=[...discovered,...manual.map(edge=>({...edge,id:edge.relationship_id,name:null,evidence:'manual_tde_approval',source_fields:edge.source_fields,target_fields:edge.target_fields,validated:false,enforced:false}))]
  const keyCandidates=objects.flatMap(object=>[
    ...(object.primary_key?.fields.length?[{object_name:object.name,key_kind:'primary' as const,key_name:object.primary_key.name,key_fields:object.primary_key.fields}]:[]),
    ...[...(object.unique_constraints??[]),...(object.unique_indexes??[])].map(key=>({object_name:object.name,key_kind:'unique' as const,key_name:key.name,key_fields:key.fields})),
  ])
  function addPath(){
    const anchor=anchors[0]?.object_name
    if(!anchor){setError('Select an entity anchor before adding a propagation path.');return}
    setPaths(rows=>[...rows,{name:`Path ${rows.length+1}`,anchor_object:anchor,steps:[],max_depth:1}]);setError('')
  }
  function addManual(){
    const source_fields=draft.source_fields.split(',').map(value=>value.trim()).filter(Boolean)
    const target_fields=draft.target_fields.split(',').map(value=>value.trim()).filter(Boolean)
    if(!draft.source_object||!draft.target_object||!draft.reason.trim()||!source_fields.length||source_fields.length!==target_fields.length){setError('Enter both objects, matching ordered field lists, and a reason for the manual relationship.');return}
    setManual(rows=>[...rows,{relationship_id:`manual:${crypto.randomUUID()}`,source_object:draft.source_object,source_fields,target_object:draft.target_object,target_fields,join_semantics:'equals',reason:draft.reason.trim()}])
    setDraft({source_object:'',source_fields:'',target_object:'',target_fields:'',reason:''});setError('')
  }
  async function save(){
    setBusy(true);setError('');setMessage('')
    try{
      const result=await apiRequest<{version_number:number}>(`/workspaces/${workspaceId}/connections/${connectionId}/lock-configurations`,role,{method:'POST',body:JSON.stringify({anchors,paths,manual_relationships:manual})})
      setMessage(`Approved configuration v${result.version_number}. This approval uses the current structure snapshot.`)
      await load()
    }catch(cause){setError(cause instanceof Error?cause.message:'Unable to save lock configuration')}
    finally{setBusy(false)}
  }
  return <section className="structure-preview"><div className="structure-heading"><div><h3>Entity lock relationships</h3><p>Keys and paths are reviewed by a Test Data Engineer. Source record values are not shown.</p></div>{state?.configuration&&<span>Approved v{state.configuration.version_number}{state.configuration.matches_current_snapshot?'':' · older snapshot'}</span>}</div>
    {error&&<div className="alert alert-warning" role="alert">{error}</div>}{message&&<div className="alert alert-info" role="status">{message}</div>}
    {!state?.snapshot?<p className="quiet-message">Run a successful PostgreSQL connection test to save an immutable structure snapshot.</p>:<>
      <p className="quiet-message">Structure snapshot v{state.snapshot.version_number} · source namespace is scoped to this connection.</p>
      {needsRefresh&&<p className="quiet-message">This snapshot predates grouped key and relationship metadata. Retest the connection to discover ordered composite keys and foreign-key edges before saving an approval.</p>}
      <h4>Eligible entity keys</h4>
      {!keyCandidates.length?<p className="quiet-message">No declared primary or unique keys were discovered. Field names alone are not eligible identity keys.</p>:<div className="dataset-meta-list">{keyCandidates.map(key=>{const checked=anchors.some(item=>item.object_name===key.object_name&&item.key_kind===key.key_kind&&item.key_name===key.key_name&&JSON.stringify(item.key_fields)===JSON.stringify(key.key_fields));return <label className="dataset-info" key={`${key.object_name}:${key.key_kind}:${key.key_name}`}><span><input type="checkbox" checked={checked} disabled={!canEdit||busy} onChange={event=>setAnchors(rows=>event.target.checked?[...rows,key]:rows.filter(item=>!(item.object_name===key.object_name&&item.key_kind===key.key_kind&&item.key_name===key.key_name)))}/> {key.key_kind==='primary'?'Primary key':'Unique key'} · {key.object_name}.{key.key_fields.join(' + ')}</span><small>{key.key_name}{key.key_kind==='unique'?' · unique keys require explicit TDE approval':''}</small></label>})}</div>}
      <div className="connection-inventory-heading"><div><h4>Approved propagation paths</h4><p>Order the relationship edges from a selected anchor; every edge must connect to the previous object.</p></div>{canEdit&&<button type="button" className="button button-secondary compact" disabled={busy} onClick={addPath}><Plus size={14}/>Add path</button>}</div>
      {paths.map((path,pathIndex)=><div className="dataset-info" key={`path-${pathIndex}`}><div className="connection-form-grid"><label className="form-field">Path name<input value={path.name} disabled={!canEdit||busy} onChange={event=>setPaths(rows=>rows.map((item,index)=>index===pathIndex?{...item,name:event.target.value}:item))}/></label><label className="form-field">Anchor object<select value={path.anchor_object} disabled={!canEdit||busy} onChange={event=>setPaths(rows=>rows.map((item,index)=>index===pathIndex?{...item,anchor_object:event.target.value}:item))}>{anchors.map(anchor=><option key={anchor.object_name} value={anchor.object_name}>{anchor.object_name}</option>)}</select></label><label className="form-field">Maximum traversal depth<input type="number" min={1} max={16} value={path.max_depth} disabled={!canEdit||busy} onChange={event=>setPaths(rows=>rows.map((item,index)=>index===pathIndex?{...item,max_depth:Number(event.target.value)}:item))}/></label></div>{path.steps.map((step,stepIndex)=><div className="connection-form-grid" key={`${pathIndex}:${stepIndex}`}><label className="form-field">Relationship<select value={step.relationship_id} disabled={!canEdit||busy} onChange={event=>setPaths(rows=>rows.map((item,index)=>index===pathIndex?{...item,steps:item.steps.map((entry,edgeIndex)=>edgeIndex===stepIndex?{...entry,relationship_id:event.target.value}:entry)}:item))}><option value="">Choose relationship</option>{allEdges.map(edge=><option key={edge.id} value={edge.id}>{edge.source_object}.{edge.source_fields.join('+')} → {edge.target_object}.{edge.target_fields.join('+')}</option>)}</select></label><label className="form-field">Direction<select value={step.direction} disabled={!canEdit||busy} onChange={event=>setPaths(rows=>rows.map((item,index)=>index===pathIndex?{...item,steps:item.steps.map((entry,edgeIndex)=>edgeIndex===stepIndex?{...entry,direction:event.target.value as 'forward'|'reverse'}:entry)}:item))}><option value="forward">Forward</option><option value="reverse">Reverse</option></select></label><button type="button" className="text-button" disabled={!canEdit||busy} onClick={()=>setPaths(rows=>rows.map((item,index)=>index===pathIndex?{...item,steps:item.steps.filter((_,edgeIndex)=>edgeIndex!==stepIndex)}:item))}>Remove edge</button></div>)}<div className="connection-form-actions">{canEdit&&<><button type="button" className="button button-secondary compact" disabled={busy||path.steps.length>=16} onClick={()=>setPaths(rows=>rows.map((item,index)=>index===pathIndex?{...item,steps:[...item.steps,{relationship_id:'',direction:'forward'}],max_depth:item.steps.length+1}:item))}><Plus size={14}/>Add edge</button><button type="button" className="text-button" disabled={busy} onClick={()=>setPaths(rows=>rows.filter((_,index)=>index!==pathIndex))}>Remove path</button></>}</div></div>)}
      <h4>Manual relationships</h4><p className="quiet-message">Use exact ordered object/field pairs and document why this undeclared relationship is trusted.</p>{manual.map(edge=><div className="dataset-info" key={edge.relationship_id}><strong>{edge.source_object}.{edge.source_fields.join(' + ')} → {edge.target_object}.{edge.target_fields.join(' + ')}</strong><small>Manual TDE approval · {edge.reason}</small>{canEdit&&<button type="button" className="text-button" disabled={busy} onClick={()=>setManual(rows=>rows.filter(item=>item.relationship_id!==edge.relationship_id))}>Remove</button>}</div>)}
      {canEdit&&<><div className="connection-form-grid"><label className="form-field">Source object<select value={draft.source_object} onChange={event=>setDraft({...draft,source_object:event.target.value})}><option value="">Select object</option>{objects.map(object=><option key={object.name} value={object.name}>{object.name}</option>)}</select></label><label className="form-field">Ordered source fields<input value={draft.source_fields} onChange={event=>setDraft({...draft,source_fields:event.target.value})} placeholder="field_a, field_b"/></label><label className="form-field">Target object<select value={draft.target_object} onChange={event=>setDraft({...draft,target_object:event.target.value})}><option value="">Select object</option>{objects.map(object=><option key={object.name} value={object.name}>{object.name}</option>)}</select></label><label className="form-field">Ordered target fields<input value={draft.target_fields} onChange={event=>setDraft({...draft,target_fields:event.target.value})} placeholder="field_a, field_b"/></label><label className="form-field span-two">Reason<textarea rows={2} value={draft.reason} maxLength={1000} onChange={event=>setDraft({...draft,reason:event.target.value})} placeholder="Why is this exact relationship approved?"/></label></div><button type="button" className="button button-secondary compact" onClick={addManual}><Plus size={14}/>Add manual relationship</button><div className="connection-form-actions"><button type="button" className="button button-confirm" disabled={busy} onClick={()=>void save()}><Save size={15}/>{busy?'Saving approval…':'Save versioned TDE approval'}</button></div></>}
    </>}
  </section>
}
