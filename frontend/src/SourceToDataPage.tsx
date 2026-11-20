import { useEffect, useId, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { ArrowLeft, ArrowRight, BadgeCheck, Check, ChevronDown, CircleAlert, Code2, Database, Download, FolderTree, GitBranch, KeyRound, LoaderCircle, Plus, Save, Search, ShieldCheck, Sparkles, Table2 } from 'lucide-react'
import { apiDownload, apiRequest, type HumanRole } from './api'
import DownloadReviewDialog, { type DownloadAccess, type DownloadReview } from './DownloadReviewDialog'
import { highlightSql } from './sqlHighlight'
import SavedPlanCard from './SavedPlanCard'
import TestDataRequestPicker, { testDataRequestExpiry, testDataRequestObjective, testDataRequestRequirements } from './TestDataRequestPicker'
import type { FederatedPlanDraft } from './FederatedPlanBuilder'
import './scenario.css'

type Field = {source_field:string;target_field:string;data_type:string;required:boolean;primary_key?:boolean;json_path?:string|null;source_object?:string}
type SourceObject = {name:string;path?:string;database?:string;schema?:string;type?:string;fields:Array<{name:string;type:string;primary_key?:boolean}>}
type Source = {id:string;name:string;connector_type:string;environment?:string;structure_version:number;objects:SourceObject[];databases:Array<{name:string;objects:SourceObject[]}>}
type SourceJoin = {left_object:string;left_field:string;right_object:string;right_field:string}
type Mapping = {id:string;name:string;source_object:string;connection_id:string;version_number:number;version:string;fields:Field[];relationships:Array<{parent_field:string;child_field:string}>;source_joins:SourceJoin[]}
type Job = {id:string;status:string;progress:number;safe_summary:string|null;dataset_id:string|null;dataset_reference_code?:string|null;request_id?:string}
type DraftDetail = {data_set:{name:string;reference_code:string;description:string|null;workspace_id:string;creation_pathway:string};selected_version:{version_number:number;lifecycle_state:string;privacy_state:string;validation_state:string;publication_state:string;expiry_at:string|null;generation_settings:Record<string,string|number>;schema_scope:Array<unknown>;selected_fields:string[];provenance:Record<string,unknown>;record_count:number|null;validation_evidence:Record<string,unknown>}}
type QueryOutputField = {name:string;data_type:string;source_object?:string|null;source_field?:string|null;primary_key?:boolean;output_field:string}
type Filter = {id:number;field:string;operator:string;value:string}
type SourcePlan = {id:string;workspace_id:string;name:string;state:'draft'|'saved';plan_data:Record<string,unknown>;created_at:string;updated_at:string}
type DraftSummary = {id:string;reference_code:string;name:string;description:string|null;created_at?:string;latest_version:{lifecycle_state:string;publication_state?:string;schema_scope:Array<unknown>;selected_fields:string[];provenance:Record<string,string>;record_count:number|null;generation_settings:Record<string,string|number>;expiry_at:string|null}}
type SourcePlanData = {kind?:string;method?:'mapping'|'query';step?:number;draftDatasetId?:string;generationJobId?:string;editBaseDatasetId?:string;editBaseVersionNumber?:number;sourceId?:string;objectName?:string;objectNames?:string[];mappingId?:string;mappingName?:string;federatedConfigId?:string;fields?:Field[];selected?:string[];relationships?:Array<{parent_field:string;child_field:string}>;sourceJoins?:SourceJoin[];federatedPlan?:FederatedPlanDraft;datasetName?:string;testDataRequirements?:string;testDataRequestId?:string;targetUse?:string;volume?:string;format?:'json'|'csv'|'';expiry?:string;filters?:Filter[];queryText?:string;queryId?:string;queryOutputFields?:QueryOutputField[];queryHasRows?:boolean;querySyntaxValid?:boolean|null;querySyntaxMessage?:string}
const MAPPING_STEPS=['Select Source','Map fields','Generation settings','Review & Generate']
const QUERY_STEPS=['Select Source','Execute Query','Generation settings','Review & Generate']

export default function SourceToDataPage({role,workspaceId,piiMaskingEnabled,workflow='source'}:{role:HumanRole;workspaceId:string;piiMaskingEnabled:boolean;workflow?:'source'|'sql'}) {
  const navigate=useNavigate()
  const location=useLocation()
  const [method,setMethod]=useState<'mapping'|'query'>(workflow==='sql'?'query':'mapping')
  const [step,setStep]=useState(0),[sources,setSources]=useState<Source[]>([]),[mappings,setMappings]=useState<Mapping[]>([])
  const [sourceId,setSourceId]=useState(''),[objectName,setObjectName]=useState(''),[objectNames,setObjectNames]=useState<string[]>([]),[mappingId,setMappingId]=useState(''),[mappingName,setMappingName]=useState('')
  const [federatedPlan,setFederatedPlan]=useState<FederatedPlanDraft>({sources:[],joins:[]})
  const [federatedConfigId,setFederatedConfigId]=useState('')
  const [sourcePlanId,setSourcePlanId]=useState(''),[savedSourcePlans,setSavedSourcePlans]=useState<SourcePlan[]>([])
  const [quarantiningSourcePlanId,setQuarantiningSourcePlanId]=useState('')
  const [datasetReferences,setDatasetReferences]=useState<Record<string,string>>({})
  const [datasetReferencesByName,setDatasetReferencesByName]=useState<Record<string,string>>({})
  const [excludedDatasetIds,setExcludedDatasetIds]=useState<Set<string>>(new Set())
  const [draftDatasetId,setDraftDatasetId]=useState(''),[generationJobId,setGenerationJobId]=useState('')
  const [editBaseDatasetId,setEditBaseDatasetId]=useState(''),[editBaseVersionNumber,setEditBaseVersionNumber]=useState<number|null>(null)
  const [draftGenerationStatus,setDraftGenerationStatus]=useState<'checking'|'empty'|'generated'|'unavailable'>('empty')
  const [draftCheckRetry,setDraftCheckRetry]=useState(0)
  const [fields,setFields]=useState<Field[]>([]),[selected,setSelected]=useState<string[]>([]),[relationships,setRelationships]=useState<Array<{parent_field:string;child_field:string}>>([]),[sourceJoins,setSourceJoins]=useState<SourceJoin[]>([])
  const [datasetName,setDatasetName]=useState(''),[targetUse,setTargetUse]=useState(''),[volume,setVolume]=useState(''),[format,setFormat]=useState<'json'|'csv'|''>(''),[expiry,setExpiry]=useState('')
  const [testDataRequirements,setTestDataRequirements]=useState(''),[requirementsOpen,setRequirementsOpen]=useState(false)
  const [testDataRequestId,setTestDataRequestId]=useState('')
  const [filters,setFilters]=useState<Filter[]>([])
  const [queryText,setQueryText]=useState(''),[queryId,setQueryId]=useState(''),[queryOutputFields,setQueryOutputFields]=useState<QueryOutputField[]>([]),[queryHasRows,setQueryHasRows]=useState(false)
  const [querySyntaxValid,setQuerySyntaxValid]=useState<boolean|null>(null),[querySyntaxMessage,setQuerySyntaxMessage]=useState('')
  const queryEditorRef=useRef<HTMLTextAreaElement|null>(null),queryHighlightRef=useRef<HTMLPreElement|null>(null)
  const draftPlanCreations=useRef(new Map<string,Promise<SourcePlan>>())
  const [confirm,setConfirm]=useState(false),[job,setJob]=useState<Job|null>(null),[downloadReady,setDownloadReady]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('')
  const [downloadBusy,setDownloadBusy]=useState(false),[downloadReview,setDownloadReview]=useState<DownloadReview|null>(null)
  const source=sources.find(item=>item.id===sourceId)
  const currentMapping=mappings.find(item=>item.id===mappingId)
  const objectBranches=groupObjects(source?.objects??[])
  const selectedFields=fields.filter(field=>selected.includes(field.target_field))
  const queryMode=workflow==='sql'
  const steps=queryMode?QUERY_STEPS:MAPPING_STEPS
  const sourceSchemaFieldOptions=(source?.objects??[]).flatMap(object=>object.fields.map(field=>({objectName:object.name,field})))
  const outputFields=federatedPlan.sources.length?federatedPlan.sources.flatMap(source=>source.fields.map(sourceField=>({source_field:sourceField,target_field:`${source.alias}__${sourceField}`,data_type:federatedFieldType(source,sourceField),primary_key:false,source_object:federatedSourcePath(source)}))):queryMode?queryOutputFields.map(field=>({source_field:field.source_field??field.name,target_field:field.output_field,data_type:field.data_type,primary_key:field.primary_key??false,source_object:field.source_object??null})):selectedFields
  const objectLeaf=(name:string)=>name.split('.').at(-1)?.replace(/^"|"$/g,'')
  function querySchemaFieldValue(queryField:QueryOutputField){
    const matches=sourceSchemaFieldOptions.map((option,index)=>({option,index})).filter(({option})=>option.field.name===queryField.source_field&&(queryField.source_object?objectLeaf(option.objectName)===objectLeaf(queryField.source_object):true))
    return matches.length===1?String(matches[0].index):''
  }
  function querySchemaFieldLabel(queryField:QueryOutputField){
    const value=querySchemaFieldValue(queryField)
    const selectedField=value===''?undefined:sourceSchemaFieldOptions[Number(value)]
    return selectedField?`${selectedField.objectName}.${selectedField.field.name}`:'Not mapped'
  }
  function updateQuerySchemaField(fieldName:string,value:string){
    const selectedField=value===''?null:sourceSchemaFieldOptions[Number(value)]
    setQueryOutputFields(rows=>rows.map(field=>field.name===fieldName?{...field,source_object:selectedField?.objectName??null,source_field:selectedField?.field.name??null,primary_key:selectedField?.field.primary_key??false}:field))
  }
  function isPrimaryKey(field:{primary_key?:boolean;source_field?:string|null;source_object?:string|null}){
    if(field.primary_key===true)return true
    if(!field.source_field||!field.source_object)return false
    const matchingObjects=source?.objects.filter(item=>item.name===field.source_object||objectLeaf(item.name)===objectLeaf(field.source_object!))??[]
    return matchingObjects.length===1&&matchingObjects[0].fields.some(sourceField=>sourceField.name===field.source_field&&sourceField.primary_key===true)
  }
  function federatedSourcePath(selectedSource:FederatedPlanDraft['sources'][number]){
    const connection=sources.find(item=>item.id===selectedSource.connection_id)
    return `${connection?.name??selectedSource.alias}/${selectedSource.database}/${selectedSource.schema}/${selectedSource.object_name}`
  }
  function federatedFieldType(selectedSource:FederatedPlanDraft['sources'][number],fieldName:string){
    const connection=sources.find(item=>item.id===selectedSource.connection_id)
    const object=connection?.databases.find(item=>item.name===selectedSource.database)?.objects.find(item=>item.name===selectedSource.object_name&&item.schema===selectedSource.schema)
    return object?.fields.find(field=>field.name===fieldName)?.type??'unknown'
  }

  async function loadSources(){
    const loadDataSets=async()=>{
      const items:DraftSummary[]=[]
      let page=1
      while(true){
        const result=await apiRequest<{items:DraftSummary[];has_more:boolean}>(`/datasets?workspace_id=${encodeURIComponent(workspaceId)}&creation_pathway=source_to_data&page=${page}&page_size=100`,role)
        items.push(...result.items)
        if(!result.has_more)return items
        page+=1
      }
    }
    const [available, saved, listedPlans, datasets] = await Promise.all([
      apiRequest<Source[]>(`/workspaces/${workspaceId}/source-data-sources`,role),
      apiRequest<Mapping[]>(`/workspaces/${workspaceId}/source-mappings`,role),
      apiRequest<SourcePlan[]>(`/workspaces/${workspaceId}/source-plans`,role),
      loadDataSets(),
    ])
    const newestByName=new Map<string,DraftSummary>()
    for(const item of [...datasets].sort((a,b)=>new Date(b.created_at??0).getTime()-new Date(a.created_at??0).getTime())){
      const name=item.name.trim().toLocaleLowerCase()
      if(name&&!newestByName.has(name))newestByName.set(name,item)
    }
    const shouldExclude=(item:DraftSummary)=>item.latest_version.lifecycle_state==='published'||item.latest_version.lifecycle_state==='quarantined'
    setDatasetReferences(Object.fromEntries(datasets.map(item=>[item.id,item.reference_code])))
    setDatasetReferencesByName(Object.fromEntries([...newestByName].map(([name,item])=>[name,item.reference_code])))
    const linkedPlans=await Promise.all(listedPlans.map(async plan=>{
      const data=plan.plan_data as SourcePlanData
      if(data.draftDatasetId)return plan
      const matchingDatasets=datasets.filter(item=>{
        const provenance=item.latest_version.provenance??{}
        const sameName=item.name.trim().toLocaleLowerCase()===(data.datasetName?.trim()||plan.name).toLocaleLowerCase()
        return sameName&&((data.queryId&&provenance.source_query_id===data.queryId)||(data.mappingId&&provenance.source_mapping_id===data.mappingId))
      }).sort((a,b)=>new Date(b.created_at??0).getTime()-new Date(a.created_at??0).getTime())
      const match=matchingDatasets[0]
      if(!match)return plan
      const repairedData:SourcePlanData={...data,draftDatasetId:match.id,generationJobId:match.latest_version.provenance?.job_id??data.generationJobId,step:3}
      const repairedPlan={...plan,plan_data:repairedData}
      try{
        return await apiRequest<SourcePlan>(`/source-plans/${plan.id}`,role,{method:'PATCH',body:JSON.stringify({name:plan.name,state:plan.state,plan_data:repairedData})})
      }catch{
        return repairedPlan
      }
    }))
    const linkedIds=new Set(linkedPlans.map(plan=>String((plan.plan_data as SourcePlanData)?.draftDatasetId??'')))
    const unlinked=datasets.filter(item=>item.latest_version.lifecycle_state==='draft'&&item.latest_version.provenance.origin==='not_set'&&!item.latest_version.schema_scope.length&&!item.latest_version.selected_fields.length&&item.latest_version.record_count===null&&!linkedIds.has(item.id))
    const recovered=await Promise.all(unlinked.map(item=>{
      const pending=draftPlanCreations.current.get(item.id)
      if(pending)return pending
      const creation=apiRequest<SourcePlan>(`/workspaces/${workspaceId}/source-plans`,role,{method:'POST',body:JSON.stringify({name:item.name,state:'draft',plan_data:{method:'mapping',step:0,draftDatasetId:item.id,datasetName:item.name,targetUse:item.description??String(item.latest_version.generation_settings.target_use??''),volume:item.latest_version.generation_settings.maximum_records==null?'':String(item.latest_version.generation_settings.maximum_records),format:item.latest_version.generation_settings.output_format==='csv'?'csv':item.latest_version.generation_settings.output_format==='json'?'json':'',expiry:item.latest_version.expiry_at?toLocalDateTimeInput(item.latest_version.expiry_at):''}})})
      draftPlanCreations.current.set(item.id,creation)
      return creation.finally(()=>draftPlanCreations.current.delete(item.id))
    }))
    const plans=[...recovered,...linkedPlans]
    const excludedIds=new Set(datasets.filter(shouldExclude).map(item=>item.id))
    setExcludedDatasetIds(excludedIds)
    setSources(available);setMappings(saved)
    setSavedSourcePlans(rows=>[...plans,...rows.filter(item=>!plans.some(plan=>plan.id===item.id))])
    const routeParams=new URLSearchParams(location.search)
    const routeDraftId=routeParams.get('draft_id')
    const editDatasetId=routeParams.get('edit_dataset_id')
    const routePlan=plans.find(plan=>{
      const data=plan.plan_data as SourcePlanData
      return data?.draftDatasetId===(editDatasetId||routeDraftId)&&Boolean(data.method==='query')===queryMode
    })
    if(routePlan)resumeSourcePlan(routePlan,Boolean(editDatasetId))
    else if(editDatasetId)setError('The saved source plan for this data set could not be found. Reopen the original source plan before creating a new version.')
  }
  useEffect(()=>{if(workspaceId&&role==='Test Data Engineer')void loadSources().catch(err=>setError(message(err)))},[workspaceId,role,location.search])
  useEffect(()=>{setRequirementsOpen(false)},[step])
  useEffect(()=>{
    let active=true
    setError('')
    const routeParams=new URLSearchParams(location.search)
    const draftId=routeParams.get('draft_id')
    const editDatasetId=routeParams.get('edit_dataset_id')
    if(!draftId){if(!editDatasetId){setDatasetName('');setTargetUse('');setVolume('');setFormat('');setExpiry('')}return()=>{active=false}}
    apiRequest<DraftDetail>(`/datasets/${encodeURIComponent(draftId)}`,role)
      .then(async draft=>{
        if(!active)return
        if(draft.data_set.workspace_id!==workspaceId||draft.data_set.creation_pathway!=='source_to_data'||draft.selected_version.lifecycle_state!=='draft')throw Error('The selected Source to Data draft is unavailable in this workspace')
        const generated=draftContainsGenerationData(draft.selected_version)
        setDraftGenerationStatus(generated?'generated':'empty')
        // A direct draft URL can be opened without its saved source-plan row.
        // Put generated drafts on the review step so the user can open the data set.
        if(generated)setStep(3)
        const settings=draft.selected_version?.generation_settings??{}
        const targetUseValue=draft.data_set.description??String(settings.target_use??'')
        const volumeValue=settings.maximum_records==null?'':String(settings.maximum_records)
        const formatValue=settings.output_format==='csv'?'csv':settings.output_format==='json'?'json':''
        const expiryValue=draft.selected_version.expiry_at?toLocalDateTimeInput(draft.selected_version.expiry_at):''
        setDraftDatasetId(draftId)
        setDatasetName(draft.data_set.name)
        setTargetUse(targetUseValue)
        setVolume(volumeValue)
        setFormat(formatValue)
        setExpiry(expiryValue)
      })
      .catch(err=>{if(active)setError(message(err))})
    return()=>{active=false}
  },[location.search,role,workspaceId])
  useEffect(()=>{
    const params=new URLSearchParams(location.search)
    const baseDatasetId=params.get('edit_dataset_id')
    const baseVersionNumber=Number(params.get('base_version_number'))
    if(!baseDatasetId||!Number.isInteger(baseVersionNumber)||baseVersionNumber<1){setEditBaseDatasetId('');setEditBaseVersionNumber(null);return}
    let active=true
    setEditBaseDatasetId(baseDatasetId);setEditBaseVersionNumber(baseVersionNumber)
    setDraftDatasetId('');setGenerationJobId('');setJob(null);setDraftGenerationStatus('empty');setStep(0)
    apiRequest<DraftDetail>(`/datasets/${encodeURIComponent(baseDatasetId)}?version_number=${baseVersionNumber}`,role)
      .then(detail=>{
        if(!active)return
        if(detail.data_set.workspace_id!==workspaceId||detail.data_set.creation_pathway!=='source_to_data')throw Error('The selected Source to Data version is unavailable in this workspace')
        setDatasetName(detail.data_set.name)
        setTargetUse(detail.data_set.description??String(detail.selected_version.generation_settings.target_use??''))
        setVolume(detail.selected_version.generation_settings.maximum_records==null?'':String(detail.selected_version.generation_settings.maximum_records))
        setFormat(detail.selected_version.generation_settings.output_format==='csv'?'csv':detail.selected_version.generation_settings.output_format==='json'?'json':'')
        setExpiry(detail.selected_version.expiry_at?toLocalDateTimeInput(detail.selected_version.expiry_at):'')
      })
      .catch(err=>{if(active)setError(message(err))})
    return()=>{active=false}
  },[location.search,role,workspaceId])
  useEffect(()=>{
    if(!draftDatasetId){setDraftGenerationStatus('empty');return}
    let active=true
    const controller=new AbortController()
    const timeout=window.setTimeout(()=>controller.abort(),12000)
    setDraftGenerationStatus('checking')
    apiRequest<{has_generation_data:boolean}>(`/datasets/${encodeURIComponent(draftDatasetId)}/generation-state`,role,{signal:controller.signal})
      .then(result=>{if(active)setDraftGenerationStatus(result.has_generation_data?'generated':'empty')})
      .catch(()=>{if(active)setDraftGenerationStatus('unavailable')})
      .finally(()=>window.clearTimeout(timeout))
    return()=>{active=false;window.clearTimeout(timeout);controller.abort()}
  },[draftDatasetId,role,draftCheckRetry])
  useEffect(()=>{
    if(!draftDatasetId||generationJobId||!workspaceId)return
    let active=true
    apiRequest<{items:Array<Job&{job_type?:string}>}>(`/jobs?workspace_id=${encodeURIComponent(workspaceId)}&page=1&page_size=100`,role)
      .then(async result=>{
        if(!active)return
        const previousJob=result.items.find(item=>item.dataset_id===draftDatasetId&&['source_data_generation','source_query_generation'].includes(item.job_type??''))
        if(!previousJob)return
        setGenerationJobId(previousJob.id);setJob(previousJob)
        try{await saveSourcePlan('draft',{...sourcePlanData(),draftDatasetId,generationJobId:previousJob.id})}catch{/* Keep the recovered job visible even if plan persistence is temporarily unavailable. */}
      }).catch(()=>undefined)
    return()=>{active=false}
  },[draftDatasetId,generationJobId,workspaceId,role])
  useEffect(()=>{if(!job||!['queued','running'].includes(job.status))return;const timer=window.setInterval(()=>apiRequest<Job>(`/source-data/jobs/${job.id}`,role).then(setJob).catch(()=>undefined),1200);return()=>window.clearInterval(timer)},[job?.id,job?.status,role])
  useEffect(()=>{if(!['succeeded','succeeded_with_warnings'].includes(job?.status??'')||!job?.dataset_id){setDownloadReady(false);return}let active=true;apiRequest<DraftDetail>(`/datasets/${encodeURIComponent(job.dataset_id)}`,role).then(detail=>{if(!active)return;const version=detail.selected_version;setDownloadReady(version.lifecycle_state==='published'&&version.publication_state==='published'&&version.validation_state==='passed'&&version.privacy_state==='compliant'&&(!version.expiry_at||new Date(version.expiry_at).getTime()>Date.now()))}).catch(()=>{if(active)setDownloadReady(false)});return()=>{active=false}},[job?.dataset_id,job?.status,role])

  function sourcePlanData():SourcePlanData{return{method,step,draftDatasetId:draftDatasetId||undefined,generationJobId:generationJobId||undefined,editBaseDatasetId:editBaseDatasetId||undefined,editBaseVersionNumber:editBaseVersionNumber??undefined,sourceId,objectName,objectNames,mappingId,mappingName,federatedConfigId:federatedConfigId||undefined,fields,selected,relationships,sourceJoins,federatedPlan:federatedPlan.sources.length?federatedPlan:undefined,datasetName,testDataRequirements,testDataRequestId:testDataRequestId||undefined,targetUse,volume,format,expiry,filters,queryText,queryId,queryOutputFields,queryHasRows,querySyntaxValid,querySyntaxMessage}}
  async function saveSourcePlan(state:'draft'|'saved'='draft',planData:SourcePlanData=sourcePlanData()){
    if(!datasetName.trim())throw Error('Enter a data set name before saving')
    if(planData.federatedPlan?.sources.length){
      const federated=planData.federatedPlan
      await apiRequest(`/workspaces/${workspaceId}/federated-plans/validate`,role,{method:'POST',body:JSON.stringify({sources:federated.sources,joins:federated.joins,row_limit:1000})})
    }
    const body={name:datasetName.trim(),state,plan_data:planData}
    const saved=sourcePlanId
      ?await apiRequest<SourcePlan>(`/source-plans/${sourcePlanId}`,role,{method:'PATCH',body:JSON.stringify(body)})
      :await apiRequest<SourcePlan>(`/workspaces/${workspaceId}/source-plans`,role,{method:'POST',body:JSON.stringify(body)})
    setSourcePlanId(saved.id);setSavedSourcePlans(rows=>[saved,...rows.filter(item=>item.id!==saved.id)]);return saved
  }
  async function savePlanDraft(){setBusy(true);setError('');setNotice('');try{await saveSourcePlan();setNotice(queryMode?'SQL plan saved. Reopen it from Saved SQL plans.':'Source plan saved. Reopen it from Saved source plans.')}catch(err){setError(message(err))}finally{setBusy(false)}}
  async function quarantineSourceDraft(saved:SourcePlan,data:SourcePlanData){
    const hasDataset=Boolean(data.draftDatasetId)
    if(!window.confirm(hasDataset?'Quarantine this complete source data set draft? It will remain in Data Sets with Quarantined status, and its saved plan will be removed.':'Delete this saved source plan draft? It has no data set version to quarantine.'))return
    setQuarantiningSourcePlanId(saved.id);setError('');setNotice('')
    try{
      await apiRequest(`/source-plans/${saved.id}/quarantine`,role,{method:'POST'})
      setSavedSourcePlans(rows=>rows.filter(item=>item.id!==saved.id))
      if(data.draftDatasetId)setExcludedDatasetIds(current=>new Set([...current,data.draftDatasetId!]))
      if(sourcePlanId===saved.id)startNewPlan()
      setNotice(hasDataset?'Source data set draft moved to Quarantined.':'Saved source plan draft deleted.')
    }catch(err){setError(message(err))}finally{setQuarantiningSourcePlanId('')}
  }
  function resumeSourcePlan(saved:SourcePlan,editingVersion=false){
    const data=saved.plan_data as SourcePlanData
    const isEditingContext=editingVersion||Boolean(data.editBaseDatasetId)
    const routeParams=new URLSearchParams(location.search)
    const baseId=editingVersion?routeParams.get('edit_dataset_id')??'':data.editBaseDatasetId??''
    const baseVersion=editingVersion?Number(routeParams.get('base_version_number')):data.editBaseVersionNumber
    setEditBaseDatasetId(baseId);setEditBaseVersionNumber(Number.isInteger(baseVersion)&&Number(baseVersion)>0?Number(baseVersion):null)
    setDraftGenerationStatus(editingVersion?'empty':data.draftDatasetId?'checking':'empty')
    setSourcePlanId(editingVersion?'':saved.id);setMethod(data.method==='query'?'query':'mapping');setStep(editingVersion?0:Math.max(0,Math.min(3,Number(data.step??0))))
    setDraftDatasetId(editingVersion?'':data.draftDatasetId??'');setGenerationJobId(editingVersion?'':data.generationJobId??'')
    setSourceId(data.sourceId??'');setObjectName(data.objectName??'');setObjectNames(data.objectNames??[]);setMappingId(data.mappingId??'');setMappingName(data.mappingName??'')
    setFederatedConfigId(data.federatedConfigId??'')
    setFederatedPlan(data.federatedPlan?{sources:data.federatedPlan.sources??[],joins:data.federatedPlan.joins??[]}:{sources:[],joins:[]})
    setFields(data.fields??[]);setSelected(data.selected??[]);setRelationships(data.relationships??[]);setSourceJoins(data.sourceJoins??[])
    setDatasetName(data.datasetName??saved.name);setTestDataRequirements(data.testDataRequirements??'');setTestDataRequestId(data.testDataRequestId??'');setRequirementsOpen(false);setTargetUse(data.targetUse??'');setVolume(data.volume??'');setFormat(data.format??'');setExpiry(data.expiry??'');setFilters(data.filters??[])
    setQueryText(data.queryText??'');setQueryId(data.queryId??'');setQueryOutputFields(data.queryOutputFields??[]);setQueryHasRows(data.queryHasRows??false);setQuerySyntaxValid(data.querySyntaxValid??null);setQuerySyntaxMessage(data.querySyntaxMessage??'')
    setConfirm(false);setJob(!editingVersion&&data.generationJobId?{id:data.generationJobId,status:'queued',progress:0,safe_summary:'Restoring saved job status',dataset_id:data.draftDatasetId??null}:null);setError('');setNotice(editingVersion?'Review the source configuration from Step 1. Generation starts only after you review and confirm Step 4.':isEditingContext?'Saved new-version plan reopened.':'Saved source plan reopened.')
  }
  function startNewPlan(){setSourcePlanId('');setDraftDatasetId('');setGenerationJobId('');setEditBaseDatasetId('');setEditBaseVersionNumber(null);setStep(0);setMethod(queryMode?'query':'mapping');setSourceId('');setObjectName('');setObjectNames([]);setMappingId('');setMappingName('');setFederatedConfigId('');setFederatedPlan({sources:[],joins:[]});setFields([]);setSelected([]);setRelationships([]);setSourceJoins([]);setDatasetName('');setTestDataRequirements('');setTestDataRequestId('');setRequirementsOpen(false);setTargetUse('');setVolume('');setFormat('');setExpiry('');setFilters([]);setQueryText('');setQueryId('');setQueryOutputFields([]);setQueryHasRows(false);setQuerySyntaxValid(null);setQuerySyntaxMessage('');setConfirm(false);setJob(null);setDownloadReady(false);setError('');setNotice('');navigate(queryMode?'/sql-to-data':'/source-to-data')}

  function openDataSet(datasetId:string){
    window.location.assign(`/datasets?dataset_id=${encodeURIComponent(datasetId)}`)
  }

  function chooseSource(id:string){setSourceId(id);setFederatedConfigId('');setFederatedPlan({sources:[],joins:[]});setObjectName('');setObjectNames([]);setMappingId('');setFields([]);setSelected([]);setRelationships([]);setSourceJoins([]);setFilters([]);setQueryId('');setQueryOutputFields([]);setQueryHasRows(false);setQuerySyntaxValid(null);setQuerySyntaxMessage('')}
  function chooseFederatedConfig(id:string){
    setFederatedConfigId(id)
    const saved=savedSourcePlans.find(item=>item.id===id&&item.plan_data.kind==='federated_config')
    const raw=saved?.plan_data.federatedPlan as FederatedPlanDraft|undefined
    if(!id||!raw){setFederatedPlan({sources:[],joins:[]});return}
    setFederatedPlan({sources:raw.sources??[],joins:raw.joins??[]});if(!sourceId)setSourceId(raw.sources?.[0]?.connection_id??'')
    setObjectName('');setObjectNames([]);setFields([]);setSelected([]);setSourceJoins([]);setFilters([])
  }
  function selectObjects(next:string[]){
    const unique=[...new Set(next)],nextRoot=unique[0]??''
    const candidates=unique.flatMap(name=>{
      const existing=fields.filter(field=>field.source_object===name)
      if(existing.length)return existing
      return (source?.objects.find(item=>item.name===name)?.fields??[]).map(field=>({source_field:field.name,target_field:field.name,data_type:field.type,required:false,primary_key:field.primary_key??false,source_object:name}))
    })
    const counts=new Map<string,number>();candidates.forEach(field=>counts.set(field.target_field,(counts.get(field.target_field)??0)+1))
    const updated=candidates.map(field=>counts.get(field.target_field)!>1?{...field,target_field:`${field.source_object}_${field.source_field}`}:field)
    setObjectNames(unique);setObjectName(nextRoot);setMappingId('');setFields(updated);setSelected(updated.map(field=>field.target_field));setRelationships([]);setFilters(rows=>rows.filter(filter=>updated.some(field=>field.target_field===filter.field)));setSourceJoins(rows=>rows.filter(join=>unique.includes(join.left_object)&&unique.includes(join.right_object)))
  }
  function chooseObject(name:string){selectObjects(objectNames.includes(name)?objectNames.filter(item=>item!==name):[...objectNames,name])}
  function chooseBranch(names:string[],checked:boolean){selectObjects(checked?[...objectNames,...names]:objectNames.filter(item=>!names.includes(item)))}
  function chooseMapping(id:string){
    setMappingId(id);const saved=mappings.find(item=>item.id===id);if(!saved)return
    const names=[...new Set(saved.fields.map(item=>item.source_object??saved.source_object))]
    const savedFields=saved.fields.map(item=>({...item,source_object:item.source_object??saved.source_object}))
    const candidates=names.flatMap(name=>{
      const discovered=sources.find(item=>item.id===saved.connection_id)?.objects.find(item=>item.name===name)?.fields??[]
      if(!discovered.length)return savedFields.filter(item=>item.source_object===name)
      const mapped=discovered.map(field=>{const savedField=savedFields.find(item=>item.source_object===name&&item.source_field===field.name);return savedField?{...savedField,primary_key:field.primary_key??savedField.primary_key}:{source_field:field.name,target_field:field.name,data_type:field.type,required:false,primary_key:field.primary_key??false,source_object:name}})
      return [...mapped,...savedFields.filter(item=>item.source_object===name&&!discovered.some(field=>field.name===item.source_field))]
    })
    const usedTargets=new Set<string>()
    const allFields=candidates.map(field=>{
      const fromSaved=savedFields.some(item=>item.source_object===field.source_object&&item.source_field===field.source_field)
      let targetField=field.target_field
      if(!fromSaved&&usedTargets.has(targetField)){const base=`${field.source_object}_${field.source_field}`;targetField=base;let ordinal=2;while(usedTargets.has(targetField))targetField=`${base}_${ordinal++}`}
      usedTargets.add(targetField)
      return targetField===field.target_field?field:{...field,target_field:targetField}
    })
    setSourceId(saved.connection_id);setObjectName(saved.source_object);setObjectNames(names);setMappingName(saved.name);setFields(allFields);setSelected([...new Set([...savedFields.map(item=>item.target_field),...allFields.filter(item=>item.required).map(item=>item.target_field)])]);setRelationships(saved.relationships);setSourceJoins(saved.source_joins??[]);setFilters([])
  }
  function editField(index:number,key:keyof Field,value:string|boolean){const previous=fields[index];setFields(rows=>rows.map((row,i)=>i===index?{...row,[key]:value}:row));if(key==='target_field'&&typeof value==='string'){setSelected(rows=>rows.map(item=>item===previous.target_field?value:item));setRelationships(rows=>rows.map(row=>({...row,parent_field:row.parent_field===previous.target_field?value:row.parent_field,child_field:row.child_field===previous.target_field?value:row.child_field})));setFilters(rows=>rows.map(filter=>filter.field===previous.target_field?{...filter,field:value}:filter))}}
  async function saveMapping(){
    if(!source||!objectName||!objectNames.length)throw Error('Select an enabled source and at least one object first')
    if(!mappingName.trim())throw Error('Enter a mapping name')
    const mappingFields=fields.filter(item=>selected.includes(item.target_field))
    const saved=await apiRequest<Mapping>(`/workspaces/${workspaceId}/source-mappings`,role,{method:'POST',body:JSON.stringify({name:mappingName,connection_id:sourceId,source_object:objectName,fields:mappingFields,relationships,source_joins:sourceJoins})})
    setMappings(rows=>[saved,...rows.filter(item=>item.name!==saved.name)]);setMappingId(saved.id);setSelected(fields.map(item=>item.target_field));setNotice(`Saved mapping version ${saved.version} with the generation draft.`)
    return saved
  }
  async function executeQuery(){
    if(!sourceId||source?.connector_type!=='postgresql')throw Error('Choose an enabled PostgreSQL source connection')
    if(!queryText.trim())throw Error('Enter a SELECT, WITH, or read-only CALL query')
    setBusy(true);setError('');setNotice('');setQueryId('');setQueryOutputFields([])
    try{
      const result=await apiRequest<{query_id:string;output_fields:Array<Omit<QueryOutputField,'output_field'>>;row_preview_available:boolean;values_included:boolean}>(`/workspaces/${workspaceId}/source-data/query/execute`,role,{method:'POST',body:JSON.stringify({connection_id:sourceId,query_text:queryText})})
      setQueryId(result.query_id);setQueryOutputFields(result.output_fields.map(field=>({...field,output_field:field.name})));setQueryHasRows(result.row_preview_available)
      setNotice(`Query executed. ${result.output_fields.length} output field${result.output_fields.length===1?'':'s'} discovered${result.row_preview_available?' · matching rows found':''}. Source values remain hidden.`)
    }catch(err){setError(message(err))}
    finally{setBusy(false)}
  }
  async function validateQuery(){
    if(!sourceId||source?.connector_type!=='postgresql')throw Error('Choose an enabled PostgreSQL source connection')
    if(!queryText.trim())throw Error('Enter a SELECT, WITH, or read-only CALL query')
    setBusy(true);setError('');setNotice('');setQuerySyntaxValid(null);setQuerySyntaxMessage('')
    try{
      const result=await apiRequest<{valid:boolean;message:string}>(`/workspaces/${workspaceId}/source-data/query/validate`,role,{method:'POST',body:JSON.stringify({connection_id:sourceId,query_text:queryText})})
      setQuerySyntaxValid(result.valid);setQuerySyntaxMessage(result.message)
    }catch(err){setQuerySyntaxValid(false);setQuerySyntaxMessage(message(err))}
    finally{setBusy(false)}
  }
  async function continueStep(){
    setBusy(true);setError('');setNotice('')
    try{
      if(step===0){
        if(!sourceId&&!federatedPlan.sources.length)throw Error('Select a source connection')
        if(datasetName.trim().length<2)throw Error('Enter a data set name')
        if(federatedPlan.sources.length){if(federatedPlan.sources.length<2)throw Error('Add at least two reviewed source objects')}
        else if(queryMode){if(source?.connector_type!=='postgresql')throw Error('Query generation requires a PostgreSQL source connection')}
        else{if(!objectNames.length)throw Error('Select at least one source object');if(!selected.length)throw Error('Select at least one field in Select Source');if(!mappingName.trim())setMappingName(objectName||objectNames[0])}
      }
      if(step===1&&queryMode){if(!queryId||!queryOutputFields.length)throw Error('Execute the query and review its output fields before continuing');const outputNames=queryOutputFields.map(field=>field.output_field.trim());if(outputNames.some(name=>!name))throw Error('Every output field needs a name');if(new Set(outputNames).size!==outputNames.length)throw Error('Output field names must be unique')}
      if(step===1&&!queryMode&&!federatedPlan.sources.length){
        const included=fields.filter(item=>selected.includes(item.target_field))
        if(!included.length)throw Error('Select at least one field to include')
        if(objectNames.some(name=>!included.some(item=>item.source_object===name)))throw Error('Select at least one output field from each selected object')
        if(included.some(item=>!item.target_field.trim()))throw Error('Every selected field needs an output name')
        if(new Set(included.map(item=>item.target_field)).size!==included.length)throw Error('Output field names must be unique')
        const connected=new Set([objectName]);while(true){const next=new Set(connected);sourceJoins.forEach(join=>{if(connected.has(join.left_object))next.add(join.right_object);if(connected.has(join.right_object))next.add(join.left_object)});if(next.size===connected.size)break;connected.clear();next.forEach(name=>connected.add(name))}if(objectNames.some(name=>!connected.has(name)))throw Error('Add join keys connecting every selected object')
      }
      
      if(step===2){if(federatedPlan.sources.length<2){if(queryMode){if(!queryId||!queryOutputFields.length)throw Error('Execute the query before reviewing generation')}else{if(!selected.length)throw Error('Select at least one output field');if(filters.some(filter=>!filter.field||(filter.operator!=='is_null'&&!filter.value.trim())))throw Error('Complete each filter or remove it before continuing')}}if(!datasetName.trim())throw Error('Enter a data set name');if(!targetUse.trim())throw Error('Enter a target use');if(!volume||Number(volume)<1||Number(volume)>10000)throw Error('Record Count must be between 1 and 10,000');if(!format)throw Error('Choose an output format')}
      await saveSourcePlan('draft',{...sourcePlanData(),step:Math.min(3,step+1),mappingName:mappingName.trim()||objectName||objectNames[0]||''});setNotice(queryMode?'Draft saved. Your progress is available in Saved SQL plans.':'Draft saved. Your progress is available in Saved source plans.')
      setStep(value=>Math.min(3,value+1))
    }catch(err){setError(message(err))}finally{setBusy(false)}
  }
  async function startGeneration(event:React.FormEvent){
    event.preventDefault()
    if(draftGenerationStatus==='generated'){setError('This draft already has generation data. Create a fresh draft to generate again.');return}
    if(draftGenerationStatus!=='empty'){setError(draftGenerationStatus==='checking'?'Checking this draft before generation. Please wait.':'Could not verify this draft. <button type="button" className="text-button" onClick={()=>setDraftCheckRetry(value=>value+1)}>Retry check</button>');return}
    setBusy(true);setError('');setNotice('')
    try{
      const predicates=filters.filter(filter=>filter.field).map(filter=>({field:filter.field,operator:filter.operator,value:filter.operator==='is_null'?null:parseValue(filter.value)}))
      if(federatedPlan.sources.length){
        const saved=await saveSourcePlan('saved',sourcePlanData())
        const requestBody={source_plan_id:saved.id,dataset_name:datasetName,target_use:targetUse||'testing',volume:Number(volume),format:format||'json',expiry_at:expiry?new Date(expiry).toISOString():null,test_data_request_id:testDataRequestId||undefined,confirm_source_extraction:confirm}
        const draftId=editBaseDatasetId?undefined:(draftDatasetId||undefined)
        const generationRequest=editBaseDatasetId&&editBaseVersionNumber?{...requestBody,base_dataset_id:editBaseDatasetId,base_version_number:editBaseVersionNumber}:draftId?{...requestBody,draft_dataset_id:draftId}:requestBody
        const queued=await apiRequest<{job_id:string;dataset_id:string;dataset_reference_code:string;status:string;request_id:string}>(`/workspaces/${workspaceId}/federated-plans/execute`,role,{method:'POST',body:JSON.stringify(generationRequest)})
        setDraftDatasetId(queued.dataset_id);setGenerationJobId(queued.job_id)
        let planLinked=true
        try{await saveSourcePlan('saved',{...sourcePlanData(),draftDatasetId:queued.dataset_id,generationJobId:queued.job_id})}catch{planLinked=false}
        setJob({id:queued.job_id,status:queued.status,progress:0,safe_summary:'Federated generation queued',dataset_id:queued.dataset_id,dataset_reference_code:queued.dataset_reference_code,request_id:queued.request_id})
        setNotice(`Federated generation queued across ${federatedPlan.sources.length} sources. Job ID: ${queued.job_id}. Data set ID: ${queued.dataset_reference_code}.${planLinked?'':' The source plan could not be linked automatically.'}`)
        return
      }
      const finalMapping=queryMode?null:await saveMapping()
      if(sourcePlanId)await saveSourcePlan('draft',{...sourcePlanData(),mappingId:finalMapping?.id??mappingId})
      const requestBody=queryMode?{query_id:queryId,output_fields:queryOutputFields.map(field=>({source_field:field.name,output_field:field.output_field.trim(),source_object:field.source_object??null,source_column:field.source_field??null})),dataset_name:datasetName,target_use:targetUse,volume:Number(volume),format,expiry_at:expiry?new Date(expiry).toISOString():null,test_data_request_id:testDataRequestId||undefined,confirm_source_extraction:confirm}:{mapping_id:finalMapping!.id,dataset_name:datasetName,selected_fields:selected,target_use:targetUse,volume:Number(volume),format,expiry_at:expiry?new Date(expiry).toISOString():null,predicates,test_data_request_id:testDataRequestId||undefined,confirm_source_extraction:confirm}
      const endpoint=queryMode?'query/generate':'generate'
      const draftId=editBaseDatasetId?undefined:(draftDatasetId||undefined)
      const generationRequest=editBaseDatasetId&&editBaseVersionNumber
        ?{...requestBody,base_dataset_id:editBaseDatasetId,base_version_number:editBaseVersionNumber}
        :draftId?{...requestBody,draft_dataset_id:draftId}:requestBody
      const queued=await apiRequest<{job_id:string;dataset_id:string;dataset_reference_code:string;status:string;mapping_version?:string;request_id:string}>(`/workspaces/${workspaceId}/source-data/${endpoint}`,role,{method:'POST',body:JSON.stringify(generationRequest)})
      const generationSource=queryMode?`Query ${queryId}`:queued.mapping_version??'Source mapping'
      setDraftDatasetId(queued.dataset_id);setGenerationJobId(queued.job_id)
      let planLinked=true
      try{await saveSourcePlan('draft',{...sourcePlanData(),draftDatasetId:queued.dataset_id,generationJobId:queued.job_id})}catch{planLinked=false}
      setJob({id:queued.job_id,status:queued.status,progress:0,safe_summary:'Generation queued',dataset_id:queued.dataset_id,dataset_reference_code:queued.dataset_reference_code,request_id:queued.request_id});setNotice(`Generation queued. Job ID: ${queued.job_id}. Data set ID: ${queued.dataset_reference_code}. Source: ${generationSource}. Request: ${queued.request_id}.${planLinked?'':' The source plan could not be linked automatically.'}`)
    }catch(err){const detail=message(err);if(detail.includes('This draft already has generation data'))setDraftGenerationStatus('generated');setError(detail)}finally{setBusy(false)}
  }
  async function resetFailedGenerationDraft(){
    setBusy(true);setError('');setNotice('')
    try{
      setDraftDatasetId('');setGenerationJobId('');setJob(null);setDraftGenerationStatus('empty')
      await saveSourcePlan('draft',{...sourcePlanData(),draftDatasetId:undefined,generationJobId:undefined})
      setNotice('The failed attempt was cleared. Generate data to start with a fresh draft.')
    }catch(err){setError(message(err))}finally{setBusy(false)}
  }
  async function download(){
    if(!job?.dataset_id)return
    setError('')
    try{
      const detail=await apiRequest<DraftDetail>(`/datasets/${job.dataset_id}`,role)
      const version=detail.selected_version
      const access=await apiRequest<DownloadAccess>(`/datasets/${job.dataset_id}/versions/${version.version_number}/download-access`,role)
      const artifactFormat=format==='json'?'json':'csv'
      const review:DownloadReview={datasetId:job.dataset_id,datasetName:detail.data_set.name,referenceCode:detail.data_set.reference_code,versionNumber:version.version_number,format:artifactFormat,access}
      if(access.blocked_record_count>0||!access.can_download){setDownloadReview(review);return}
      await saveSourceArtifact(review)
    }catch(err){setError(message(err))}
  }
  async function saveSourceArtifact(review:DownloadReview){
    setDownloadBusy(true)
    try{const result=await apiDownload(`/source-data/datasets/${review.datasetId}/artifact`,role);const url=URL.createObjectURL(result.blob);const a=document.createElement('a');a.href=url;a.download=result.filename??`${review.referenceCode}.${review.format}`;a.click();URL.revokeObjectURL(url);setDownloadReview(null)}catch(err){setError(message(err))}finally{setDownloadBusy(false)}
  }

  const visibleSourcePlans=savedSourcePlans.filter(saved=>{
    if(saved.state!=='draft'&&saved.state!=='saved')return false
    const data=saved.plan_data as SourcePlanData
    if(data.kind==='federated_config')return false
    if(Boolean(data.method==='query')!==queryMode)return false
    return !(data.draftDatasetId&&excludedDatasetIds.has(data.draftDatasetId))
  })
  const federatedConfigurations=savedSourcePlans.filter(saved=>saved.state==='saved'&&saved.plan_data.kind==='federated_config')
  const selectedFederatedConfig=federatedConfigurations.find(saved=>saved.id===federatedConfigId)

  if(role!=='Test Data Engineer')return <section className="panel scenario-empty"><GitBranch size={24}/><h2>Test Data Engineer access required</h2><p>{queryMode?'SQL-based data generation requires a Test Data Engineer.':'Source mappings and source-backed data generation require a Test Data Engineer.'}</p></section>
  if(!workspaceId)return <section className="panel scenario-empty"><GitBranch size={24}/><h2>Select a workspace</h2><p>{queryMode?'SQL to Data uses workspace-approved PostgreSQL connections.':'Source to Data uses workspace-approved connections and mappings.'}</p></section>
  return <div className="scenario-page source-data-page">
    <section className="scenario-hero panel"><div><div className="eyebrow">{queryMode?'SQL TO DATA':'SOURCE TO DATA'}</div><h2>{queryMode?'Create a governed data set from a PostgreSQL query':'Create a governed data set from an approved source'}</h2><p>{queryMode?'Choose an enabled PostgreSQL source, validate and execute a read-only SQL query, then generate a validated draft for privacy review and publication.':'Choose a saved source, version its field mapping, review a bounded request, then generate a validated draft for privacy review and publication.'}</p></div><div className="scenario-hero-actions"><div className="scenario-provider"><Database size={16}/><span>Generation mode</span><strong>{queryMode?'SQL query':'Source-backed'}</strong></div></div></section>
    {step===0&&visibleSourcePlans.length>0&&<section className="saved-plans-panel-v3 panel">
      <header className="saved-plans-heading-v3">
        <div><span className="eyebrow">YOUR WORK</span><h3>{queryMode?'Saved SQL plans':'Saved source plans'}</h3><p>Pick up where you left off. Dataset drafts created from this flow are listed here too.</p></div>
        <span className="saved-plans-count-v3">{visibleSourcePlans.length} saved</span>
      </header>
      <div className="saved-plans-rail-v3">
        {visibleSourcePlans.sort((a,b)=>new Date(b.updated_at).getTime()-new Date(a.updated_at).getTime()).map(saved=>{
          const data=saved.plan_data as SourcePlanData
          const datasetName=data.datasetName?.trim()||saved.name
          const referenceCode=(data.draftDatasetId?datasetReferences[data.draftDatasetId]:undefined)||datasetReferencesByName[datasetName.trim().toLocaleLowerCase()]
          const planSteps=data.method==='query'?QUERY_STEPS:MAPPING_STEPS
          const savedStep=Number.isInteger(data.step)?Math.max(0,Math.min(planSteps.length-1,data.step!)):0
          return <SavedPlanCard
            key={saved.id}
            icon={<Database size={19}/>}
            title={datasetName}
            pathway={data.method==='query'?'Pre-built query':'Object mapped'}
            step={`Step ${savedStep+1} · ${planSteps[savedStep]}`}
            referenceCode={referenceCode||'Data set ID pending'}
            status={saved.state==='draft'?'Draft':'Saved'}
            statusTone={saved.state==='draft'?'draft':'saved'}
            updatedAt={new Date(saved.updated_at).toLocaleDateString()}
            onResume={()=>resumeSourcePlan(saved)}
            onQuarantine={saved.state==='draft'?()=>void quarantineSourceDraft(saved,data):undefined}
            quarantineDisabled={quarantiningSourcePlanId===saved.id}
          />
        })}
      </div>
    </section>}
    <section className="scenario-steps panel" aria-label="Source to Data workflow steps">{steps.map((label,index)=><button type="button" key={label} className={`${index===step?'current':''} ${index<step?'complete':''}`} onClick={()=>index<step&&setStep(index)} disabled={index>step}><span>{index<step?<Check size={15}/>:index+1}</span><small>{label}</small></button>)}</section>
    <div className="scenario-step-overview"><div><div className="eyebrow">{step===0?'STEP 1 · SELECT SOURCE':step===1?(queryMode?'STEP 2 · EXECUTE QUERY':'STEP 2 · MAP FIELDS'):step===2?'STEP 3 · GENERATION SETTINGS':'STEP 4 · REVIEW & GENERATE'}</div><h2>{step===0?(queryMode?"Select a PostgreSQL source":"Choose the approved source objects"):step===1?(queryMode?"Run a read-only source query":"Map selected fields"):step===2?"Set delivery and output details":"Review the extraction request"}</h2><p>{step===0?(queryMode?"Select an enabled PostgreSQL connection for your pre-built query.":"Select a branch to include its objects, then include only the fields needed for generation."):step===1?(queryMode?"Paste a SELECT or WITH query, or a read-only CALL that returns output columns. Joins and field aliases are defined in the query.":"The selected connection and objects are shown below. Only selected fields are mapped here; return to Select Source to change the scope."):step===2?"Preview displays metadata only. Source access starts after explicit confirmation on the next step.":"{piiMaskingEnabled?'Generation creates a validated draft. Review privacy and publish it from Data Sets before QA access.':'Generation creates validated data sets that can be published from Data Sets.'}"}</p></div><button type="button" className="button button-secondary" onClick={()=>void savePlanDraft()} disabled={busy}><Save size={15}/>Save draft</button></div>
          <details className="scenario-mode-panel" open={requirementsOpen} onToggle={event=>setRequirementsOpen(event.currentTarget.open)}><summary className="scenario-mode-indicator is-source_backed" aria-label="Generation mode: Source-backed"><span className="scenario-mode-indicator-icon"><Database size={16}/></span><div className="scenario-mode-indicator-context"><span><small>{(draftDatasetId&&datasetReferences[draftDatasetId])||datasetReferencesByName[datasetName.trim().toLocaleLowerCase()]||'ID not assigned'}</small><strong>{datasetName.trim()||'Untitled data set'}</strong></span><span><small>Source connection</small><strong>{source?.name??'Not selected'}</strong></span></div><span className="scenario-mode-indicator-copy"><small>Generation mode</small><strong>Source-backed</strong></span></summary><div className="scenario-requirement-content"><section><strong>Test data requirements</strong><p>{testDataRequirements.trim()||'No test data requirements entered.'}</p></section><small className="source-requirements-note">For reference only. These requirements are saved with the plan and are not sent to the model or used to change source queries.</small></div></details>
        <form className="scenario-step-panel panel" onSubmit={event=>{event.preventDefault();step===3?void startGeneration(event):void continueStep()}}>

      {step===0&&<><div className="scenario-section-heading"><div><div className="eyebrow">STEP 1 · SELECT SOURCE</div><h2>{queryMode?"Select a PostgreSQL source":"Choose the approved source objects"}</h2><p>{queryMode?"Select an enabled PostgreSQL connection for your pre-built query.":"Select a branch to include its objects, then include only the fields needed for generation."}</p></div><button type="button" className="button button-secondary" onClick={()=>void savePlanDraft()} disabled={busy}><Save size={15}/>Save draft</button></div><div className="source-select-controls"><label className="form-field">Data set name<input required minLength={2} maxLength={180} value={datasetName} onChange={e=>setDatasetName(e.target.value)} placeholder="e.g. Customer account extract"/></label><label className="form-field">Source connection<select value={sourceId} onChange={e=>chooseSource(e.target.value)}><option value="">Choose a connection</option>{sources.filter(item=>!queryMode||item.connector_type==='postgresql').map(item=><option value={item.id} key={item.id}>{item.name} · {item.connector_type}</option>)}</select></label>{!queryMode&&<label className="form-field">Federated source<select value={federatedConfigId} onChange={e=>chooseFederatedConfig(e.target.value)}><option value="">Choose a saved federated join</option>{federatedConfigurations.filter(item=>!sourceId||((item.plan_data.federatedPlan as FederatedPlanDraft|undefined)?.sources??[]).some(source=>source.connection_id===sourceId)).map(item=><option value={item.id} key={item.id}>{item.name} · {((item.plan_data.federatedPlan as FederatedPlanDraft|undefined)?.sources??[]).length} sources</option>)}</select></label>}{!queryMode&&!federatedPlan.sources.length&&<><label className="form-field">Saved mapping<select value={mappingId} onChange={e=>chooseMapping(e.target.value)}><option value="">Create a mapping version</option>{mappings.filter(item=>item.connection_id===sourceId).map(item=><option value={item.id} key={item.id}>{item.name} · {item.version}</option>)}</select></label><label className="form-field">Mapping name<input value={mappingName} onChange={e=>setMappingName(e.target.value)} placeholder="e.g. Customer extract"/></label></>}</div><TestDataRequestPicker role={role} workspaceId={workspaceId} value={testDataRequestId} disabled={busy} onChange={ticket=>{setTestDataRequestId(ticket?.id??"");if(ticket){setTestDataRequirements(testDataRequestRequirements(ticket));if(ticket.record_count!=null)setVolume(String(ticket.record_count));const objective=testDataRequestObjective(ticket);if(objective)setTargetUse(objective);const requestedExpiry=testDataRequestExpiry(ticket);if(requestedExpiry)setExpiry(requestedExpiry)}}}/><label className="form-field source-test-requirements">Test data requirements<textarea rows={4} maxLength={4000} value={testDataRequirements} onChange={event=>setTestDataRequirements(event.target.value)} placeholder="eg. Describe the test data you need."/><small>These requirements are saved with the plan. Selecting a request fills this field from its objective and scenarios.</small></label>{!queryMode&&federatedPlan.sources.length>0&&<section className="federated-step-review"><strong>{selectedFederatedConfig?.name??'Saved federated source'}</strong><span>{federatedPlan.sources.length} source objects · {federatedPlan.sources.reduce((count,item)=>count+item.fields.length,0)} selected fields</span><small>{federatedPlan.sources.map(item=>`${sources.find(source=>source.id===item.connection_id)?.name??'Source'} · ${item.database}.${item.schema}.${item.object_name}`).join('  →  ')}</small></section>}{!queryMode&&!federatedPlan.sources.length&&<div className="source-selection-panels"><section className="source-selection-card source-tree-panel"><div className="source-card-heading"><div><h3>Source objects</h3><p>{source?`${source.objects.length} objects available`:'Select a source connection to browse its objects'}</p></div>{objectNames.length>0&&<span className="source-count-badge">{objectNames.length} selected</span>}</div>{source&&<div className="source-object-tree">{objectBranches.map(branch=>{const branchNames=branch.objects.map(item=>item.name),allSelected=branchNames.length>0&&branchNames.every(name=>objectNames.includes(name)),someSelected=branchNames.some(name=>objectNames.includes(name));return <details className="source-tree-branch" key={branch.name} open><summary><ChevronDown size={15}/><span><strong>{branch.name}</strong><small>{branchNames.filter(name=>objectNames.includes(name)).length}/{branchNames.length} objects selected</small></span><input type="checkbox" checked={allSelected} aria-checked={someSelected&&!allSelected?'mixed':allSelected} aria-label={`Select all objects in ${branch.name}`} onClick={event=>event.stopPropagation()} onChange={event=>chooseBranch(branchNames,event.target.checked)}/></summary><div className="source-tree-leaves">{branch.objects.map(item=><label className={objectNames.includes(item.name)?'is-selected':''} key={item.name}><input type="checkbox" checked={objectNames.includes(item.name)} onChange={()=>chooseObject(item.name)}/><Table2 size={15}/><span><strong>{item.name.split('.').slice(-1)[0]}</strong><small>{item.type??'Source object'} · {item.fields.length} fields</small></span></label>)}</div></details>})}</div>}</section><section className="source-selection-card source-selected-fields-panel"><div className="source-card-heading"><div><h3>Fields from selected objects</h3><p>Primary keys are marked with a key icon</p></div><span className="source-count-badge">Selected {selectedFields.length}/{fields.length} fields</span></div>{fields.length?<div className="source-selected-fields-table"><table><thead><tr><th aria-label="Primary key"></th><th>Source object</th><th>Source field</th><th>Type</th><th>Include</th></tr></thead>{objectNames.map(objectNameForFields=>{const objectFields=fields.filter(field=>field.source_object===objectNameForFields);if(!objectFields.length)return null;return <tbody className="source-object-group" key={objectNameForFields}>{objectFields.map(field=><tr key={`${field.source_object}-${field.source_field}`}><td>{field.primary_key&&<span className="source-primary-key-mark" role="img" aria-label={`${field.source_object}.${field.source_field} is a primary key`} title="Primary key"><KeyRound size={13}/></span>}</td><td><span className="source-object-name" title={field.source_object}>{field.source_object}</span></td><td><span className="source-field-name" title={field.source_field}>{field.source_field}</span></td><td><code>{field.data_type}</code></td><td><input aria-label={`Include ${field.source_object}.${field.source_field}`} type="checkbox" checked={selected.includes(field.target_field)} disabled={field.required} onChange={event=>setSelected(rows=>event.target.checked?[...rows,field.target_field]:rows.filter(item=>item!==field.target_field))}/></td></tr>)}</tbody>})}</table></div>:<div className="source-empty-selection"><FolderTree size={22}/><strong>No fields available yet</strong><span>Select one or more objects from the tree.</span></div>}</section></div>}{objectNames.length>0&&<div className="scenario-metadata-strip">Approved structure · {objectNames.length} object{objectNames.length===1?'':'s'} · metadata only, no source record values loaded.</div>}{queryMode&&<div className="scenario-metadata-strip">Query mode uses an enabled PostgreSQL source. Enter and execute a read-only query in step 2; output values are not previewed.</div>}</>}
      {step===1&&queryMode&&<><div className="scenario-section-heading"><div><div className="eyebrow">STEP 2 · EXECUTE QUERY</div><h2>Run a read-only source query</h2><p>Paste a SELECT or WITH query, or a read-only CALL that returns output columns. Joins and field aliases are defined in the query.</p></div></div><div className="source-query-workspace"><section className="source-query-editor-panel"><div className="source-query-input-column"><label className="form-field source-query-label">Pre-built query<div className="source-query-editor-shell"><pre ref={queryHighlightRef} className="source-query-highlight" aria-hidden="true" dangerouslySetInnerHTML={{__html:queryText?highlightSql(queryText):'<span className="sql-placeholder">Paste a SELECT, WITH, or CALL statement</span>'}}/><textarea ref={queryEditorRef} className="source-query-editor" spellCheck={false} wrap="off" aria-label="Pre-built SQL query" value={queryText} onChange={event=>{setQueryText(event.target.value);setQueryId('');setQueryOutputFields([]);setQueryHasRows(false);setQuerySyntaxValid(null);setQuerySyntaxMessage('');setNotice('')}} onScroll={event=>{if(queryHighlightRef.current){queryHighlightRef.current.scrollTop=event.currentTarget.scrollTop;queryHighlightRef.current.scrollLeft=event.currentTarget.scrollLeft}}}/></div></label><div className="source-query-actions"><button type="button" className={`button button-secondary source-query-validate${querySyntaxValid===true?' is-valid':querySyntaxValid===false?' is-invalid':''}`} disabled={busy||!sourceId||!queryText.trim()} onClick={()=>void validateQuery()} aria-label="Validate query syntax" title="Validate query syntax">{busy?<LoaderCircle size={16} className="scenario-spin"/>:querySyntaxValid===true?<BadgeCheck size={16}/>:querySyntaxValid===false?<CircleAlert size={16}/>:<BadgeCheck size={16}/>}<span>Validate syntax</span></button><button type="button" className="button button-confirm" disabled={busy||!sourceId||!queryText.trim()} onClick={()=>void executeQuery()}><Code2 size={15}/>{busy?'Working…':'Execute Query'}</button>{queryId&&<span className="source-query-execution">Executed · {queryOutputFields.length} output fields · {queryHasRows?'rows found':'no matching rows'}</span>}</div></div><aside className={`source-query-diagnostics-panel${querySyntaxValid===false?' has-error':querySyntaxValid===true?' is-valid':''}`} aria-live="polite"><div className="source-query-diagnostics-heading">{querySyntaxValid===false?<CircleAlert size={18}/>:querySyntaxValid===true?<BadgeCheck size={18}/>:<Code2 size={18}/>}<div><h3>Validation messages</h3><small>{querySyntaxValid===false?'Issues found':querySyntaxValid===true?'Query validated':'SQL diagnostics'}</small></div></div>{querySyntaxMessage?<div className="source-query-diagnostic" role={querySyntaxValid===false?'alert':'status'}>{querySyntaxMessage}</div>:<p className="source-query-diagnostics-empty">Enter a query and select <strong>Validate syntax</strong> to see syntax or source reference issues here.</p>}</aside></section></div>{queryOutputFields.length>0&&<section className="source-query-output"><div className="source-card-heading"><div><h3>Query output fields</h3><p>Search and choose the source schema field for each query result column. The selection carries its source type and primary key into Step 3.</p></div><span className="source-count-badge">{queryOutputFields.length} fields</span></div>{groupQueryFields(queryOutputFields).map(([sourceObject,groupFields])=><section className="source-query-object-group" key={sourceObject}><div className="source-query-object-heading">{sourceObject}</div><div className="scenario-mapping-table source-query-fields-table"><table><thead><tr><th aria-label="Primary key"></th><th>Query result field</th><th>Source schema field</th><th>Output field</th><th>Type</th></tr></thead><tbody>{groupFields.map(field=><tr key={field.name}><td>{isPrimaryKey({primary_key:field.primary_key,source_object:field.source_object??undefined,source_field:field.source_field??undefined})&&<span className="source-primary-key-mark" role="img" aria-label={`${field.name} is a primary key`} title="Primary key"><KeyRound size={13}/></span>}</td><td><span className="source-query-original-field"><span title={field.name}>{field.name}</span></span></td><td><SearchableSchemaFieldPicker options={sourceSchemaFieldOptions} value={querySchemaFieldValue(field)} ariaLabel={`Source schema field for ${field.name}`} onChange={value=>updateQuerySchemaField(field.name,value)}/></td><td><input className="source-query-output-name" aria-label={`Output field name for ${field.name}`} value={field.output_field} onChange={event=>setQueryOutputFields(rows=>rows.map(item=>item.name===field.name?{...item,output_field:event.target.value}:item))}/></td><td><code>{field.data_type}</code></td></tr>)}</tbody></table></div></section>)}</section>}</>}
      {step===1&&!queryMode&&federatedPlan.sources.length>0&&<FederatedFieldSelection plan={federatedPlan} sourceProfiles={sources} onChange={setFederatedPlan}/>}
      {step===1&&!queryMode&&!federatedPlan.sources.length&&<><div className="scenario-section-heading"><div><div className="eyebrow">STEP 2 · MAP FIELDS</div><h2>Map selected fields</h2><p>The selected connection and objects are shown below. Only selected fields are mapped here; return to Select Source to change the scope.</p></div></div><div className="source-selected-fields-total"><span className="source-count-badge">{selectedFields.length} selected fields</span></div><div className="source-step-scope"><div><span>Selected connection</span><strong>{source?.name??"—"} · {source?.connector_type??"—"}</strong></div><div><span>Selected objects</span><strong>{objectNames.join(", ")||"—"}</strong></div></div>{objectNames.map(objectNameForFields=>{const objectFields=fields.map((field,index)=>({field,index})).filter(item=>item.field.source_object===objectNameForFields&&selected.includes(item.field.target_field));return <details className="source-object-accordion" key={objectNameForFields} open={objectNames.length===1}><summary><strong>{objectNameForFields}</strong></summary><div className="scenario-mapping-table"><table><thead><tr><th>Source field</th><th>Output field</th><th>Type</th></tr></thead><tbody>{objectFields.map(({field,index})=><tr key={`${field.source_object}-${field.source_field}-${index}`}><td>{field.source_field}</td><td><input value={field.target_field} onChange={e=>editField(index,'target_field',e.target.value)}/></td><td><code>{field.data_type}</code></td></tr>)}</tbody></table></div></details>})}{objectNames.length>1&&<section className="scenario-source-joins"><div><h3>Join keys</h3><p>Connect each selected object using matching source key fields.</p></div>{sourceJoins.map((join,index)=>{const leftFields=fields.filter(field=>field.source_object===join.left_object);const rightFields=fields.filter(field=>field.source_object===join.right_object);return <div className="scenario-source-join-row" key={index}><label className="form-field">Object<select value={join.left_object} onChange={e=>setSourceJoins(rows=>rows.map((row,i)=>i===index?{...row,left_object:e.target.value,left_field:''}:row))}>{objectNames.map(name=><option key={name}>{name}</option>)}</select></label><label className="form-field">Key field<select value={join.left_field} onChange={e=>setSourceJoins(rows=>rows.map((row,i)=>i===index?{...row,left_field:e.target.value}:row))}><option value="">Select key</option>{leftFields.map(field=><option key={field.source_field}>{field.source_field}</option>)}</select></label><label className="form-field">Join to<select value={join.right_object} onChange={e=>setSourceJoins(rows=>rows.map((row,i)=>i===index?{...row,right_object:e.target.value,right_field:''}:row))}>{objectNames.filter(name=>name!==join.left_object).map(name=><option key={name}>{name}</option>)}</select></label><label className="form-field">Key field<select value={join.right_field} onChange={e=>setSourceJoins(rows=>rows.map((row,i)=>i===index?{...row,right_field:e.target.value}:row))}><option value="">Select key</option>{rightFields.map(field=><option key={field.source_field}>{field.source_field}</option>)}</select></label><button type="button" className="text-button" onClick={()=>setSourceJoins(rows=>rows.filter((_,i)=>i!==index))}>Remove</button></div>})}<button type="button" className="button button-secondary" onClick={()=>{const left=objectName||objectNames[0];const right=objectNames.find(name=>name!==left)??'';setSourceJoins(rows=>[...rows,{left_object:left,left_field:'',right_object:right,right_field:''}])}}><Plus size={15}/>Add join</button></section>}<div className="scenario-relationships"><div><h3>Output relationships</h3><p>Optional checks for parent and child fields in the generated rows.</p></div>{relationships.map((rel,index)=><div className="scenario-relationship-row" key={index}><label className="form-field">Parent key<select value={rel.parent_field} onChange={e=>setRelationships(rows=>rows.map((row,i)=>i===index?{...row,parent_field:e.target.value}:row))}><option value="">Select field</option>{fields.filter(item=>selected.includes(item.target_field)).map(item=><option key={item.target_field}>{item.target_field}</option>)}</select></label><label className="form-field">Child key<select value={rel.child_field} onChange={e=>setRelationships(rows=>rows.map((row,i)=>i===index?{...row,child_field:e.target.value}:row))}><option value="">Select field</option>{fields.filter(item=>selected.includes(item.target_field)).map(item=><option key={item.target_field}>{item.target_field}</option>)}</select></label><button type="button" className="text-button" onClick={()=>setRelationships(rows=>rows.filter((_,i)=>i!==index))}>Remove</button></div>)}<button type="button" className="button button-secondary" onClick={()=>setRelationships(rows=>[...rows,{parent_field:'',child_field:''}])}><Plus size={15}/>Add relationship</button></div></>}
      
      {step===2&&<><div className="scenario-section-heading"><div><div className="eyebrow">STEP 3 · GENERATION SETTINGS</div><h2>Set delivery and output details</h2><p>Preview displays metadata only. Source access starts after explicit confirmation on the next step.</p></div></div>{step===2&&<section className="source-plan-settings panel"><div className="scenario-form-grid"><label className="form-field">Data set name<input required minLength={2} maxLength={180} value={datasetName} onChange={e=>setDatasetName(e.target.value)}/></label><label className="form-field">Target use<input required value={targetUse} onChange={e=>setTargetUse(e.target.value)}/></label><label className="form-field">Record Count<input required type="number" inputMode="numeric" step={1} min={1} max={10000} value={volume} onChange={e=>setVolume(e.target.value)}/></label><label className="form-field">Output format<select required value={format} onChange={e=>setFormat(e.target.value as 'json'|'csv'|'')}><option value="">Choose a format</option><option value="json">JSON</option><option value="csv">CSV</option></select></label><label className="form-field">Expiry (optional)<input type="datetime-local" value={expiry} onChange={e=>setExpiry(e.target.value)}/></label></div></section>}<div className="generation-detail-panels"><section className="generation-fields-panel"><div className="source-card-heading"><div><h3>Output fields</h3><p>Primary keys are marked with a key icon</p></div><span className="source-count-badge">{outputFields.length} fields</span></div><div className="generation-selected-fields">{outputFields.map(field=><div className="generation-field-row" key={field.target_field}><span className={`generation-field-key${isPrimaryKey(field)?' is-primary-key':''}`} title={isPrimaryKey(field)?"Primary key":undefined}>{isPrimaryKey(field)&&<><KeyRound size={14} className="source-primary-key-icon" aria-label="Primary key"/></>}</span><div className="generation-field-name"><strong>{field.target_field}</strong><small>{field.source_object??'Query result'}.{field.source_field??field.target_field}</small></div><code>{field.data_type}</code></div>)}</div></section><section className="generation-source-panel"><div className="source-card-heading"><div><h3>Source details</h3><p>Connection and mapping</p></div><GitBranch size={17}/></div><dl className="generation-source-details"><div><dt>Connection</dt><dd>{source?.name??'—'}</dd></div><div><dt>Connector type</dt><dd>{source?.connector_type??"—"}</dd></div><div><dt>Saved mapping</dt><dd>{currentMapping?.name??(mappingName||'Unsaved')}</dd></div><div><dt>Mapping version</dt><dd>{mappingName?`${mappingName} · assigned when generation starts`:'Assigned when generation starts'}</dd></div><div><dt>{queryMode?'Query output':'Objects'}</dt><dd>{queryMode?`${outputFields.length} fields from executed query`:objectNames.join(', ')||'—'}</dd></div></dl><p className="generation-source-note">Source values remain hidden in this step. Extraction starts only after you confirm on the review step.</p></section></div>{!queryMode&&<section className="scenario-filters"><div className="scenario-mapping-header"><div><h3>Filters</h3><p>Apply one or more criteria to the selected source fields.</p></div><button type="button" className="button button-secondary" onClick={()=>setFilters(rows=>[...rows,{id:Date.now(),field:'',operator:'eq',value:''}])}><Plus size={15}/>Add filter</button></div>{filters.map(filter=><div className="scenario-filter-grid" key={filter.id}><label className="form-field">Field<select value={filter.field} onChange={e=>setFilters(rows=>rows.map(row=>row.id===filter.id?{...row,field:e.target.value}:row))}><option value="">Choose a field</option>{fields.filter(item=>selected.includes(item.target_field)).map(item=><option value={item.target_field} key={item.target_field}>{item.target_field}</option>)}</select></label><label className="form-field">Operator<select value={filter.operator} onChange={e=>setFilters(rows=>rows.map(row=>row.id===filter.id?{...row,operator:e.target.value,value:''}:row))}>{['eq','ne','lt','lte','gt','gte','in','is_null'].map(item=><option key={item} value={item}>{item}</option>)}</select></label>{filter.operator!=='is_null'&&<label className="form-field">Value<input value={filter.value} onChange={e=>setFilters(rows=>rows.map(row=>row.id===filter.id?{...row,value:e.target.value}:row))} placeholder={filter.operator==='in'?'e.g. ["A","B"]':'e.g. Value'}/></label>}<button type="button" className="text-button" onClick={()=>setFilters(rows=>rows.filter(row=>row.id!==filter.id))}>Remove</button></div>)}</section>}</>}
      {step===3&&<><div className="scenario-section-heading"><div><div className="eyebrow">STEP 4 · REVIEW & GENERATE</div><h2>Review the extraction request</h2><p>{piiMaskingEnabled?'Generation creates a validated draft. Complete privacy review and publish it from Data Sets before QA access.':'Generation creates a validated data set. Review and publish it from Data Sets when ready.'}</p></div></div><div className="scenario-review-grid"><div><span>Mapping version</span><strong>{queryMode?`Executed query · ${queryId.slice(0,8)}`:`${mappingName||"Mapping"} · version assigned when generation starts`}</strong></div><div><span>Selected fields</span><strong>{outputFields.length} of {queryMode?queryOutputFields.length:fields.length}</strong></div><div><span>Record Count</span><strong>{volume.toLocaleString()}</strong></div><div><span>Format · use</span><strong>{format.toUpperCase()} · {targetUse}</strong></div></div>{!job&&draftGenerationStatus==='generated'&&<div className="alert alert-warning" role="status">This draft already has generation data. Create a fresh draft to generate again.</div>}{!job&&draftGenerationStatus==='checking'&&<div className="scenario-notice" role="status">Checking this draft before generation…</div>}{!job&&draftGenerationStatus==='unavailable'&&<div className="alert alert-warning" role="alert">Could not verify this draft. <button type="button" className="text-button" onClick={()=>setDraftCheckRetry(value=>value+1)}>Retry check</button></div>}{!job&&<section className={`source-confirm-card${confirm?' is-confirmed':''}`}><div className="source-confirm-heading"><span className="source-confirm-icon"><ShieldCheck size={19}/></span><div><div className="eyebrow">SOURCE ACCESS CONTROL</div><h3>Confirm source extraction</h3><p>Review the selected objects, fields, filters, and Record Count. TIGR will access the source only after you confirm and generate.</p></div></div><div className="source-confirm-summary"><span><strong>{queryMode?1:objectNames.length}</strong> {queryMode?'query':'objects'}</span><span><strong>{outputFields.length}</strong> output fields</span><span><strong>{filters.filter(filter=>filter.field).length}</strong> filters</span><span><strong>{volume.toLocaleString()}</strong> Record Count</span></div><section className="source-review-metadata"><div className="source-review-connection"><span>Selected connection</span><strong>{source?.name??"—"} · {source?.connector_type??"—"}</strong></div><div className="source-review-object-list">{queryMode?<span>Executed query · {queryOutputFields.length} output fields</span>:objectNames.map(name=><span key={name}>{name}</span>)}</div><div className="source-review-field-table"><table><thead><tr><th aria-label="Primary key"></th><th>{queryMode?'Source schema field':'Source object'}</th><th>Output field</th><th>Type</th></tr></thead><tbody>{outputFields.map(field=>{const queryField=queryMode?queryOutputFields.find(item=>item.output_field===field.target_field):undefined;return <tr key={field.target_field}><td>{field.primary_key&&<span className="source-primary-key-mark" role="img" aria-label="Primary key" title="Primary key"><KeyRound size={13}/></span>}</td><td>{queryMode&&queryField?<span className="source-query-schema-readonly" title={querySchemaFieldLabel(queryField)}>{querySchemaFieldLabel(queryField)}</span>:field.source_object||'Unmapped'}</td><td>{field.target_field}</td><td><code>{field.data_type}</code></td></tr>})}</tbody></table></div></section><label className="source-confirm-choice"><input type="checkbox" checked={confirm} onChange={e=>setConfirm(e.target.checked)}/><span><strong>I reviewed and authorize this extraction</strong><small>This confirmation is required before the generation job can start.</small></span></label></section>}{job&&<div className="scenario-job-status"><div><strong>Job {job.id}</strong><span className="status-badge"><i/>{job.status}</span></div><progress max="100" value={job.progress}/><small>{job.safe_summary??'Generation is processing.'}</small></div>}{job?.status==='succeeded'&&<div className="scenario-complete"><Check size={18}/><div><strong>Validation passed · draft ready</strong><p>Data set {job.dataset_reference_code??'generated data set'} is saved with lineage. {piiMaskingEnabled?'Privacy state is requires masking; QA access remains gated.':'The validated data set is ready for publication.'}</p></div></div>}{job?.status==='failed'&&<div className="alert alert-warning" role="alert"><strong>Generation or validation failed.</strong><span>{job.safe_summary||'Review the mapping and connection, then retry.'}</span><button type="button" className="button button-secondary compact" disabled={busy} onClick={()=>void resetFailedGenerationDraft()}>Retry with a fresh draft</button></div>}</>}
      <footer className="scenario-step-actions">{(error||notice)&&<div className="scenario-step-feedback">{error&&<div className="alert alert-warning" role="alert">{error}</div>}{notice&&<div className="scenario-notice" role="status">{notice}{job&&<button type="button" className="button button-secondary compact" onClick={()=>navigate('/jobs')}>View in Jobs</button>}</div>}</div>}<div className="scenario-step-control-row">{step>0&&<button type="button" className="button button-secondary" onClick={()=>setStep(value=>value-1)}><ArrowLeft size={15}/>Back</button>}<span className="scenario-step-spacer"/>{step<3?<button type="button" className="button button-confirm" disabled={busy} onClick={()=>void continueStep()}>{busy&&<LoaderCircle className="scenario-spin" size={15}/>}Continue<ArrowRight size={15}/></button>:job?.status==='succeeded'||job?.status==='succeeded_with_warnings'?<><button type="button" className="button button-secondary" disabled={!downloadReady} title={downloadReady?`Download the published ${format.toUpperCase()} artifact`:`Download is available after publication. Open Data Sets to review status.`} onClick={()=>void download()}><Download size={15}/>Download output</button>{piiMaskingEnabled&&job.dataset_id&&<button type="button" className="button button-primary" onClick={()=>navigate(`/privacy?dataset_id=${encodeURIComponent(job.dataset_id!)}`)}><ShieldCheck size={15}/>Review PII & masking</button>}<button type="button" className="button button-confirm" disabled={!job.dataset_id} onClick={()=>job.dataset_id&&openDataSet(job.dataset_id)}><GitBranch size={15}/>Open data set</button></>:draftGenerationStatus==='generated'&&draftDatasetId?<button type="button" className="button button-confirm" onClick={()=>openDataSet(draftDatasetId)}><GitBranch size={15}/>Open data set</button>:<button className="button button-confirm" disabled={busy||!confirm||!!job||draftGenerationStatus!=='empty'}><Sparkles size={15}/>{busy?'Starting…':job?.status==='failed'?'Generation failed':'Generate data'}</button>}</div></footer>
    </form>
    {downloadReview&&<DownloadReviewDialog review={downloadReview} busy={downloadBusy} onCancel={()=>setDownloadReview(null)} onDownload={()=>void saveSourceArtifact(downloadReview)}/>}
  </div>
}
function FederatedFieldSelection({plan,sourceProfiles,onChange}:{plan:FederatedPlanDraft;sourceProfiles:Source[];onChange:(plan:FederatedPlanDraft)=>void}){
  function isJoinField(alias:string,field:string){return plan.joins.some(join=>(join.left_alias===alias&&join.left_field===field)||(join.right_alias===alias&&join.right_field===field))}
  function toggleField(alias:string,field:string,checked:boolean){
    onChange({...plan,sources:plan.sources.map(source=>source.alias!==alias?source:{...source,fields:checked?[...new Set([...source.fields,field])]:isJoinField(alias,field)?source.fields:source.fields.filter(name=>name!==field)})})
  }
  return <section className="federated-step-review federated-fields-review"><header><div><h3>Federated fields</h3><p>Select additional discovered fields to include. Join fields stay selected.</p></div><span>{plan.sources.reduce((count,item)=>count+item.fields.length,0)} fields</span></header>{plan.sources.map(item=>{
    const profile=sourceProfiles.find(source=>source.id===item.connection_id)
    const object=profile?.databases.find(database=>database.name===item.database)?.objects.find(candidate=>candidate.name===item.object_name&&candidate.schema===item.schema)
    const available=object?.fields??item.fields.map(name=>({name,type:'Unknown'}))
    return <div className="federated-fields-review-source" key={item.alias}><strong>{profile?.name??item.alias} · {item.database}.{item.schema}.{item.object_name}</strong><div className="federated-extra-fields">{available.map(field=>{
      const checked=item.fields.includes(field.name),joinField=isJoinField(item.alias,field.name)
      return <label className={checked?'is-selected':''} key={field.name}><input type="checkbox" checked={checked} disabled={joinField} onChange={event=>toggleField(item.alias,field.name,event.target.checked)}/><span><strong>{field.name}</strong></span></label>
    })}</div></div>
  })}</section>
}

function SearchableSchemaFieldPicker({options,value,onChange,ariaLabel,compact=false}:{options:Array<{objectName:string;field:{name:string;type:string;primary_key?:boolean}}>;value:string;onChange:(value:string)=>void;ariaLabel:string;compact?:boolean}){
  const inputId=useId()
  const selected=value===''?undefined:options[Number(value)]
  const selectedLabel=selected?`${selected.objectName}.${selected.field.name}`:''
  const [search,setSearch]=useState(selectedLabel)
  useEffect(()=>setSearch(selectedLabel),[selectedLabel])
  const listId=`${inputId}-source-fields`
  return <div className={`source-schema-field-picker${compact?' is-compact':''}`}>
    <div className="source-schema-field-search"><Search size={14} aria-hidden="true"/><input type="search" role="combobox" aria-label={ariaLabel} aria-autocomplete="list" list={listId} placeholder="Search source fields…" value={search} onChange={event=>{const next=event.target.value;setSearch(next);const match=options.findIndex(option=>`${option.objectName}.${option.field.name}`===next);if(match>=0)onChange(String(match));else if(!next)onChange('')}} onBlur={()=>{if(search!==selectedLabel){const match=options.findIndex(option=>`${option.objectName}.${option.field.name}`===search);if(match<0){onChange('');setSearch('')}}}}/>{selected&&<span className="source-schema-field-type">{selected.field.type}</span>}</div>
    <datalist id={listId}>{options.map((option,index)=><option value={`${option.objectName}.${option.field.name}`} label={`${option.field.type}${option.field.primary_key?' · primary key':''}`} key={`${option.objectName}.${option.field.name}.${index}`}/>)}</datalist>
  </div>
}

function groupObjects(objects:SourceObject[]){
  const groups=new Map<string,SourceObject[]>()
  for(const item of objects){const separator=item.name.lastIndexOf('.'),branch=separator>0?item.name.slice(0,separator):'Objects';groups.set(branch,[...(groups.get(branch)??[]),item])}
  return [...groups.entries()].map(([name,items])=>({name,objects:items}))
}
function groupQueryFields(fields:QueryOutputField[]){const groups=new Map<string,QueryOutputField[]>();for(const field of fields){const sourceObject=field.source_object??'Query result';groups.set(sourceObject,[...(groups.get(sourceObject)??[]),field])}return [...groups.entries()]}
function draftContainsGenerationData(version:DraftDetail['selected_version']){return version.provenance?.origin!=='not_set'||version.schema_scope?.length>0||version.selected_fields?.length>0||version.record_count!==null||Object.keys(version.validation_evidence??{}).length>0}
function parseValue(value:string):unknown{try{return JSON.parse(value)}catch{return value}}
function toLocalDateTimeInput(value:string){const date=new Date(value);if(Number.isNaN(date.getTime()))return '';const local=new Date(date.getTime()-date.getTimezoneOffset()*60000);return local.toISOString().slice(0,16)}
function message(error:unknown){return error instanceof Error?error.message:'Source to Data request failed'}
