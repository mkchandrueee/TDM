import { useCallback, useEffect, useRef, useState, type ChangeEvent, type FormEvent } from 'react'
import { Check, ChevronDown, CircleAlert, Database, FileJson2, FileSpreadsheet, Network, Pencil, Play, Plus, Power, RefreshCw, Save, Trash2, Upload, X } from 'lucide-react'
import { apiRequest, type HumanRole } from './api'
import { DatasetActivityIcons } from './DatasetActivity'
import { ConfirmDialog } from './components/ui'
import FederatedConnectionPlans from './FederatedConnectionPlans'
import './connections-page.css'

type ConnectorType = 'postgresql' | 'mysql' | 'oracle' | 'mssql' | 'json' | 'csv' | 'delimited_text'
type ConnectionDirection = 'source' | 'target' | 'both'
type OperationScope = 'read_only' | 'write_only' | 'read_write'
type PreviewField = {name: string; type: string; fields?: PreviewField[]}
type StructureKey = {name: string; fields: string[]; evidence: string; validated: boolean | null; enforced: boolean | null}
type StructureEdge = {id: string; name: string | null; source_object: string; source_fields: string[]; target_object: string; target_fields: string[]; evidence: string; validated: boolean | null; enforced: boolean | null}
type StructureObject = {name: string; type: string; database?: string | null; schema?: string | null; fields: PreviewField[]; primary_key?: StructureKey | null; unique_constraints?: StructureKey[]; unique_indexes?: StructureKey[]; foreign_key_edges?: StructureEdge[]}
type ConnectionStructure = {format: string; fields?: PreviewField[]; objects?: StructureObject[]; structure_snapshot_version?: number; [key: string]: unknown}
type Connection = {
  id: string; workspace_id: string; name: string; connector_type: ConnectorType; direction: ConnectionDirection
  environment: string; purpose: string | null; owner: string; operation_scope: OperationScope
  secret_configured: boolean; connector_config: Record<string, unknown>; status: 'draft' | 'tested' | 'enabled' | 'disabled'
  database_credential_mode: 'shared' | 'per_database'; database_credentials: Array<{database_name:string;configured:boolean}>
  tested_at: string | null; tested_by: string | null; test_result: {succeeded: boolean; message: string} | null
  structure_preview: ConnectionStructure | null; created_by: string; updated_by: string; created_at: string; updated_at: string
}
type ConnectionTestRun = {id:string;requested_by:string;started_at:string;finished_at:string;outcome:'succeeded'|'failed';safe_error_code:string|null;safe_message:string;correlation_id:string}

type ServerCredentials = {host:string;port:string;username:string;password:string;initial_database:string}
type DatabaseBindingForm = {database_name:string;credential_configured:boolean}
type ConnectionForm = {operation_scope: OperationScope; direction: ConnectionDirection; name: string; connector_type: ConnectorType; environment: string; purpose: string; owner: string; credentials:ServerCredentials; credential_mode:'shared'|'per_database'; file_path: string; delimiter: string; has_header: boolean; encoding: string; record_limit: string; schemasByDatabase:Record<string,string[]>; databases: DatabaseBindingForm[]}
type CredentialGroup = {id:string;credentials:ServerCredentials;available:string[];selected:string[];loading:boolean;error:string;credential_configured?:boolean}
const blankCredentials=():ServerCredentials=>({host:'',port:'5432',username:'',password:'',initial_database:''})
const defaults: ConnectionForm = { operation_scope: 'read_write', direction: 'source', name: '', connector_type: 'postgresql', environment: 'Development', purpose: '', owner: '', credentials:blankCredentials(), credential_mode:'shared', file_path: '', delimiter: ',', has_header: true, encoding: 'utf-8-sig', record_limit: '1000', schemasByDatabase:{}, databases: [] }
const CHUNKED_UPLOAD_THRESHOLD = 10 * 1024 * 1024

