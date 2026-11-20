import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowRight, Check, CircleAlert, Database, Eye, KeySquare, ScanSearch, ShieldCheck } from 'lucide-react'
import { apiRequest, type HumanRole } from './api'
import { DatasetActivityIcons } from './DatasetActivity'
import { generationModeClass, generationModeLabel } from './datasetLabels'
import { DataSetDetailsDialog } from './DataSetsPage'

type Version={id:string;version_number:number;lifecycle_state:string;privacy_state:string;validation_state:string;record_count:number|null;selected_fields:string[];schema_scope:Array<{field:string;data_type:string;required:boolean;entity?:string|null;source_field?:string|null;json_path?:string|null}>;generation_settings:Record<string,any>;provenance:Record<string,any>;expiry_at:string|null;permitted_use:string[];validation_evidence:Record<string,string|number|boolean|null>}
type Dataset={id:string;reference_code:string;workspace_id:string;name:string;description:string|null;creation_pathway:string;latest_version:Version & {provenance:Record<string,any>}}
type Finding={id:string;dataset_id:string;version_id:string;field:string;suggested_type:string;suggested_transformation:string|null;applied_transformation:string|null;classification:'sensitive'|'non_sensitive';confidence:number;reviewed_by:string|null;reviewed_at:string|null}
type Classification='sensitive'|'non_sensitive'
type QaPrivacyField={field:string;data_type:string;classification:'sensitive'|'non_sensitive'|'not_assessed';is_masked:boolean;applied_transformation:string|null}
type Policy={id:string;name:string;version_number:number;version:string;rules:Array<{field:string;transformation:string}>;status:string;created_by:string;approved_by:string|null}
type Run={id:string;status:string;evidence:Record<string,string|number|boolean|null>;job:{status:string;progress:number;safe_summary:string|null;heartbeat_at:string|null}|null}
type LockSchema={configuration:{id:string;structure_snapshot_id:string;configuration:{anchors:Array<{object_name:string;key_fields:string[]}>}}|null}
const TRANSFORM_LABELS:Record<string,string>={tokenization:'Stable token',substitution:'Realistic substitution',redaction:'Full redaction',partial_redaction:'Keep last 4 characters',date_shift:'Date shift',numeric_transform:'Numeric offset',generalization:'Generalize to range'}
export default function PrivacyPage({role,workspaceId}:{role:HumanRole;workspaceId:string}){
  const [searchParams]=useSearchParams()
  const navigate=useNavigate()
  const [datasets,setDatasets]=useState<Dataset[]>([]),[findings,setFindings]=useState<Finding[]>([])
  const [qaFields,setQaFields]=useState<QaPrivacyField[]>([])
  const [detailsOpen,setDetailsOpen]=useState(false)
  const [manualClassificationMode,setManualClassificationMode]=useState(false),[manualClassifications,setManualClassifications]=useState<Record<string,Classification|''>>({}),[manualClassificationBusy,setManualClassificationBusy]=useState(false)
  const [datasetId,setDatasetId]=useState(()=>searchParams.get('dataset_id')??''),[transform,setTransform]=useState<Record<string,string>>({}),[run,setRun]=useState<Run|null>(null)
  const [entityLockFingerprintFields,setEntityLockFingerprintFields]=useState<Set<string>>(()=>new Set())
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[scanNotice,setScanNotice]=useState(''),[scanAttempted,setScanAttempted]=useState(false),[scanInProgress,setScanInProgress]=useState(false),[maskRequestInProgress,setMaskRequestInProgress]=useState(false)
  const dataset=datasets.find(item=>item.id===datasetId),version=dataset?.latest_version
  const manualOutputFields=(version?.schema_scope??[]).map((schema,index)=>version?.selected_fields?.[index]||schema.field).filter((field):field is string=>typeof field==='string'&&field.length>0)
  useEffect(()=>{
    let cancelled=false
    setEntityLockFingerprintFields(new Set())
    if(role==='QA Engineer')return()=>{cancelled=true}
    const connectionId=String(version?.provenance?.source_connection_id??'')
    const pinnedConfigurationId=String(version?.provenance?.lock_metadata?.lock_configuration_id??'')
    const pinnedSnapshotId=String(version?.provenance?.lock_metadata?.structure_snapshot_id??'')
    if(!workspaceId||!connectionId)return()=>{cancelled=true}
    const configurationQuery=pinnedConfigurationId?'?configuration_id='+encodeURIComponent(pinnedConfigurationId):''
    apiRequest<LockSchema>(`/workspaces/${workspaceId}/connections/${connectionId}/lock-schema${configurationQuery}`,role).then(result=>{
      if(cancelled||!result.configuration||(pinnedConfigurationId&&result.configuration.id!==pinnedConfigurationId)||(pinnedSnapshotId&&result.configuration.structure_snapshot_id!==pinnedSnapshotId))return
      const anchors=result.configuration.configuration.anchors??[]
      const fields=new Set<string>()
      for(const anchor of anchors){
        for(const schema of version?.schema_scope??[]){
          if(schema.entity===anchor.object_name&&schema.source_field&&anchor.key_fields.includes(schema.source_field))fields.add(schema.field)
        }
      }
      setEntityLockFingerprintFields(fields)
    }).catch(()=>undefined)
    return()=>{cancelled=true}
  },[workspaceId,role,version?.id,version?.provenance?.source_connection_id,version?.provenance?.lock_metadata?.lock_configuration_id,version?.provenance?.lock_metadata?.structure_snapshot_id])
  const maskingInProgress=run?.status==='queued'||run?.status==='running'
  const readyForPublication=version?.privacy_state==='compliant'&&version.lifecycle_state==='ready'
  const datasetSelectionLocked=busy||maskingInProgress
  const findingsReadOnly=version?.lifecycle_state==='published'
  const sensitive=useMemo(()=>findings.filter(item=>item.classification==='sensitive'),[findings])
  const sourceBacked=version?.provenance?.origin==='source_backed'
  const syntheticDataset=version?.provenance?.origin==='synthetic'
  const retryableSourceRefreshFailure=!!version&&!!sourceBacked&&version.lifecycle_state==='quarantined'&&version.validation_state==='failed'&&!version.validation_evidence.masking_run_id&&version.validation_evidence.failure_stage==='source_refresh'
  const allFindingsReviewed=findings.length>0&&findings.every(item=>item.reviewed_at!==null)
  const pendingReviewCount=findings.filter(item=>!item.reviewed_at).length
  const missingTransformation=sensitive.some(item=>transform[item.field]!==undefined?transform[item.field]==='-':!item.suggested_transformation)
  const incompatibleTransformations=sensitive.flatMap(item=>{const isSuggested=transform[item.field]===undefined,choice=isSuggested?item.suggested_transformation:transform[item.field];if(!choice||choice==='-')return[];const dataType=dataTypeForField(item.field),type=dataType.toLowerCase(),compatible=availableTransforms(item);return compatible.includes(choice)?[]:[{field:item.field,type,dataType,choice,isSuggested}]})
  const canRunMasking=!!version&&!syntheticDataset&&!maskingInProgress&&(version.validation_state==='passed'&&['draft','ready'].includes(version.lifecycle_state)||retryableSourceRefreshFailure)&&allFindingsReviewed&&(sensitive.length===0?!!sourceBacked:sensitive.every(item=>transform[item.field]!==undefined?transform[item.field]!=='-':!!item.suggested_transformation))&&incompatibleTransformations.length===0
  const incompatibleMessages=incompatibleTransformations.map(item=>{const category=/int|serial|bigint|numeric|decimal|float|double|real/.test(item.type)?'numeric':/date|time/.test(item.type)?'date/time':item.type.includes('uuid')?'UUID':item.type.includes('bool')?'boolean':'text';const compatible=category==='numeric'?'Numeric offset or Generalize to range':category==='date/time'?'Date shift or Generalize to range':category==='UUID'?'Stable token':category==='boolean'?'a type-preserving method':'a text-compatible method';return `${item.field} (${item.dataType||category}): ${item.isSuggested?'suggested':'selected'} ${TRANSFORM_LABELS[item.choice]??item.choice.replaceAll('_',' ')} is incompatible. Choose ${compatible}.`})
  const maskingActionHint=syntheticDataset?'PII scanning and masking are not required for synthetic data sets.':incompatibleMessages.length?'Review and change the transformations below before running masking.':!findings.length?'Run Scan Fields before completing the privacy review.':pendingReviewCount>0?'Review all fields to enable the next privacy action.':sensitive.length===0?(sourceBacked?'No fields are marked sensitive. Complete privacy review to refresh and validate this source-backed data set without masking.':'No sensitive fields remain. Open the data set to publish.'):missingTransformation?'Choose a transformation for every sensitive field to enable masking.':''
  async function refresh(){
    if(!workspaceId)return
    const data=await apiRequest<{items:Dataset[]}>(`/datasets?workspace_id=${workspaceId}&page_size=100`,role)
    const selectedDataset=data.items.find(item=>item.id===datasetId)??data.items[0]
    setDatasets(data.items)
    if(role==='QA Engineer'){
      if(!selectedDataset){setQaFields([]);setFindings([]);return}
      if(!datasetId)setDatasetId(selectedDataset.id)
      const summary=await apiRequest<{fields:QaPrivacyField[]}>(`/datasets/${selectedDataset.id}/versions/${selectedDataset.latest_version.version_number}/privacy/summary`,role)
      setQaFields(summary.fields)
      return
    }
    const pii=await apiRequest<Finding[]>(`/workspaces/${workspaceId}/privacy/findings`,role)
    const selectedDatasetId=selectedDataset?.id,selectedVersionId=selectedDataset?.latest_version.id
    const fieldOrder=new Map((selectedDataset?.latest_version.schema_scope??[]).map((field,index)=>[selectedDataset?.latest_version.selected_fields[index]||field.field,index]))
    setFindings(pii.filter(item=>item.dataset_id===selectedDatasetId&&item.version_id===selectedVersionId).sort((a,b)=>(fieldOrder.get(a.field)??Number.MAX_SAFE_INTEGER)-(fieldOrder.get(b.field)??Number.MAX_SAFE_INTEGER)))
    if(!datasetId&&data.items.length)setDatasetId(data.items[0].id)
  }
  useEffect(()=>{if(workspaceId)void refresh().catch(err=>setError(messageOf(err)))},[workspaceId,role,datasetId])
  useEffect(()=>{if(!run||!['queued','running'].includes(run.status))return;const timer=window.setInterval(()=>apiRequest<Run>(`/masking-runs/${run.id}`,role).then(setRun).then(()=>refresh()).catch(()=>undefined),1200);return()=>window.clearInterval(timer)},[run?.id,run?.status,role])
  useEffect(()=>{if(dataset){setFindings(rows=>rows.filter(item=>item.version_id===dataset.latest_version.id));setRun(null)}setScanAttempted(false);setScanNotice('');setManualClassificationMode(false);setManualClassifications({});setDetailsOpen(false)},[datasetId])
  useEffect(()=>{if(!detailsOpen)return;const closeOnEscape=(event:KeyboardEvent)=>{if(event.key==='Escape')setDetailsOpen(false)};window.addEventListener('keydown',closeOnEscape);return()=>window.removeEventListener('keydown',closeOnEscape)},[detailsOpen])
  async function action(work:()=>Promise<unknown>,message:string){setBusy(true);setError('');setNotice('');try{await work();await refresh();setNotice(message);return true}catch(err){setError(messageOf(err));return false}finally{setBusy(false)}}
  async function scan(){if(!dataset||!version||syntheticDataset)return;setScanAttempted(true);setScanNotice('');setManualClassificationMode(false);setManualClassifications({});if(!['draft','ready'].includes(version.lifecycle_state))return;const previousFindings=findings,previousTransform=transform;let failureMessage='';setScanInProgress(true);setFindings([]);setTransform({});const succeeded=await action(async()=>{try{const result=await apiRequest<{findings:Finding[]}>(`/datasets/${dataset.id}/versions/${version.version_number}/privacy/scan`,role,{method:'POST'});setFindings(result.findings)}catch(err){failureMessage=messageOf(err);throw err}},'Findings refreshed. Review each field; sensitive fields require a masking transformation, while a source-backed version with no sensitive fields can complete privacy review without masking.');if(succeeded){setNotice('');setScanNotice('Findings refreshed. Review each field; sensitive fields require a masking transformation, while a source-backed version with no sensitive fields can complete privacy review without masking.')}if(!succeeded){setFindings(previousFindings);setTransform(previousTransform);if(/ollama|classification|confidence|pii field|\(Reference:/i.test(failureMessage)){setError('');setManualClassifications(Object.fromEntries(manualOutputFields.map(field=>[field,''])));setManualClassificationMode(manualOutputFields.length>0);setScanNotice('')}}setScanInProgress(false)}
  async function saveManualClassifications(){if(!dataset||!version)return;const fields=manualOutputFields;if(!fields.length||fields.some(field=>!manualClassifications[field]))return;setManualClassificationBusy(true);const succeeded=await action(async()=>{const result=await apiRequest<{findings:Finding[]}>(`/datasets/${dataset.id}/versions/${version.version_number}/privacy/manual-classifications`,role,{method:'POST',body:JSON.stringify({fields:fields.map(field=>({field,classification:manualClassifications[field]}))})});setFindings(result.findings);setTransform(Object.fromEntries(result.findings.map(item=>[item.field,'-'])))},'Manual field classifications saved. Choose a masking transformation for each sensitive field before running masking.');if(succeeded){setManualClassificationMode(false);setScanNotice('Manual classifications saved. Choose a masking transformation for each sensitive field before running masking.')}setManualClassificationBusy(false)}
  async function review(item:Finding,classification:'sensitive'|'non_sensitive'){if(syntheticDataset)return;await action(async()=>{const saved=await apiRequest<Finding>(`/privacy/findings/${item.id}`,role,{method:'PATCH',body:JSON.stringify({classification})});setFindings(rows=>rows.map(row=>row.id===saved.id?saved:row));if(classification==='non_sensitive')setTransform(current=>({...current,[item.field]:'-'}))},`Reviewed ${item.field} as ${classification.replace('_',' ')}.`)}
  function availableTransforms(item:Finding){
    // Keep the LOV in sync with masking_rule_schema_error() on the API. A
    // privacy category (for example, "date of birth") must not override the
    // declared output type because the worker has to preserve that type.
    const type=dataTypeForField(item.field).toLowerCase()
    if(/int|serial|bigint|numeric|decimal|float|double|real/.test(type))return ['tokenization','numeric_transform','generalization']
    if(/date|time/.test(type))return ['date_shift','generalization']
    if(type.includes('uuid'))return ['tokenization']
    if(type.includes('bool'))return []
    return ['tokenization','substitution','redaction','partial_redaction']
  }
  function schemaForOutputField(field:string){
    const outputIndex=(version?.selected_fields??[]).indexOf(field)
    return (outputIndex>=0?version?.schema_scope[outputIndex]:undefined)??version?.schema_scope.find(item=>item.field===field)
  }
  function dataTypeForField(field:string){return schemaForOutputField(field)?.data_type??''}
  function sourceFieldForOutputField(field:string){
    const schema=schemaForOutputField(field)
    return schema?.source_field?`${schema.entity?`${schema.entity}.`:''}${schema.source_field}`:'—'
  }
  async function mask(){if(!dataset||!version||syntheticDataset||!canRunMasking)return;setScanAttempted(false);setMaskRequestInProgress(true);await action(async()=>{
    const rules=sensitive.map(item=>{const choice=transform[item.field]!==undefined?(transform[item.field]!=='-'?transform[item.field]:null):item.suggested_transformation;if(!choice)throw new Error(`Choose a masking transformation for ${item.field}.`);const rule:{field:string;transformation:string;replacement?:string;days?:number;offset?:number;prefix?:string;suggested_type?:string;visible_characters?:number;bucket_size?:number}={field:item.field,transformation:choice};
      if(choice==='redaction')rule.replacement='[REDACTED]';if(choice==='partial_redaction')rule.visible_characters=4;if(choice==='substitution'){rule.replacement='__AUTO__';rule.suggested_type=item.suggested_type}if(choice==='date_shift')rule.days=-37;if(choice==='numeric_transform')rule.offset=17;if(choice==='generalization')rule.bucket_size=100;if(choice==='tokenization')rule.prefix='tok_';return rule})
    const policyName=sensitive.length?dataset.name.slice(0,160)+' masking':dataset.name.slice(0,145)+' privacy review'
    const policy=await apiRequest<Policy>("/workspaces/"+workspaceId+"/masking-policies",role,{method:'POST',body:JSON.stringify({name:policyName,rules})})
    await apiRequest("/masking-policies/"+policy.id+"/approve",role,{method:'POST'})
    const queued=await apiRequest<{run_id:string;status:string}>("/datasets/"+dataset.id+"/versions/"+version.version_number+"/privacy/mask?policy_id="+policy.id,role,{method:'POST'})
    setRun({id:queued.run_id,status:'queued',evidence:{},job:null})
  },sensitive.length===0?'Source data is being refreshed and validated without masking. Existing records will be replaced when validation passes.':version.lifecycle_state==='ready'||Boolean(version.validation_evidence.masking_run_id)?'Fresh data is being extracted and masked. The existing records in this version will be replaced when validation passes.':'Masking is queued for this version.');setMaskRequestInProgress(false)}
  if(!workspaceId)return <section className="panel scenario-empty"><ShieldCheck size={24}/><h2>Select a workspace</h2><p>Privacy findings and policies are workspace-scoped.</p></section>
  if(role==='QA Engineer')return <div className="privacy-page privacy-qa-page">
    {error&&<div className="alert alert-warning" role="alert">{error}</div>}
    <section className="panel privacy-workspace">
      <div className="privacy-workspace-heading"><span className="privacy-workspace-heading-icon"><ShieldCheck size={18}/></span><div><h2>Data set privacy details</h2><p>Read-only view of field sensitivity and masking for your selected published data set. Record values are never shown here.</p></div></div>
      <div className="privacy-version-controls">
        {version?<div className="privacy-state-card"><div className="privacy-state-header"><span className="privacy-state-icon"><ShieldCheck size={16}/></span><div><strong className="privacy-qa-dataset-name">{dataset?.name}</strong><small>Published version · v{version.version_number}</small></div><span className="status-badge status-success"><i/>Published</span></div><div className="privacy-gate-metrics"><div><span>Generation mode</span><strong className={`dataset-mode-value ${generationModeClass(version.provenance,dataset?.creation_pathway)}`}>{generationModeLabel(version.provenance,dataset?.creation_pathway)}</strong></div><div><span>Records</span><strong>{version.record_count??'—'}</strong></div><div><span>Fields</span><strong>{qaFields.length}</strong></div></div></div>:<div className="privacy-selection-empty"><Database size={18}/><span><strong>No data set selected</strong><small>Select a published data set to view field classifications.</small></span></div>}
        <label className="form-field">Data set
          <small className="privacy-field-help">Choose a published data set to review its field privacy details.</small>
          <select value={datasetId} onChange={event=>setDatasetId(event.target.value)}><option value="">Choose a data set</option>{datasets.map(item=><option key={item.id} value={item.id}>{item.name} · {generationModeLabel(item.latest_version.provenance,item.creation_pathway)} · v{item.latest_version.version_number}</option>)}</select>
          {dataset&&<small className="privacy-selected-dataset-id"><span>Data set ID</span><code>{dataset.reference_code}</code></small>}
        </label>
      </div>
      <div className="privacy-readonly-note">Read-only view. Sensitive fields are identified below, with the masking outcome recorded for this published version.</div>
    </section>
    <section className="panel privacy-findings privacy-qa-findings"><div className="panel-heading"><div><h2>Field privacy details</h2><p>Fields marked “Not assessed” have no saved PII classification for this version.</p></div><div className="privacy-findings-status">{qaFields.filter(field=>field.classification==='sensitive').length} sensitive · {qaFields.filter(field=>field.is_masked).length} masked</div></div>
      <div className="table-shell"><table className="privacy-findings-table"><thead><tr><th>Field</th><th>Field type</th><th>Sensitivity</th><th>Masking status</th><th>Transformation</th></tr></thead><tbody>{qaFields.map(field=><tr key={field.field}><td data-label="Field"><span className="privacy-field-lock-name">{field.field}</span></td><td data-label="Field type">{field.data_type||'—'}</td><td data-label="Sensitivity"><span className={`pii-classification ${field.classification==='not_assessed'?'not-assessed':field.classification}`}>{field.classification==='sensitive'?'Sensitive':field.classification==='non_sensitive'?'Non-sensitive':'Not assessed'}</span></td><td data-label="Masking status"><span className={`privacy-qa-mask-status ${field.is_masked?'is-masked':'is-unmasked'}`}>{field.is_masked?'Masked':field.classification==='sensitive'?'Not masked':'Not required'}</span></td><td data-label="Transformation">{field.applied_transformation?TRANSFORM_LABELS[field.applied_transformation]??field.applied_transformation.replaceAll('_',' '):'—'}</td></tr>)}{!qaFields.length&&<tr><td colSpan={5} className="table-empty">{dataset?'No schema fields are available for this data set version.':'Choose a published data set to view its privacy details.'}</td></tr>}</tbody></table></div>
    </section>
  </div>
  if(role!=='Test Data Engineer')return <section className="panel scenario-empty"><ShieldCheck size={24}/><h2>Test Data Engineer access required</h2><p>PII findings and masking policies can be reviewed and managed by a Test Data Engineer in an active workspace.</p></section>
  return <div className="privacy-page">
    {detailsOpen&&dataset&&<DataSetDetailsDialog role={role} workspaceId={workspaceId} datasetId={dataset.id} versionNumber={dataset.latest_version.version_number} onClose={()=>setDetailsOpen(false)} onPublished={()=>{void refresh().then(()=>{setError('');setNotice('Data set published. PII findings are now read-only.')}).catch(cause=>setError(messageOf(cause)))}}/>}
    <section className="panel scenario-hero"><div><div className="eyebrow">PII DISCOVERY & MASKING</div><h2>Review sensitive fields and run masking</h2><p>Discovery uses schema names only. Source-backed output is retained after privacy review and validation; masking applies only to fields reviewed as sensitive.</p></div></section>
    {error&&<div className="alert alert-warning" role="alert">{error}</div>}{notice&&<div className="scenario-notice" role="status">{notice}</div>}
    <section className="panel privacy-workspace">
      <div className="privacy-workspace-heading">
        <span className="privacy-workspace-heading-icon"><ShieldCheck size={18}/></span>
        <div><h2>Data set version</h2><p>Validated Draft and Ready versions can be rescanned and remasked. Published versions are locked.</p></div>
      </div>
      <div className="privacy-version-controls">
        <label className="form-field">Data set
          <small className="privacy-field-help">{maskingInProgress?'Dataset selection is locked while masking is in progress.':'Select the data set for privacy field discovery'}</small>
          <span className="privacy-dataset-picker"><select value={datasetId} disabled={datasetSelectionLocked} onChange={e=>setDatasetId(e.target.value)}>
            <option value="">Choose a data set</option>
            {datasets.map(item=><option key={item.id} value={item.id}>{item.name} · {generationModeLabel(item.latest_version.provenance,item.creation_pathway)} · v{item.latest_version.version_number} · {item.latest_version.lifecycle_state}</option>)}
          </select><DatasetActivityIcons datasetId={dataset?.id}/><button type="button" className="button button-secondary privacy-dataset-view-button" aria-label="View selected data set details" title={dataset?'View selected data set details':'Select a data set to view details'} disabled={!dataset} onClick={()=>setDetailsOpen(true)}><Eye size={17}/></button></span>
          {dataset&&<small className="privacy-selected-dataset-id"><span>Data set ID</span><code>{dataset.reference_code}</code></small>}
        </label>
        {version?<div className="privacy-state-card">
          <div className="privacy-state-header"><span className="privacy-state-icon"><ShieldCheck size={16}/></span><div><small>Privacy state</small><strong className={['failed','quarantined'].includes(version.privacy_state)?'is-bad':''}>{version.privacy_state.replaceAll('_',' ')}</strong></div>{role==='Test Data Engineer'&&(readyForPublication||maskingInProgress)&&<button type="button" className="button button-primary privacy-publish-link" disabled={scanInProgress||maskRequestInProgress||maskingInProgress||!readyForPublication} onClick={()=>navigate("/datasets?dataset_id="+encodeURIComponent(dataset.id)+"&version_number="+version.version_number)} title={scanInProgress||maskRequestInProgress||maskingInProgress?'Wait for the current scan or masking job to finish.':'Open the ready data set to publish.'}><ArrowRight size={14}/>Open to publish</button>}</div>
          <div className="privacy-gate-metrics">
            <div><span>Generation mode</span><strong className={`dataset-mode-value ${generationModeClass(version.provenance,dataset?.creation_pathway)}`}>{generationModeLabel(version.provenance,dataset?.creation_pathway)}</strong></div>
            <div><span>Validation</span><strong className={version.validation_state==='passed'?'is-good':version.validation_state==='failed'?'is-bad':''}>{version.validation_state.replaceAll('_',' ')}</strong></div>
            <div><span>Lifecycle</span><strong className={['failed','quarantined'].includes(version.lifecycle_state)?'is-bad':''}>{version.lifecycle_state.replaceAll('_',' ')}</strong></div>
          </div>
        </div>:<div className="privacy-selection-empty"><Database size={18}/><span><strong>No data set selected</strong><small>Choose a data set to view its privacy gates.</small></span></div>}
      </div>
      <div className="privacy-workspace-footer">
        <span className="privacy-scan-note"><ShieldCheck size={15}/>{syntheticDataset?'PII scanning and masking are not required for synthetic data sets.':'The model assesses field names and schema metadata only; dataset record values are not sent.'}</span>
        <button className="button button-confirm privacy-scan-button" disabled={role!=='Test Data Engineer'||!version||syntheticDataset||busy||maskingInProgress||version.validation_state!=='passed'||!['draft','ready'].includes(version.lifecycle_state)} onClick={()=>void scan()}><ScanSearch size={15}/>{scanInProgress?'TIGR is scanning…':'Scan Fields'}</button>
      </div>
      {syntheticDataset&&<div className="alert alert-info" role="status">PII scanning and masking aren’t required for synthetic data sets. These records were generated by TIGR rather than extracted from a source database.</div>}
      {scanNotice&&<div className="scenario-notice privacy-scan-notice" role="status">{scanNotice}</div>}
      {findingsReadOnly&&<div className="privacy-readonly-note privacy-scan-readonly-note">This data set version is Published. Saved classifications and applied masking transformations are shown below in disabled mode.</div>}
      {version&&!syntheticDataset&&(version.lifecycle_state==='ready'||Boolean(version.validation_evidence.masking_run_id))&&<div className="privacy-version-guidance" role="status"><CircleAlert size={16}/><span><strong>Privacy review can be rerun on this version.</strong><small>{sensitive.length===0&&allFindingsReviewed&&sourceBacked?'No masking transformation is needed. Complete privacy review to refresh source data and validate it before publication.':`Scan Fields is available while the version is Draft or Ready. After you review the findings and transformations, Run masking extracts fresh data and replaces this version’s records when validation passes. Published versions require a new version.`}</small>{incompatibleMessages.map(message=><small className="privacy-version-guidance-detail" key={message}>{message}</small>)}</span></div>}
      {version&&(version.lifecycle_state==='quarantined'||scanAttempted&&version.lifecycle_state==='published')&&<div className="privacy-version-warning" role="status"><CircleAlert size={17}/><span><strong>Privacy discovery unavailable</strong><small>{retryableSourceRefreshFailure?'This version is quarantined after a source-refresh masking failure. Retry masking below to extract fresh source data and validate the output.':version.lifecycle_state==='quarantined'?'This version is quarantined and cannot be scanned. Create a fresh draft version to run privacy discovery again.':'This version is Published. Create a new version to run privacy discovery again.'}</small></span></div>}
    </section>
    <section className="panel privacy-findings">
      <div className="panel-heading">
        <div><h2>Findings queue</h2><p>{manualClassificationMode?'Automatic classification failed. Choose Sensitive or Non-sensitive for every output field below. Dataset record values are not used or displayed.':'Every output field is assessed using its name and schema metadata.'}{!manualClassificationMode&&!findingsReadOnly&&<><br/>Review the classification and confidence. Sensitive fields need a masking transformation; non-sensitive fields do not.</>}</p></div>
        <div className="privacy-findings-summary">{manualClassificationMode&&role==='Test Data Engineer'?<button type="button" className="button button-primary privacy-mask-button" disabled={manualClassificationBusy||busy||Object.values(manualClassifications).some(value=>!value)||!manualOutputFields.length} onClick={()=>void saveManualClassifications()}>{manualClassificationBusy?'Saving…':'Save classifications'}</button>:<button type="button" className="button button-confirm privacy-mask-button" disabled={role!=='Test Data Engineer'||busy||!canRunMasking} onClick={()=>void mask()}><ShieldCheck size={15}/>{syntheticDataset?'Not required':sensitive.length===0&&findings.length>0?'Complete privacy review':'Run masking'}</button>}{busy&&scanAttempted?<span className="privacy-findings-status">Refreshing findings…</span>:manualClassificationMode?<span className="privacy-findings-status">{Object.values(manualClassifications).filter(Boolean).length} of {manualOutputFields.length} fields classified</span>:<span className="privacy-findings-status">{sensitive.length} marked sensitive · {pendingReviewCount} awaiting review</span>}</div>
      </div>
      <div className="table-shell">
        <table className="privacy-findings-table">
          <thead><tr><th>Output field</th><th>Field type</th><th>Confidence</th><th>Classification</th><th>Review</th><th>{findingsReadOnly?'Applied transformation':'Masking transformation'}</th></tr></thead>
          <tbody>{manualClassificationMode&&role==='Test Data Engineer'?manualOutputFields.map((field,index)=>{
            const classification=manualClassifications[field],fieldType=version?.schema_scope[index]?.data_type||'—',sourceField=sourceFieldForOutputField(field)
            return <tr key={`manual-${field}`}><td className="privacy-source-field-cell" data-label="Output field" data-source-field={sourceField} tabIndex={0} aria-label={`Output field ${field}. Source field: ${sourceField}`}><span className="privacy-field-lock-name"><span className="privacy-output-field-name">{field}</span>{entityLockFingerprintFields.has(field)&&<span className="privacy-entity-lock-key" role="img" aria-label="Entity key" title="Entity key"><KeySquare size={15}/></span>}</span></td><td data-label="Field type">{fieldType}</td><td data-label="Confidence"><span className="privacy-manual-badge">Manual</span></td><td data-label="Classification"><div className="finding-review-options" role="group" aria-label={`Sensitivity classification for ${field}`}><button type="button" className={`finding-review-option${classification==='sensitive'?' is-selected':''}`} aria-pressed={classification==='sensitive'} disabled={role!=='Test Data Engineer'} onClick={()=>setManualClassifications(current=>({...current,[field]:'sensitive'}))}>Sensitive</button><button type="button" className={`finding-review-option${classification==='non_sensitive'?' is-selected':''}`} aria-pressed={classification==='non_sensitive'} disabled={role!=='Test Data Engineer'} onClick={()=>setManualClassifications(current=>({...current,[field]:'non_sensitive'}))}>Non-sensitive</button></div></td><td data-label="Review">TDE review</td><td data-label="Masking transformation">{classification==='sensitive'?'Choose after saving':'Not required'}</td></tr>
          }):findings.map(item=>{
            const allowed=availableTransforms(item),suggested=item.suggested_transformation,choice=item.classification==='non_sensitive'?'-':findingsReadOnly?item.applied_transformation??'-':transform[item.field]??(suggested&&allowed.includes(suggested)?suggested:'-'),fieldType=dataTypeForField(item.field)||'—',sourceField=sourceFieldForOutputField(item.field)
            return <tr key={item.id}>
              <td className="privacy-source-field-cell" data-label="Output field" data-source-field={sourceField} tabIndex={0} aria-label={`Output field ${item.field}. Source field: ${sourceField}`}><span className="privacy-field-lock-name"><span className="privacy-output-field-name">{item.field}</span>{entityLockFingerprintFields.has(item.field)&&<span className="privacy-entity-lock-key" role="img" aria-label="Entity key" title="Entity key"><KeySquare size={15}/></span>}</span></td>
              <td data-label="Field type">{fieldType}</td>
              <td data-label="Confidence">{item.suggested_type==='manual'?'Manual':`${Math.round(item.confidence*100)}%`}</td>
              <td data-label="Classification"><span className={`pii-classification ${item.classification}`}>{item.classification==='sensitive'?'Sensitive':'Not sensitive'}</span></td>
              <td data-label="Review"><div className="finding-review-options"><button type="button" className={`finding-review-option${item.reviewed_at&&item.classification==='sensitive'?' is-selected':''}`} aria-pressed={!!item.reviewed_at&&item.classification==='sensitive'} disabled={role!=='Test Data Engineer'||syntheticDataset||busy||maskingInProgress||findingsReadOnly||!!item.reviewed_at&&item.classification==='sensitive'} onClick={()=>void review(item,'sensitive')}>Sensitive</button><button type="button" className={`finding-review-option${item.reviewed_at&&item.classification==='non_sensitive'?' is-selected':''}`} aria-pressed={!!item.reviewed_at&&item.classification==='non_sensitive'} disabled={role!=='Test Data Engineer'||syntheticDataset||busy||maskingInProgress||findingsReadOnly||!!item.reviewed_at&&item.classification==='non_sensitive'} onClick={()=>void review(item,'non_sensitive')}>Not sensitive</button></div></td>
              <td data-label={findingsReadOnly?'Applied transformation':'Masking transformation'}><select className="privacy-transformation-select" aria-label={(findingsReadOnly?'Applied':'Masking')+' transformation for '+item.field} value={choice} disabled={role!=='Test Data Engineer'||syntheticDataset||findingsReadOnly||busy||maskingInProgress||item.classification==='non_sensitive'} onChange={e=>setTransform(current=>({...current,[item.field]:e.target.value}))}><option value="-">-</option>{allowed.map(value=><option key={value} value={value}>{TRANSFORM_LABELS[value]??value.replaceAll('_',' ')}</option>)}{findingsReadOnly&&item.applied_transformation&&!allowed.includes(item.applied_transformation)&&<option value={item.applied_transformation}>{TRANSFORM_LABELS[item.applied_transformation]??item.applied_transformation.replaceAll('_',' ')}</option>}</select></td>
            </tr>
          })}{!findings.length&&!manualClassificationMode&&<tr><td colSpan={6} className="table-empty">{busy&&scanAttempted?'Refreshing field assessments…':syntheticDataset?'PII discovery is not required for synthetic data sets.':'No field assessment for this version. Run discovery to classify every output field.'}</td></tr>}</tbody>
        </table>
      </div>
      {!findingsReadOnly&&<>{incompatibleMessages.length>0&&<div className="privacy-preflight-warning" role="alert"><strong>Review incompatible masking transformations</strong><ul>{incompatibleMessages.map(message=><li key={message}>{message}</li>)}</ul></div>}{maskingActionHint&&<small className={`privacy-findings-action-hint privacy-findings-action-hint-below${incompatibleMessages.length?' is-warning':''}`} role={incompatibleMessages.length?'alert':undefined}>{maskingActionHint}</small>}</>}
    </section>
    {run&&<section className="panel privacy-run"><div className="panel-heading"><div><h2>{sensitive.length===0?'Privacy review run':'Masking run'}</h2><p>Run {run.id.slice(0,8)} · {run.status}</p></div><span className={`status-badge ${run.status==='succeeded'?'status-success':run.status==='failed'?'status-warning':'status-neutral'}`}><i/>{run.status}</span></div>{run.job&&<><progress max="100" value={run.job.progress}/><p>{run.job.safe_summary}</p>{run.job.heartbeat_at&&<small className="privacy-worker-heartbeat">Worker heartbeat {new Date(run.job.heartbeat_at).toLocaleTimeString()}</small>}</>}{run.status==='failed'&&<div className="alert alert-warning">{run.evidence.failure_stage&&<p><strong>Failure reason:</strong> {String(run.evidence.failure_stage).replaceAll('_',' ')}{run.evidence.reason_code&&run.evidence.reason_code!==`${run.evidence.failure_stage}_failed`?` · ${String(run.evidence.reason_code).replaceAll('_',' ')}`:''}</p>}{!run.evidence.failure_stage&&run.evidence.reason_code==='masking_failed'&&<p>The original failure reason was not recorded for this run.</p>}{run.evidence.preserve_previous_records?'Fresh extraction or masking failed. The new records were not committed; the previous compliant records remain available. Review the job details before retrying.':sensitive.length===0?'Privacy review or source refresh failed. No new source records were retained. Correct the issue, then retry privacy review.':'Masking failed and no source-backed records were retained. Correct the issue, then retry the masking run.'}</div>}{run.status==='succeeded'&&<div className="validation-ready-note"><Check size={16}/>{sensitive.length===0?(run.evidence.source_refreshed?'Fresh source data was refreshed and validated without masking. The version is ready for publication.':'Privacy review completed without masking. The version is ready for publication.'):(run.evidence.source_refreshed?'Fresh source data replaced the previous records in this version and passed masking validation.':'Masking validation passed. The version is ready for Test Data Engineer publication.')}</div>}</section>}
  </div>
}
function messageOf(err:unknown){return err instanceof Error?err.message:'Privacy request failed'}