export default function ConnectionsPage({role, workspaceId, ownerName}:{role:HumanRole;workspaceId:string;ownerName:string}) {
  const [items,setItems]=useState<Connection[]>([])
  const [form,setForm]=useState({...defaults})
  const [editing,setEditing]=useState<string|null>(null)
  const [showForm,setShowForm]=useState(false)
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const [notice,setNotice]=useState('')
  const [uploadingFile,setUploadingFile]=useState(false)
  const [uploadProgress,setUploadProgress]=useState(0)
  const [uploadUsesChunks,setUploadUsesChunks]=useState(false)
  const [uploadedFileName,setUploadedFileName]=useState('')
  const [fileUploadError,setFileUploadError]=useState('')
  const fileInput=useRef<HTMLInputElement>(null)
  const [selected,setSelected]=useState<string|null>(null)
  const [wizardStep,setWizardStep]=useState(0)
  const [availableDatabases,setAvailableDatabases]=useState<string[]>([])
  const [discoveringDatabases,setDiscoveringDatabases]=useState(false)
  const [databaseDiscoveryError,setDatabaseDiscoveryError]=useState('')
  const [availableSchemas,setAvailableSchemas]=useState<Record<string,string[]>>({})
  const [schemaDiscovery,setSchemaDiscovery]=useState<Record<string,{loading:boolean;error:string}>>({})
  const [credentialGroups,setCredentialGroups]=useState<CredentialGroup[]>([])
  const [connectionTestResult,setConnectionTestResult]=useState<{succeeded:boolean;message:string}|null>(null)
  const [connectionTab,setConnectionTab]=useState<'overview'|'discovery'|'locking'|'federated'>('overview')
  const [deleting,setDeleting]=useState<Connection|null>(null)
  const [testHistory,setTestHistory]=useState<ConnectionTestRun[]>([])
  const [testHistoryRefresh,setTestHistoryRefresh]=useState(0)
  const [testHistoryPage,setTestHistoryPage]=useState(1)
  const testHistoryPageSize=5
  const testHistoryPageCount=Math.max(1,Math.ceil(testHistory.length/testHistoryPageSize))
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
      .then(rows=>{if(active){setTestHistory(rows);setTestHistoryPage(1)}})
      .catch(()=>{if(active)setTestHistory([])})
    return()=>{active=false}
  },[selected,workspaceId,role,testHistoryRefresh])

  function resetForm(){setForm({...defaults,owner:ownerName});setEditing(null);setShowForm(false);setWizardStep(0);setAvailableDatabases([]);setDiscoveringDatabases(false);setDatabaseDiscoveryError('');setAvailableSchemas({});setSchemaDiscovery({});setCredentialGroups([]);setConnectionTestResult(null);setUploadedFileName('');setFileUploadError('');setUploadProgress(0);setUploadUsesChunks(false)}
  useEffect(()=>{if(!editing)setForm(current=>({...current,owner:ownerName}))},[ownerName,editing])
  function edit(item:Connection){
    const config=item.connector_config
    const savedDatabaseCredentials=new Set((item.database_credentials??[]).map(entry=>entry.database_name.toLocaleLowerCase()))
    const databaseBindings=(Array.isArray(config.databases)?config.databases.map(String):[]).map(database_name=>({database_name,credential_configured:savedDatabaseCredentials.has(database_name.toLocaleLowerCase())}))
    const savedSchemaMap=(config.schemas_by_database&&typeof config.schemas_by_database==='object'?config.schemas_by_database:{}) as Record<string,unknown>
    const savedSchemas=Object.fromEntries(databaseBindings.map(({database_name})=>{
      const mapped=Object.entries(savedSchemaMap).find(([name])=>name.toLocaleLowerCase()===database_name.toLocaleLowerCase())?.[1]
      const legacy=Array.isArray(config.schemas)?config.schemas:typeof config.schema==='string'?[config.schema]:[]
      return [database_name,Array.isArray(mapped)?mapped.map(String):legacy.map(String)]
    }))
    const connectorPort=({postgresql:'5432',mysql:'3306',oracle:'1521',mssql:'1433'} as Partial<Record<ConnectorType,string>>)[item.connector_type]??'5432'
    setForm({...defaults,operation_scope:item.operation_scope,direction:item.direction,name:item.name,connector_type:item.connector_type,environment:item.environment,purpose:item.purpose??'',owner:item.owner,credentials:{...blankCredentials(),port:connectorPort},
      credential_mode:item.database_credential_mode??'shared',file_path:String(config.file_path??''),delimiter:String(config.delimiter??(item.connector_type==='delimited_text'?'\t':',')),has_header:Boolean(config.has_header??true),encoding:String(config.encoding??'utf-8-sig'),record_limit:String(config.record_limit??1000),schemasByDatabase:savedSchemas,databases:databaseBindings})
    setCredentialGroups(item.database_credential_mode==='per_database'?databaseBindings.map(binding=>({id:crypto.randomUUID(),credentials:blankCredentials(),available:[binding.database_name],selected:[binding.database_name],loading:false,error:'',credential_configured:binding.credential_configured})):[])
    setAvailableDatabases([]);setDatabaseDiscoveryError('');setAvailableSchemas({});setSchemaDiscovery({});setEditing(item.id);setWizardStep(0);setConnectionTestResult(null);setShowForm(true)
  }
  function connectorConfig(){
    if(['postgresql','oracle','mssql'].includes(form.connector_type))return {schemas_by_database:Object.fromEntries(form.databases.map(({database_name})=>[database_name,form.schemasByDatabase[database_name]??[]])),databases:form.databases.map(binding=>binding.database_name)}
    if(form.connector_type==='mysql')return {databases:form.databases.map(binding=>binding.database_name)}
    if(form.connector_type==='json')return {file_path:form.file_path,encoding:form.encoding,record_limit:Number(form.record_limit)}
    return {file_path:form.file_path,delimiter:form.delimiter,has_header:form.has_header,encoding:form.encoding,record_limit:Number(form.record_limit)}
  }
  const isDatabaseConnector=['postgresql','mysql','oracle','mssql'].includes(form.connector_type)
  const needsSchemaSelection=['postgresql','oracle','mssql'].includes(form.connector_type)
  function completeCredentials(credentials:ServerCredentials){return Boolean(credentials.host.trim()&&credentials.username.trim()&&Number(credentials.port)>=1&&Number(credentials.port)<=65535&&(!['postgresql','oracle'].includes(form.connector_type)||credentials.initial_database.trim()))}
  function credentialPayload(credentials:ServerCredentials){return {host:credentials.host.trim(),port:Number(credentials.port),username:credentials.username.trim(),password:credentials.password,initial_database:credentials.initial_database.trim()||null}}
  function credentialFields(value:ServerCredentials,onChange:(next:ServerCredentials)=>void){const port=({postgresql:'5432',mysql:'3306',oracle:'1521',mssql:'1433'} as Partial<Record<ConnectorType,string>>)[form.connector_type]??'5432';const databaseLabel=form.connector_type==='oracle'?'Oracle service name':form.connector_type==='mssql'?'Default database (optional)':'Starting database';const databaseHint=['postgresql','oracle'].includes(form.connector_type)?`required for ${form.connector_type==='oracle'?'Oracle service':'database'} discovery`:'optional';return <div className="connection-form-grid"><label className="form-field">Server / host<input autoComplete="off" value={value.host} onChange={event=>onChange({...value,host:event.target.value})} placeholder="127.0.0.1"/></label><label className="form-field">Port<input inputMode="numeric" value={value.port} onChange={event=>onChange({...value,port:event.target.value})} placeholder={port}/></label><label className="form-field">Username<input autoComplete="username" value={value.username} onChange={event=>onChange({...value,username:event.target.value})}/></label><label className="form-field">Password<input type="password" autoComplete="new-password" value={value.password} onChange={event=>onChange({...value,password:event.target.value})} placeholder="Leave blank if the database account has no password"/></label><label className="form-field span-two">{databaseLabel} <span className="quiet-message">({databaseHint})</span><input autoComplete="off" value={value.initial_database} onChange={event=>onChange({...value,initial_database:event.target.value})} placeholder={form.connector_type==='oracle'?'For example: ORCLPDB1':form.connector_type==='mssql'?'For example: CRM_SOURCE':'Enter an existing database on this server'}/></label></div>}
  async function discoverDatabases(credentials:ServerCredentials,groupId?:string){
    if(!completeCredentials(credentials)){setDatabaseDiscoveryError(['postgresql','oracle'].includes(form.connector_type)?'Enter the server address, port, username, and database/service name first.':'Enter the server address, port, and username first.');return}
    setDatabaseDiscoveryError('');setDiscoveringDatabases(true)
    if(groupId)setCredentialGroups(groups=>groups.map(group=>group.id===groupId?{...group,loading:true,error:''}:group))
    try{
      const result=await apiRequest<{databases:string[]}>(`/workspaces/${workspaceId}/connections/available-databases`,role,{method:'POST',body:JSON.stringify({connector_type:form.connector_type,credentials:credentialPayload(credentials)})})
      const names=result.databases??[]
      if(groupId)setCredentialGroups(groups=>groups.map(group=>group.id===groupId?{...group,available:names,loading:false,error:names.length?'':'No accessible databases were returned.'}:group))
      else setAvailableDatabases(names)
      setDatabaseDiscoveryError(names.length?'': 'No accessible databases were returned for this credential.')
    }catch(cause){
      const message=cause instanceof Error?cause.message:'Unable to discover databases'
      if(groupId)setCredentialGroups(groups=>groups.map(group=>group.id===groupId?{...group,loading:false,error:message}:group))
      else setDatabaseDiscoveryError(message)
    }finally{setDiscoveringDatabases(false)}
  }
  function credentialsForDatabase(databaseName:string){
    if(form.credential_mode==='shared')return form.credentials
    return credentialGroups.find(group=>group.selected.some(name=>name.toLocaleLowerCase()===databaseName.toLocaleLowerCase()))?.credentials??blankCredentials()
  }
  async function discoverSchemas(databaseName:string){
    const credentials=credentialsForDatabase(databaseName)
    if(!completeCredentials(credentials)){
      setSchemaDiscovery(current=>({...current,[databaseName]:{loading:false,error:'Enter the credentials for this database in the previous step to discover its schemas.'}}))
      return
    }
    setSchemaDiscovery(current=>({...current,[databaseName]:{loading:true,error:''}}))
    try{
      const result=await apiRequest<{schemas:string[]}>(`/workspaces/${workspaceId}/connections/available-schemas`,role,{method:'POST',body:JSON.stringify({connector_type:form.connector_type,database_name:databaseName,credentials:credentialPayload(credentials)})})
      setAvailableSchemas(current=>({...current,[databaseName]:result.schemas??[]}))
      setSchemaDiscovery(current=>({...current,[databaseName]:{loading:false,error:result.schemas?.length?'':'No accessible schemas were returned for this database.'}}))
    }catch(cause){
      setSchemaDiscovery(current=>({...current,[databaseName]:{loading:false,error:cause instanceof Error?cause.message:'Unable to discover schemas'}}))
    }
  }
  function selectSchema(databaseName:string,schemaName:string,checked:boolean){
    setForm(current=>{
      const selected=current.schemasByDatabase[databaseName]??[]
      const next=checked?[...new Set([...selected,schemaName])]:selected.filter(schema=>schema!==schemaName)
      return {...current,schemasByDatabase:{...current.schemasByDatabase,[databaseName]:next}}
    })
  }
  function selectSharedDatabase(database_name:string,checked:boolean){
    setForm(current=>{
      const databases=checked
        ? current.databases.some(row=>row.database_name.toLocaleLowerCase()===database_name.toLocaleLowerCase())?current.databases:[...current.databases,{database_name,credential_configured:false}]
        :current.databases.filter(row=>row.database_name.toLocaleLowerCase()!==database_name.toLocaleLowerCase())
      const schemasByDatabase={...current.schemasByDatabase}
      if(checked&&!schemasByDatabase[database_name])schemasByDatabase[database_name]=[]
      if(!checked)delete schemasByDatabase[database_name]
      return {...current,databases,schemasByDatabase}
    })
  }
  function syncCredentialGroups(groups:CredentialGroup[]){
    setCredentialGroups(groups)
    const selected=groups.flatMap(group=>group.selected.map(database_name=>({database_name,credential_configured:Boolean(group.credential_configured)})))
    setForm(current=>({...current,databases:selected,schemasByDatabase:Object.fromEntries(Object.entries(current.schemasByDatabase).filter(([database])=>selected.some(row=>row.database_name.toLocaleLowerCase()===database.toLocaleLowerCase())))}))
  }
  const wizardSteps=isDatabaseConnector?['Connector type','Basic information','Server credentials','Database selection','Test connection']:['Connector type','Basic information','File settings','Test connection']
  function continueWizard(){
    setError('')
    if(wizardStep===1&&(!form.name.trim()||form.name.trim().length<2||!form.environment.trim()||form.environment.trim().length<2)){setError('Enter a connection name and an environment of at least two characters.');return}
    if(isDatabaseConnector&&wizardStep===2){
      if(form.credential_mode==='shared'&&!completeCredentials(form.credentials)&&!(editingConnection?.secret_configured&&editingConnection.database_credential_mode==='shared')){setError(['postgresql','oracle'].includes(form.connector_type)?'Enter the server address, port, username, and database/service name.':'Enter the server address, port, and username.');return}
      if(form.credential_mode==='per_database'&&!credentialGroups.length&&!form.databases.length){setError('Add at least one database credential.');return}
      if(form.credential_mode==='per_database'&&credentialGroups.some(group=>!completeCredentials(group.credentials)&&!form.databases.some(row=>group.selected.includes(row.database_name)&&row.credential_configured))){setError(['postgresql','oracle'].includes(form.connector_type)?'Enter the server address, port, username, and database/service name for each new credential group.':'Enter the server address, port, and username for each new credential group.');return}
    }
    if(isDatabaseConnector&&wizardStep===3&&!form.databases.length){setError('Select at least one database to continue.');return}
    if(needsSchemaSelection&&wizardStep===3&&form.databases.some(({database_name})=>!(form.schemasByDatabase[database_name]??[]).length)){setError('Discover and select at least one schema for every selected database.');return}
    if(needsSchemaSelection&&wizardStep===3&&form.databases.some(({database_name})=>(form.schemasByDatabase[database_name]??[]).length>20)){setError('Select no more than 20 schemas per database.');return}
    if(isDatabaseConnector&&wizardStep===3&&form.databases.length>20){setError('Select no more than 20 databases.');return}
    if(!isDatabaseConnector&&wizardStep===2&&(!form.file_path.trim()||Number(form.record_limit)<1||Number(form.record_limit)>10000||!Number.isInteger(Number(form.record_limit)))){setError('Choose a source file and enter a record limit between 1 and 10,000.');return}
    setWizardStep(step=>Math.min(wizardSteps.length-1,step+1))
  }
  async function uploadSelectedFile(event:ChangeEvent<HTMLInputElement>){
    const file=event.target.files?.[0]
    event.target.value=''
    if(!file)return
    setUploadingFile(true);setFileUploadError('');setUploadedFileName('');setUploadProgress(0);setUploadUsesChunks(file.size>CHUNKED_UPLOAD_THRESHOLD)
    try{
      const params=new URLSearchParams({connector_type:form.connector_type,filename:file.name})
      let result:{file_path:string;file_name:string;size_bytes:number}|null=null
      if(file.size>CHUNKED_UPLOAD_THRESHOLD){
        const job=await apiRequest<{id:string;chunk_size:number;total_chunks:number}>(`/workspaces/${workspaceId}/connections/file-uploads?${params}&total_size=${file.size}`,role,{method:'POST'})
        for(let part=0;part<job.total_chunks;part++){
          const start=part*job.chunk_size
          const blob=file.slice(start,Math.min(file.size,start+job.chunk_size))
          await apiRequest<{part_number:number}>(`/workspaces/${workspaceId}/connections/file-uploads/${job.id}/parts/${part}`,role,{method:'PUT',headers:{'Content-Type':'application/octet-stream'},body:blob})
          setUploadProgress(Math.round(((part+1)/job.total_chunks)*90))
        }
        await apiRequest<{status:string}>(`/workspaces/${workspaceId}/connections/file-uploads/${job.id}/complete`,role,{method:'POST'})
        let completed=false
        let attempts=0
        while(!completed&&attempts<120){
          const status=await apiRequest<{status:string;file_path?:string;filename?:string;size_bytes?:number;error?:string}>(`/workspaces/${workspaceId}/connections/file-uploads/${job.id}`,role)
          if(status.status==='completed'&&status.file_path&&status.filename){result={file_path:status.file_path,file_name:status.filename,size_bytes:status.size_bytes??file.size};completed=true}
          else if(status.status==='failed')throw new Error(status.error||'The upload job could not assemble the file')
          else{setUploadProgress(95);await new Promise(resolve=>window.setTimeout(resolve,500));attempts++}
        }
        if(!completed)throw new Error('The upload job is still running. Please retry the upload shortly.')
      }else{
        result=await apiRequest<{file_path:string;file_name:string;size_bytes:number}>(`/workspaces/${workspaceId}/connections/file-upload?${params}`,role,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file})
      }
      if(!result)throw new Error('The upload job did not return a completed file')
      setUploadProgress(100)
      setForm(current=>({...current,file_path:result.file_path}));setUploadedFileName(result.file_name)
    }catch(cause){setFileUploadError(cause instanceof Error?cause.message:'Unable to upload the selected file')}
    finally{setUploadingFile(false)}
  }
  async function save(event:FormEvent){
    event.preventDefault()
    const intent=(event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement|null
    const shouldTest=intent?.value==='test'
    if(wizardStep!==wizardSteps.length-1){setError('Complete the setup steps before saving or testing.');return}
    if(intent?.value==='save'&&connectionTestResult?.succeeded){resetForm();return}
    setBusy(true);setError('');setNotice('')
    const databaseConnector=['postgresql','mysql','oracle','mssql'].includes(form.connector_type)
    const databaseCredentials=databaseConnector&&form.credential_mode==='per_database'?form.databases.map(({database_name})=>{
      const group=credentialGroups.find(row=>row.selected.some(name=>name.toLocaleLowerCase()===database_name.toLocaleLowerCase()))
      return {database_name,credentials:group&&completeCredentials(group.credentials)?credentialPayload(group.credentials):undefined}
    }):[]
    const payload={name:form.name,connector_type:form.connector_type,direction:form.direction,environment:form.environment,purpose:form.purpose||null,owner:form.owner||ownerName,operation_scope:databaseConnector?form.operation_scope:'read_only',server_credentials:databaseConnector&&form.credential_mode==='shared'&&completeCredentials(form.credentials)?credentialPayload(form.credentials):undefined,database_credential_mode:databaseConnector?form.credential_mode:'shared',database_credentials:databaseCredentials,connector_config:connectorConfig()}
    let testOutcome:{succeeded:boolean;message:string}|null=null
    try{
      const saved=editing
        ? await apiRequest<Connection>(`/workspaces/${workspaceId}/connections/${editing}`,role,{method:'PATCH',body:JSON.stringify(payload)})
        : await apiRequest<Connection>(`/workspaces/${workspaceId}/connections`,role,{method:'POST',body:JSON.stringify(payload)})
      setForm(current=>({...current,credentials:blankCredentials()}))
      setCredentialGroups(groups=>groups.map(group=>({...group,credentials:blankCredentials()})))
      setSelected(saved.id);setEditing(saved.id)
      if(shouldTest){
        const tested=await apiRequest<Connection>(`/workspaces/${workspaceId}/connections/${saved.id}/test`,role,{method:'POST'})
        setTestHistoryRefresh(value=>value+1)
        testOutcome={succeeded:Boolean(tested.test_result?.succeeded),message:tested.test_result?.message??(tested.test_result?.succeeded?'Connection test succeeded.':'Connection test failed.')}
      }else setNotice(editing?'Connection updated. Test the connection again before enabling it.':'Draft saved. Test the connection to verify access to the source.')
      await load()
      if(shouldTest){
        setEditing(saved.id);setConnectionTestResult(testOutcome)
        if(testOutcome?.succeeded)setNotice(testOutcome.message)
        else if(testOutcome)setError(testOutcome.message)
      }else resetForm()
    }catch(cause){setError(cause instanceof Error?cause.message:'Unable to save connection')}
    finally{setBusy(false)}
  }
  async function transition(item:Connection,action:'test'|'enable'|'disable'){
    setBusy(true);setError('');setNotice('')
    try{
      const result=await apiRequest<Connection>(`/workspaces/${workspaceId}/connections/${item.id}/${action}`,role,{method:'POST'})
      setSelected(item.id);setConnectionTab('overview');await load()
      if(action==='test'){
        setTestHistoryRefresh(value=>value+1)
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
    {showForm&&<div className="connection-modal-overlay" onClick={event=>{if(event.target===event.currentTarget)resetForm()}}><section className="panel connection-editor" role="dialog" aria-modal="true" aria-labelledby="connection-modal-title"><div className="panel-heading"><div><h2 id="connection-modal-title">{editing?'Edit connection':'Add a connection'}</h2><p>Configure your source one step at a time.</p></div><button type="button" className="icon-button" aria-label="Close connection form" onClick={resetForm}><X size={18}/></button></div>
      <nav className="connection-wizard-steps" aria-label="Connection setup steps">{wizardSteps.map((label,index)=><button type="button" key={label} className={index===wizardStep?'is-current':index<wizardStep?'is-complete':''} onClick={()=>{if(index<wizardStep){setWizardStep(index);setConnectionTestResult(null)}}}><span>{index<wizardStep?<Check size={13}/>:index+1}</span>{label}</button>)}</nav>
      <form className="connection-form" onSubmit={save}>
        {wizardStep===0&&<div className="connection-wizard-panel"><div className="eyebrow">STEP 1 / SOURCE CONNECTION</div><h3>Choose connector type</h3><label className="form-field">Connector type<select value={form.connector_type} onChange={e=>{const connector_type=e.target.value as ConnectorType;const port=({postgresql:'5432',mysql:'3306',oracle:'1521',mssql:'1433'} as Partial<Record<ConnectorType,string>>)[connector_type];setForm(current=>({...current,operation_scope:['postgresql','mysql','oracle','mssql'].includes(connector_type)?'read_write':'read_only',connector_type,credentials:{...current.credentials,port:port??current.credentials.port},databases:[],schemasByDatabase:{},delimiter:connector_type==='delimited_text'?'\t':connector_type==='csv'?',':current.delimiter}));setAvailableDatabases([]);setAvailableSchemas({});setSchemaDiscovery({});setCredentialGroups([])}}><option value="postgresql">PostgreSQL</option><option value="mysql">MySQL</option><option value="oracle">Oracle Database</option><option value="mssql">Microsoft SQL Server</option><option value="json">JSON file</option><option value="csv">CSV file</option><option value="delimited_text">Delimited text</option></select></label></div>}
        {wizardStep===1&&<div className="connection-wizard-panel"><div className="eyebrow">STEP 2 / BASIC INFORMATION</div><h3>Connection details</h3><div className="connection-form-grid"><label className="form-field">Connection name<input required minLength={2} maxLength={180} value={form.name} onChange={e=>setForm({...form,name:e.target.value})}/></label><label className="form-field">Environment<input required maxLength={40} value={form.environment} onChange={e=>setForm({...form,environment:e.target.value})}/></label>{isDatabaseConnector&&<label className="form-field connection-operation-scope">Operation scope<select value={form.operation_scope} onChange={e=>setForm({...form,operation_scope:e.target.value as OperationScope})}><option value="read_only">Read only</option><option value="read_write">Read and write</option><option value="write_only">Write only</option></select><small>Writes also require SSD grants and privileges for the saved database login. Entity locks remain enforced.</small></label>}<label className="form-field">Owner<input value={form.owner||ownerName} readOnly/></label><label className="form-field span-two">Purpose<textarea maxLength={2000} rows={3} value={form.purpose} onChange={e=>setForm({...form,purpose:e.target.value})} placeholder="What will this source be used for?"/></label></div></div>}
        {isDatabaseConnector&&wizardStep===2&&<div className="connection-wizard-panel"><div className="eyebrow">STEP 3 / SERVER CREDENTIALS</div><h3>Provide server credentials</h3><fieldset className="database-credential-choice"><legend>Access method</legend><label><input type="radio" checked={form.credential_mode==='shared'} onChange={()=>{setForm(current=>({...current,credential_mode:'shared'}));setCredentialGroups([])}}/> One credential for multiple databases</label><label><input type="radio" checked={form.credential_mode==='per_database'} onChange={()=>{setForm(current=>({...current,credential_mode:'per_database'}));setCredentialGroups(groups=>groups.length?groups:[{id:crypto.randomUUID(),credentials:blankCredentials(),available:[],selected:form.databases.map(row=>row.database_name),loading:false,error:''}])}}/> Separate credentials for different databases</label></fieldset>{form.credential_mode==='shared'?<>{credentialFields(form.credentials,credentials=>setForm(current=>({...current,credentials}))) }{editingConnection?.secret_configured&&<p className="quiet-message">A credential is already saved. Leave these fields blank to keep it, or enter new values to replace it.</p>}</>:<div className="credential-group-list">{credentialGroups.map((group,index)=><section className="database-discovery-card" key={group.id}><strong>Credential group {index+1}</strong>{credentialFields(group.credentials,credentials=>setCredentialGroups(groups=>groups.map(row=>row.id===group.id?{...row,credentials,available:[]}:row)))}{group.credential_configured&&<p className="quiet-message">Saved credential is retained if these fields are left blank.</p>}<button type="button" className="button button-secondary" onClick={()=>setCredentialGroups(groups=>[...groups,{id:crypto.randomUUID(),credentials:blankCredentials(),available:[],selected:[],loading:false,error:''}])}><Plus size={15}/>Add credential group</button></section>)}</div>}<p className="quiet-message">Credentials are encrypted by TIGR before they are stored. Passwords are never shown again in the UI.</p></div>}
        {isDatabaseConnector&&wizardStep===3&&<div className="connection-wizard-panel"><div className="eyebrow">STEP 4 / DATABASE SELECTION</div><h3>Select databases and schemas</h3>{form.credential_mode==='shared'?<section className="database-discovery-card"><button type="button" className="button button-secondary compact" disabled={discoveringDatabases||!completeCredentials(form.credentials)} onClick={()=>void discoverDatabases(form.credentials)}><RefreshCw size={14}/>{discoveringDatabases?'Searching...':form.connector_type==='oracle'?'Use Oracle service':'Find databases'}</button>{databaseDiscoveryError&&<p role="alert">{databaseDiscoveryError}</p>}{!availableDatabases.length&&form.databases.length>0&&<p className="quiet-message">Saved selection: {form.databases.map(row=>row.database_name).join(', ')}. Enter replacement credentials in the previous step to refresh the available database list.</p>}<ul className="database-discovery-list">{availableDatabases.map(name=><li key={name}><label><input type="checkbox" checked={form.databases.some(row=>row.database_name.toLowerCase()===name.toLowerCase())} onChange={event=>selectSharedDatabase(name,event.target.checked)}/><Database size={15}/>{name}</label></li>)}</ul></section>:<div className="database-discovery-groups">{credentialGroups.map((group,index)=><section className="database-discovery-card" key={group.id}><strong>Credential group {index+1}</strong><button type="button" className="button button-secondary compact" disabled={group.loading||!completeCredentials(group.credentials)} onClick={()=>void discoverDatabases(group.credentials,group.id)}>{group.loading?'Searching...':'Find databases'}</button>{group.error&&<p role="alert">{group.error}</p>}<ul className="database-discovery-list">{group.available.map(name=><li key={name}><label><input type="checkbox" checked={group.selected.includes(name)} disabled={credentialGroups.some(other=>other.id!==group.id&&other.selected.some(value=>value.toLowerCase()===name.toLowerCase()))} onChange={event=>{const selected=event.target.checked?[...group.selected,name]:group.selected.filter(value=>value!==name);syncCredentialGroups(credentialGroups.map(row=>row.id===group.id?{...row,selected}:row))}}/><Database size={15}/>{name}</label></li>)}</ul></section>)}</div>}{form.databases.length>0&&<p>{form.databases.length} database(s) selected</p>}{needsSchemaSelection&&<div className="database-schema-selections"><h4>Select schemas for each database</h4>{form.databases.map(({database_name})=>{const available=availableSchemas[database_name]??[];const selectedSchemas=form.schemasByDatabase[database_name]??[];const status=schemaDiscovery[database_name]??{loading:false,error:''};const credentials=credentialsForDatabase(database_name);return <section className="database-discovery-card" key={database_name}><div className="database-schema-heading"><strong>{database_name}</strong><button type="button" className="button button-secondary compact" disabled={status.loading||!completeCredentials(credentials)} onClick={()=>void discoverSchemas(database_name)}><RefreshCw size={14}/>{status.loading?'Searching...':available.length?'Refresh schemas':'Find schemas'}</button></div>{status.error&&<p role="alert">{status.error}</p>}{!available.length&&selectedSchemas.length>0&&<p className="quiet-message">Saved schemas: {selectedSchemas.join(', ')}. Enter credentials in the previous step to refresh the list.</p>}{available.length>0?<ul className="database-discovery-list">{available.map(schema=><li key={schema}><label><input type="checkbox" checked={selectedSchemas.includes(schema)} onChange={event=>selectSchema(database_name,schema,event.target.checked)}/><Database size={15}/>{schema}</label></li>)}</ul>:!selectedSchemas.length&&<p className="quiet-message">Discover schemas for this database, then select one or more.</p>}</section>})}</div>}{form.connector_type==='mysql'&&<p className="quiet-message">For MySQL, each selected database is its schema namespace.</p>}{form.connector_type==='oracle'&&<p className="quiet-message">The Oracle “database” list uses the configured service name; schemas are the accessible Oracle users.</p>}</div>}
        {!isDatabaseConnector&&wizardStep===2&&<div className="connection-wizard-panel"><div className="eyebrow">STEP 3 / FILE SETTINGS</div><h3>Choose source file</h3><div className="connection-file-picker"><input required value={form.file_path} onChange={e=>{setForm({...form,file_path:e.target.value});setUploadedFileName('')}} placeholder="Relative file path"/><input ref={fileInput} type="file" hidden accept={form.connector_type==='csv'?'.csv':form.connector_type==='json'?'.json':'.txt,.tsv,.dat,.psv,.pipe'} onChange={event=>void uploadSelectedFile(event)}/><button type="button" className="button button-secondary" disabled={uploadingFile} onClick={()=>fileInput.current?.click()}><Upload size={15}/>{uploadingFile?`Uploading ${uploadProgress}%`:'Browse'}</button></div>{uploadedFileName&&<p className="quiet-message">{uploadedFileName} uploaded and ready to use.</p>}{uploadingFile&&uploadUsesChunks&&<progress max={100} value={uploadProgress} aria-label="File upload progress"/>}{fileUploadError&&<p role="alert">{fileUploadError}</p>}<div className="connection-form-grid">{(form.connector_type==='csv'||form.connector_type==='delimited_text')&&<><label className="form-field">Delimiter<input value={form.delimiter} onChange={e=>setForm({...form,delimiter:e.target.value})}/></label><label className="form-field">Header row<span className="check-field"><input type="checkbox" checked={form.has_header} onChange={e=>setForm({...form,has_header:e.target.checked})}/> First row contains column names</span></label></>}<label className="form-field">Encoding<select value={form.encoding} onChange={e=>setForm({...form,encoding:e.target.value})}><option>utf-8-sig</option><option>utf-8</option><option>latin-1</option></select></label><label className="form-field">Record limit<input type="number" min={1} max={10000} value={form.record_limit} onChange={e=>setForm({...form,record_limit:e.target.value})}/></label></div></div>}
        {wizardStep===wizardSteps.length-1&&<div className="connection-wizard-panel"><div className="eyebrow">{isDatabaseConnector?'STEP 5':'STEP 4'} / TEST CONNECTION</div><h3>Test connection</h3><p>{isDatabaseConnector?'TIGR will test access to the selected databases and inspect their structure.':'TIGR will validate the file and inspect its structure.'}</p><dl className="connection-review"><div><dt>Name</dt><dd>{form.name||'Not set'}</dd></div><div><dt>Connector</dt><dd>{form.connector_type}</dd></div>{isDatabaseConnector&&<div><dt>Databases</dt><dd>{form.databases.map(row=>row.database_name).join(', ')||'None selected'}</dd></div>}<div><dt>Purpose</dt><dd>{form.purpose||'Not provided'}</dd></div></dl>{connectionTestResult&&<div className={`test-result ${connectionTestResult.succeeded?'test-success':'test-failed'}`} role="status"><span>{connectionTestResult.message}</span></div>}</div>}
        <div className="connection-form-actions">{wizardStep>0&&<button type="button" className="button button-secondary" onClick={()=>{setWizardStep(step=>step-1);setConnectionTestResult(null);setError('')}}>Back</button>}{wizardStep<wizardSteps.length-1?<button type="button" className="button button-confirm" disabled={busy||uploadingFile} onClick={continueWizard}>Continue</button>:<><button type="submit" value="test" className="button button-secondary" disabled={busy||isDatabaseConnector&&!form.databases.length}><Play size={15}/>{busy?'Testing...':'Test connection'}</button><button type="submit" value="save" className="button button-confirm" disabled={busy}><Save size={15}/>{connectionTestResult?.succeeded?'Finish':'Save draft'}</button></>}<button type="button" className="button button-secondary" onClick={resetForm}>Cancel</button></div>
      </form></section></div>}


    <section className="panel connection-inventory">
      <div className="connection-inventory-heading"><div><span className="eyebrow">SAVED SOURCES</span><h2>Connection inventory</h2><p>Test each source before enabling it for data workflows.</p></div><div className="connection-inventory-tools"><label className="connection-picker-label"><select aria-label="Select connection" value={selected??''} onChange={event=>{setSelected(event.target.value||null);setConnectionTab('overview')}}><option value="">Choose a connection</option>{items.map(item=><option key={item.id} value={item.id}>{item.name} · {item.connector_type.toUpperCase()}</option>)}</select></label><span className="connection-count">{items.length} connection{items.length===1?'':'s'}</span><button className="icon-button" aria-label="Refresh connections" onClick={()=>void load()}><RefreshCw size={17}/></button></div></div>
      {!items.length?<div className="connection-empty"><span className="connection-empty-icon"><Network size={22}/></span><strong>No source connections yet</strong><p>Add a source, then test it to verify access and inspect its structure. File previews show column names without exposing cell values.</p><button className="button button-secondary" onClick={()=>{resetForm();setShowForm(true)}}><Plus size={15}/>Add connection</button></div>:<div className="connection-list">{items.map(item=><article key={item.id} className={`connection-card ${selected===item.id?'is-selected':''}`}>
        <div className="connection-card-heading"><div className="connection-card-main"><span className={`connection-type-icon connector-${item.connector_type}`}>{['postgresql','mysql','oracle','mssql'].includes(item.connector_type)?<Database size={20}/>:item.connector_type==='json'?<FileJson2 size={20}/>:<FileSpreadsheet size={20}/>}</span><div className="connection-card-title"><div><h3>{item.name}</h3><span className={`status-badge ${item.status==='enabled'?'status-success':item.status==='tested'?'status-warning':item.status==='disabled'?'status-neutral':'status-warning'}`}><i/>{item.status}</span></div><p>{item.connector_type.toUpperCase().replace('_',' ')}<span>·</span>{item.environment}</p><small>{item.purpose||`Created by: ${item.created_by}`}</small></div></div><div className="connection-actions"><button className="button button-secondary compact" disabled={busy} onClick={()=>edit(item)}><Pencil size={14}/>Edit</button><button className="button button-secondary compact" disabled={busy} onClick={()=>transition(item,'test')}><Play size={14}/>{busy&&selected===item.id?'Testing…':'Test connection'}</button>{item.status==='enabled'?<button className="button button-secondary compact" disabled={busy} onClick={()=>transition(item,'disable')}><Power size={14}/>Disable</button>:<button className="button button-confirm compact" disabled={busy||!item.test_result?.succeeded} onClick={()=>transition(item,'enable')}><Check size={14}/>Enable</button>}<button className="connection-details-toggle" onClick={()=>{if(selected===item.id)setSelected(null);else{setSelected(item.id);setConnectionTab('overview')}}} aria-expanded={selected===item.id}>{selected===item.id?'Hide details':'Details'}</button><button className="connection-delete-button" disabled={busy} onClick={()=>setDeleting(item)}><Trash2 size={14}/>Delete</button></div></div>
        <div className="connection-card-footer"><div className={`connection-test-summary ${item.test_result?.succeeded?'is-passed':item.test_result?'is-failed':'is-untested'}`}>{item.test_result?.succeeded?<Check size={15}/>:item.test_result?<CircleAlert size={15}/>:<Network size={15}/>}<span>{item.test_result?.message??'Not tested yet'}</span><small>{item.tested_at?new Date(item.tested_at).toLocaleString():'Run a test to verify source access'}</small></div></div>
        {selected===item.id&&<div className="connection-details" data-tab={connectionTab}>
          <div className="connection-detail-tabs" role="tablist" aria-label={`${item.name} details`}>
            {(['overview','discovery','locking','federated'] as const).map(tab=><button type="button" key={tab} role="tab" aria-selected={connectionTab===tab} className={connectionTab===tab?'active':''} onClick={()=>setConnectionTab(tab)}>{tab==='overview'?'Overview':tab==='discovery'?'Discovery':tab==='locking'?'Entity locking':'Federated plans'}</button>)}
          </div>
          {connectionTab==='overview'&&<><ConnectionOverview item={item}/><section className="connection-test-history"><h3>Recent connection tests</h3>{testHistory.length?<ul>{testHistory.slice(0,testHistoryPageSize).map(test=><li key={test.id}><span className={`status-badge ${test.outcome==='succeeded'?'status-success':'status-warning'}`}><i/>{test.outcome}</span><strong>{test.safe_message}</strong><small>{new Date(test.started_at).toLocaleString()} ? {test.requested_by}</small></li>)}</ul>:<p>No connection test history is available.</p>}</section></>}
          {connectionTab==='discovery'&&<>{item.structure_preview?<StructureView structure={item.structure_preview}/>:<div className="structure-explorer-empty">No discovered structure yet. Test the connection to inspect its databases, schemas, and fields.</div>}</>}
          {connectionTab==='locking'&&<LockConfigurationEditor workspaceId={workspaceId} connectionId={item.id} connectorType={item.connector_type} role={role}/>}
          {connectionTab==='federated'&&<div className="federated-config-tab"><FederatedConnectionPlans workspaceId={workspaceId} role={role}/></div>}
        </div>}
      </article>)}</div>}
    </section>
    {deleting&&<ConfirmDialog title="Delete connection?" confirmLabel={busy?'Deleting…':'Delete connection'} destructive onClose={()=>setDeleting(null)} onConfirm={()=>{if(!busy)void removeConnection(deleting)}}><p>Delete <strong>{deleting.name}</strong> and its connection test history? This cannot be undone. Connections referenced by data workflows cannot be deleted.</p></ConfirmDialog>}
  </div>
}

function ConnectionOverview({item}:{item:Connection}){
  const config=item.connector_config??{}
  const configRows:Array<[string,string]> = ['postgresql','mysql','oracle','mssql'].includes(item.connector_type)
    ? [
        ...(config.table_limit!==undefined?[['Discovery table limit',String(config.table_limit)]] as Array<[string,string]>:[]),
        ['Credentials','Encrypted credential configured; username and password are hidden'],
      ]
    : [
        ['File path',String(config.file_path??'Not configured')],
        ...(item.connector_type!=='json'?[['Delimiter',config.delimiter==='\t'?'Tab':String(config.delimiter??',')],['Header row',config.has_header===false?'No':'Yes']] as Array<[string,string]>:[]),
        ['Encoding',String(config.encoding??'utf-8-sig')],
        ['Maximum source records',String(config.record_limit??1000)],
      ]
  const hasDatabaseStructure=['postgresql','mysql','oracle','mssql'].includes(item.connector_type)
  return <section className={`connection-overview ${hasDatabaseStructure?'has-database-structure':'file-connection-overview'}`} aria-label="Connection overview details">
    <section className="connection-overview-section">
      <header><div><span className="eyebrow">SAVED PROFILE</span><h3>Connection details</h3><p>Values saved when this connection was created or last edited.</p></div><span className={`connection-overview-status status-${item.status}`}>{item.status}</span></header>
      <dl className="connection-overview-grid">
        <OverviewValue label="Name" value={item.name}/><OverviewValue label="Connector" value={item.connector_type.toUpperCase().replaceAll('_',' ')}/>
        <OverviewValue label="Environment" value={item.environment}/>
        <OverviewValue label="Access scope" value={item.operation_scope.replaceAll('_',' ')}/><OverviewValue label="Created by" value={item.created_by}/>
        <OverviewValue label="Purpose" value={item.purpose||'Not provided'} wide/>
        {configRows.map(([label,value])=><OverviewValue key={label} label={label} value={value} wide={label==='File path'}/>)}
        <OverviewValue label="Created" value={new Date(item.created_at).toLocaleString()}/>
        <OverviewValue label="Last updated" value={`${new Date(item.updated_at).toLocaleString()} · ${item.updated_by}`}/>
        <OverviewValue label="Last connection test" value={item.tested_at?`${new Date(item.tested_at).toLocaleString()} · ${item.tested_by??'User unavailable'}`:'Not tested yet'} wide/>
      </dl>
    </section>
    {hasDatabaseStructure&&<DatabaseStructureSummary item={item}/>}
  </section>
}

function DatabaseStructureSummary({item}:{item:Connection}){
  const config=item.connector_config??{}
  const configuredDatabases=Array.isArray(config.databases)?config.databases.map(String):[]
  const schemaMap=config.schemas_by_database&&typeof config.schemas_by_database==='object'?config.schemas_by_database as Record<string,unknown>:{}
  const objects=item.structure_preview?.objects??[]
  const databases=[...new Set([...configuredDatabases,...objects.map(object=>object.database).filter((name):name is string=>Boolean(name))])]
  const hasStructure=Boolean(item.structure_preview)
  const summaries=databases.map(database=>{
    const databaseObjects=objects.filter(object=>object.database===database||(!object.database&&databases.length===1))
    const discoveredSchemas=[...new Set(databaseObjects.map(object=>object.schema).filter((name):name is string=>Boolean(name)))].sort((a,b)=>a.localeCompare(b))
    const schemaEntry=Object.entries(schemaMap).find(([name])=>name.toLocaleLowerCase()===database.toLocaleLowerCase())?.[1]
    const configuredSchemas=Array.isArray(schemaEntry)?schemaEntry.map(String):typeof schemaEntry==='string'?[schemaEntry]:[]
    return {database,schemas:discoveredSchemas.length?discoveredSchemas:configuredSchemas,tables:databaseObjects.filter(object=>object.type.toLocaleLowerCase()==='table').length,views:databaseObjects.filter(object=>object.type.toLocaleLowerCase()==='view').length,fields:databaseObjects.reduce((total,object)=>total+object.fields.length,0)}
  })
  return <section className="connection-overview-section database-structure-panel" aria-label="Database structure"><header><div><span className="eyebrow">SOURCE</span><h3>Database details</h3><p>Selected databases, schemas, and discovered fields.</p></div></header>{summaries.length?<ul>{summaries.map(summary=><li key={summary.database}><div className="database-structure-source"><strong>{summary.database}</strong><small>Schema{summary.schemas.length===1?'':'s'}: {summary.schemas.length?summary.schemas.join(', '):'Not reported'}</small></div><div className="database-structure-counts"><span><strong>{hasStructure?summary.tables:'—'}</strong> tables</span>{Boolean(summary.views)&&<span><strong>{summary.views}</strong> views</span>}<span><strong>{hasStructure?summary.fields:'—'}</strong> fields</span></div></li>)}</ul>:<p className="connection-overview-empty">No databases are selected for this connection.</p>}</section>
}

function OverviewValue({label,value,wide=false}:{label:string;value:string;wide?:boolean}){
  return <div className={wide?'is-wide':''}><dt>{label}</dt><dd>{value}</dd></div>
}

function StructureView({structure}:{structure:ConnectionStructure}){
  const fields=structure.fields??[]
  const objects=structure.objects??[]
  const databases=[...new Set(objects.map(object=>object.database).filter((database):database is string=>Boolean(database)))]
  const [selectedDatabase,setSelectedDatabase]=useState(databases[0]??'')
  const activeDatabase=databases.includes(selectedDatabase)?selectedDatabase:databases[0]
  const databaseObjects=databases.length?objects.filter(object=>object.database===activeDatabase):objects
  const schemas=[...new Set(databaseObjects.map(object=>object.schema).filter((schema):schema is string=>Boolean(schema)))].sort((a,b)=>a.localeCompare(b))
  const [selectedSchema,setSelectedSchema]=useState(schemas[0]??'')
  const activeSchema=schemas.includes(selectedSchema)?selectedSchema:schemas[0]??''
  const visibleObjects=activeSchema?databaseObjects.filter(object=>object.schema===activeSchema):databaseObjects
  const visibleFieldCount=visibleObjects.reduce((sum,object)=>sum+object.fields.length,objects.length?0:fields.length)
  return <section className="structure-preview structure-explorer" aria-label="Discovered structure">
    <header className="structure-explorer-header"><div><h3>Discovered structure</h3><p>{structure.format} schema metadata · source row values are never shown{structure.structure_snapshot_version?` · snapshot v${structure.structure_snapshot_version}`:''}</p></div><div className="structure-explorer-counts"><span><strong>{visibleObjects.length}</strong> {visibleObjects.length===1?'object':'objects'}</span><span><strong>{visibleFieldCount}</strong> fields</span></div></header>
    {objects.length>0&&<div className="structure-explorer-toolbar">
      {databases.length>1&&<label className="structure-scope-select"><span>Database</span><select aria-label="Database" value={activeDatabase} onChange={event=>{setSelectedDatabase(event.target.value);setSelectedSchema('')}}>{databases.map(database=><option key={database} value={database}>{database}</option>)}</select></label>}
      {schemas.length>1&&<label className="structure-scope-select"><span>Schema</span><select aria-label="Schema" value={activeSchema} onChange={event=>setSelectedSchema(event.target.value)}>{schemas.map(schema=><option key={schema} value={schema}>{schema}</option>)}</select></label>}
      {schemas.length===1&&<div className="structure-scope-current"><span>Schema</span><strong>{schemas[0]}</strong></div>}
      <p>Choose a schema to browse its tables and fields.</p>
    </div>}
    {objects.length?visibleObjects.length?visibleObjects.map(object=><details className="structure-object" key={`${object.database??''}.${object.schema??''}.${object.name}`}><summary><span className="structure-object-name">{object.name.split('.').at(-1)}</span><span className="structure-object-scope">{[object.database,object.schema].filter(Boolean).join(' / ')}</span><span className="structure-object-meta">{object.type} · {object.fields.length} fields</span><ChevronDown size={16} aria-hidden="true"/></summary><FieldRows fields={object.fields}/>{object.primary_key&&<p className="quiet-message">Primary key: {object.primary_key.fields.join(' + ')}</p>}{!!(object.unique_constraints?.length||object.unique_indexes?.length)&&<p className="quiet-message">Unique keys/indexes: {[...(object.unique_constraints??[]),...(object.unique_indexes??[])].map(key=>key.fields.join(' + ')).join(' · ')}</p>}{!!object.foreign_key_edges?.length&&<div className="dataset-meta-list">{object.foreign_key_edges.map(edge=><div className="dataset-info" key={edge.id}><strong>{edge.source_object}.{edge.source_fields.join(' + ')} → {edge.target_object}.{edge.target_fields.join(' + ')}</strong><small>{edge.evidence.replaceAll('_',' ')} · {edge.validated===false?'not validated':edge.validated===true?'validated':'validation state unavailable'}</small></div>)}</div>}</details>):<p className="structure-explorer-empty">No discovered objects in this schema.</p>:<FieldRows fields={fields}/>}
  </section>
}
function FieldRows({fields}:{fields:PreviewField[]}){return <div className="structure-fields">{fields.map(field=><div className="structure-field" key={field.name}><span>{field.name}</span><code>{field.type}</code>{field.fields&&<div className="nested-fields">{field.fields.map(child=><div key={child.name}><span>{child.name}</span><code>{child.type}</code></div>)}</div>}</div>)}{!fields.length&&<p className="quiet-message">No fields were discovered.</p>}</div>}

type LockAnchor={object_name:string;key_kind:'primary'|'unique'|'fingerprint';key_name:string;key_fields:string[]}
type LockSchemaState={snapshot:{id:string;version_number:number;structure:ConnectionStructure}|null;entity_locking_enabled:boolean;connection_entity_locking_enabled:boolean;global_entity_locking_enabled:boolean;operational_audit?:{active_claim_count:number;affected_datasets:Array<{dataset_id:string;reference_code:string;name:string;blocked_record_count:number}>};configuration:{version_number:number;matches_current_snapshot:boolean;configuration:{anchors:LockAnchor[]}}|null}

function LockConfigurationEditor({workspaceId,connectionId,connectorType,role}:{workspaceId:string;connectionId:string;connectorType:ConnectorType;role:HumanRole}){
  const [state,setState]=useState<LockSchemaState|null>(null)
  const [anchors,setAnchors]=useState<LockAnchor[]>([])
  const [busy,setBusy]=useState(false)
  const [message,setMessage]=useState('')
  const [error,setError]=useState('')
  const [selectedDatabase,setSelectedDatabase]=useState('')
  const [selectedSchema,setSelectedSchema]=useState('')
  const canEdit=role==='Test Data Engineer'
  const canManageRollout=role==='Admin'||role==='Test Data Engineer'
  const fileSource=!['postgresql','mysql','oracle','mssql'].includes(connectorType)
  const selectedKeysAreUsable=anchors.length>0&&canEdit
  const needsConfigurationSave=!state?.configuration||!state.configuration.matches_current_snapshot
  const enableBlockedReason=!state?.global_entity_locking_enabled?'Entity locking is disabled by the deployment setting.':needsConfigurationSave&&!selectedKeysAreUsable?(canEdit?`Choose ${fileSource?'fingerprint fields':'entity lock keys'} below to enable locking.`:'A Test Data Engineer must select and approve entity lock keys first.'):null

  const load=useCallback(async()=>{
    try{
      const result=await apiRequest<LockSchemaState>('/workspaces/'+workspaceId+'/connections/'+connectionId+'/lock-schema',role)
      setState(result)
      setAnchors(result.configuration?.configuration.anchors??[])
      setError('')
    }catch(cause){setError(cause instanceof Error?cause.message:'Unable to load entity lock keys')}
  },[workspaceId,connectionId,role])
  useEffect(()=>{void load()},[load])

  const objects=state?.snapshot?.structure.objects??[]
  const databaseOptions=[...new Set(objects.map(object=>object.database).filter((value):value is string=>Boolean(value)))].sort((a,b)=>a.localeCompare(b))
  const activeDatabase=databaseOptions.includes(selectedDatabase)?selectedDatabase:databaseOptions[0]??''
  const databaseObjects=databaseOptions.length?objects.filter(object=>object.database===activeDatabase):objects
  const schemaOptions=[...new Set(databaseObjects.map(object=>object.schema).filter((value):value is string=>Boolean(value)))].sort((a,b)=>a.localeCompare(b))
  const activeSchema=schemaOptions.includes(selectedSchema)?selectedSchema:schemaOptions[0]??''
  const selectableObjects=activeSchema?databaseObjects.filter(object=>object.schema===activeSchema):databaseObjects
  const needsRefresh=!fileSource&&!!state?.snapshot&&objects.some(object=>object.primary_key===undefined||object.unique_constraints===undefined||object.unique_indexes===undefined)
  const fileFields=objects.find(object=>object.name==='source_file')?.fields??[]
  const selectedFileFields=anchors.find(anchor=>anchor.key_kind==='fingerprint')?.key_fields??[]
  const keyCandidates=selectableObjects.flatMap(object=>[
    ...(object.primary_key?.fields.length?[{object_name:object.name,key_kind:'primary' as const,key_name:object.primary_key.name,key_fields:object.primary_key.fields}]:[]),
    ...[...(object.unique_constraints??[]),...(object.unique_indexes??[])].map(key=>({object_name:object.name,key_kind:'unique' as const,key_name:key.name,key_fields:key.fields})),
  ])
  async function save(){
    setBusy(true);setError('');setMessage('')
    try{
      const result=await apiRequest<{version_number:number}>('/workspaces/'+workspaceId+'/connections/'+connectionId+'/lock-configurations',role,{method:'POST',body:JSON.stringify({anchors})})
      setMessage(fileSource?'Fingerprint ID fields saved as approved configuration v'+result.version_number+'.':'Entity locking saved as approved configuration v'+result.version_number+'. Records are matched by directly mapped entity keys.')
      await load()
    }catch(cause){setError(cause instanceof Error?cause.message:'Unable to save entity lock keys')}
    finally{setBusy(false)}
  }
  async function setRollout(enabled:boolean){
    setBusy(true);setError('');setMessage('')
    try{
      if(enabled&&needsConfigurationSave&&canEdit){
        await apiRequest('/workspaces/'+workspaceId+'/connections/'+connectionId+'/lock-configurations',role,{method:'POST',body:JSON.stringify({anchors})})
      }
      await apiRequest('/workspaces/'+workspaceId+'/connections/'+connectionId+'/entity-locking',role,{method:'PUT',body:JSON.stringify({enabled})})
      setMessage(enabled?'Entity locking is enabled for this source connection.':'Entity locking is disabled; active entity claims for this connection were released.')
      await load()
    }
    catch(cause){setError(cause instanceof Error?cause.message:'Unable to update entity locking')}
    finally{setBusy(false)}
  }
  function toggleFileField(field:string,checked:boolean){
    const next=checked?[...selectedFileFields,field]:selectedFileFields.filter(item=>item!==field)
    setAnchors(next.length?[{object_name:'source_file',key_kind:'fingerprint',key_name:'TDE fingerprint',key_fields:next}]:[])
  }
  return <section className="structure-preview lock-configuration-panel entity-lock-panel">
    <header className="entity-lock-panel-header"><div><h3>{fileSource?'Entity fingerprint ID':'Entity lock keys'}</h3><p>{fileSource?'Choose source fields that TIGR combines into a protected fingerprint to prevent duplicate assignment.':'Choose database keys that identify the same entity across generated data sets. Only declared primary and unique keys are eligible.'}</p></div><div className="entity-lock-panel-actions">{canManageRollout&&<button type="button" className={state?.connection_entity_locking_enabled?'button button-primary compact':'button button-confirm'} disabled={busy||(!state?.connection_entity_locking_enabled&&Boolean(enableBlockedReason))} title={!state?.connection_entity_locking_enabled?enableBlockedReason||undefined:undefined} onClick={()=>void setRollout(!state?.connection_entity_locking_enabled)}>{busy?'Saving...':state?.connection_entity_locking_enabled?'Disable locking':'Enable locking'}</button>}{state?.configuration&&<span className={`entity-lock-version ${state.configuration.matches_current_snapshot?'':'is-stale'}`}>Configuration v{state.configuration.version_number}{state.configuration.matches_current_snapshot?' · current':' · review needed'}</span>}</div></header>
    {error&&<div className="alert alert-warning" role="alert">{error}</div>}{message&&<div className="alert alert-info" role="status">{message}</div>}
    {!!state?.operational_audit&&<section className="entity-lock-impact"><header><div><span className="entity-lock-overline">OPERATIONAL IMPACT</span><h4>Protected records and active claims</h4></div><div className="entity-lock-impact-count"><strong>{state.operational_audit.active_claim_count}</strong><small>active claims</small></div></header><p>Protected records are counted without showing source keys or claim tokens.</p>{state.operational_audit.affected_datasets.length?<ul>{state.operational_audit.affected_datasets.map(dataset=><li key={dataset.dataset_id}><span><strong>{dataset.name}</strong><small>{dataset.reference_code} · {dataset.blocked_record_count} protected {dataset.blocked_record_count===1?'record':'records'}</small></span><DatasetActivityIcons datasetId={dataset.dataset_id}/></li>)}</ul>:<div className="entity-lock-impact-empty">No other data sets currently contain records affected by these claims.</div>}</section>}
    {!state?.snapshot?<div className="entity-lock-empty"><strong>Structure snapshot needed</strong><p>Run a successful connection test to discover keys and configure entity locking.</p></div>:<>
      <div className="entity-lock-snapshot"><span><small>Source structure snapshot</small><strong>Version {state.snapshot.version_number}</strong></span><small>Namespace is scoped to this connection.</small></div>
      {needsRefresh&&<p className="entity-lock-refresh-note">This snapshot predates grouped key metadata. Retest the connection to discover primary and unique keys before saving an approval.</p>}
      {!fileSource&&!!schemaOptions.length&&<div className="entity-lock-scope"><label><span>Database</span>{databaseOptions.length>1?<select aria-label="Entity lock database" value={activeDatabase} onChange={event=>{setSelectedDatabase(event.target.value);setSelectedSchema('')}}>{databaseOptions.map(database=><option key={database} value={database}>{database}</option>)}</select>:<strong>{activeDatabase||'Selected source'}</strong>}</label><label><span>Schema</span>{schemaOptions.length>1?<select aria-label="Entity lock schema" value={activeSchema} onChange={event=>setSelectedSchema(event.target.value)}>{schemaOptions.map(schema=><option key={schema} value={schema}>{schema}</option>)}</select>:<strong>{schemaOptions[0]}</strong>}</label><div className="entity-lock-scope-count"><strong>{keyCandidates.length}</strong><span>eligible keys</span></div></div>}
      <div className="entity-lock-choice-heading"><div><h4>{fileSource?'Fingerprint fields':'Eligible entity keys'}</h4><p>{fileSource?'Select all fields needed to uniquely identify a source record.':'Select a key below. Changing database or schema refreshes the available keys.'}</p></div>{!fileSource&&anchors.length>0&&<span>{anchors.length} selected</span>}</div>
      {fileSource?(!fileFields.length?<div className="entity-lock-empty"><strong>No source fields discovered</strong><p>Retest the file connection before choosing fingerprint fields.</p></div>:<div className="dataset-meta-list">{fileFields.map(field=><label className="dataset-info" key={field.name}><span><input type="checkbox" checked={selectedFileFields.includes(field.name)} disabled={!canEdit||busy} onChange={event=>toggleFileField(field.name,event.target.checked)}/>{field.name}</span><small>{field.type}</small></label>)}</div>):(!keyCandidates.length?<div className="entity-lock-empty"><strong>No eligible keys in this schema</strong><p>Choose another schema or add a primary or unique key in the source database. Field names alone cannot identify matching records.</p></div>:<div className="dataset-meta-list">{keyCandidates.map(key=>{const checked=anchors.some(item=>item.object_name===key.object_name&&item.key_kind===key.key_kind&&item.key_name===key.key_name&&JSON.stringify(item.key_fields)===JSON.stringify(key.key_fields));return <label className="dataset-info" key={key.object_name+':'+key.key_kind+':'+key.key_name}><span><input type="checkbox" checked={checked} disabled={!canEdit||busy} onChange={event=>setAnchors(rows=>event.target.checked?[...rows,key]:rows.filter(item=>!(item.object_name===key.object_name&&item.key_kind===key.key_kind&&item.key_name===key.key_name)))}/><span className="entity-lock-key-copy"><strong>{key.key_kind==='primary'?'Primary key':'Unique key'}</strong><small>{key.object_name}</small><b>{key.key_fields.join(' + ')}</b></span></span><small>{key.key_name}{key.key_kind==='unique'?' · unique key requires explicit TDE approval':''}</small></label>})}</div>)}
      {canEdit&&<div className="entity-lock-save-row"><p>{anchors.length?`${anchors.length} key${anchors.length===1?'':'s'} selected for this connection.`:'Select one or more eligible keys to enable entity locking.'}</p><button type="button" className="button button-confirm" disabled={busy||(fileSource?!selectedFileFields.length:!keyCandidates.length||!anchors.length)} onClick={()=>void save()}><Save size={15}/>{busy?'Saving...':fileSource?'Save fingerprint fields':'Save selected keys'}</button></div>}
    </>}
  </section>
}
