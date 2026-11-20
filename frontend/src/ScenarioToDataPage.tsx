import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { ArrowLeft, ArrowRight, Check, Copy, Database, Download, GitBranch, LoaderCircle, Plus, Save, Sparkles, Trash2, X } from 'lucide-react'
import { apiDownload, apiRequest, type HumanRole } from './api'
import { DatasetActivityIcons } from './DatasetActivity'
import DownloadReviewDialog, { type DownloadAccess, type DownloadReview } from './DownloadReviewDialog'
import { highlightSql } from './sqlHighlight'
import { SCENARIO_TO_DATA_LABEL } from './datasetLabels'
import { ScenarioToDataLabel } from './ScenarioToDataLabel'
import SavedPlanCard from './SavedPlanCard'
import TestDataRequestPicker, { testDataRequestExpiry, testDataRequestObjective, testDataRequestRequirements } from './TestDataRequestPicker'
import './scenario.css'

type FieldMap = {source_field:string;source_object?:string|null;target_field:string;data_type:string;required:boolean;primary_key?:boolean;confidence?:number|null;rationale?:string|null;json_path?:string|null}
type Relationship = {parent_field:string;child_field:string}
type SourceJoin = {left_object:string;left_field:string;right_object:string;right_field:string;confidence?:number|null;rationale?:string|null}
type Source = {id:string;name:string;connector_type:string;objects:Array<{name:string;fields:Array<{name:string;type:string;primary_key?:boolean}>}>}
type Coverage = {criterion_id:string;covered:boolean;source_object?:string|null;source_field?:string|null;explanation:string}
type RequirementIntent = {summary?:string;output_grain?:string;requested_output_fields?:Array<{name:string;data_type:string;purpose:string}>;entities?:Array<{name:string;purpose:string}>;criteria?:Array<{id:string;requirement:string;subject:string;quantifier:string}>;exclusions?:string[]}
type SchemaGenerationStatus = {generation_id:string;job_id?:string;status:'running'|'complete'|'failed';stage:string;error?:string;started_at?:string;stage_started_at?:string;elapsed_ms?:number;timings_ms?:Record<string,number>;reused_requirement_outline?:boolean}
type Preview = {source:string;object:string|null;objects?:string[];query_kind:string;query:string|null;parameter_count:number;row_limit:number;estimated_count:number;exact_count:number;fields:string[];sample_rows:Array<Record<string,unknown>>;sample_count?:number;sample_values_withheld?:boolean;sample_error?:string|null;_signature?:string}
type SampleGenerationStatus = {generation_id:string;job_id?:string;status:'running'|'complete'|'failed';stage:string;error?:string|null;started_at?:string;elapsed_ms?:number;include_sample?:boolean}
type PreviewStart = {status:'running';generation_id:string;job_id?:string}
type Plan = {id:string;name:string;requirement:string;expected_states:string[];exclusions:string[];target_use:string;volume:number;generation_mode:'synthetic'|'source_backed';source_connection_id:string|null;source_object:string|null;mapping:FieldMap[];suggested_mapping:FieldMap[];logical_plan:{draft_data?:Record<string,unknown>;selected_source_objects?:string[];accepted_uncovered_criteria?:string[];predicates:Array<{field:string;operator:string;value?:unknown}>;relationships:Relationship[];schema_generation_notes?:string[];schema_generation_feedback?:string;generation_correction_feedback?:string;requirement_intent?:RequirementIntent;requirement_intent_context_key?:string;schema_generation_status?:SchemaGenerationStatus;sample_generation_status?:SampleGenerationStatus;synthetic_preview?:Preview;criteria_coverage?:Coverage[];source_joins?:SourceJoin[];suggested_source_joins?:SourceJoin[];suggested_filters?:Filter[];generated_query?:string;generated_query_fields?:string[];generated_query_field_types?:Record<string,string>};clarifying_questions:string[];state:string;dataset_id:string|null;dataset_reference_code?:string|null;generation_retryable?:boolean;updated_at:string;job:{id:string;status:string;progress:number;safe_summary:string|null}|null;assistant:{provider:string;model_version:string;prompt_version:string}}
type SqlRepair = {original_error:string;fix_summary:string}
type Filter = {field:string;source_object?:string|null;source_field?:string|null;operator:string;value:string;suggested_values?:string[];confidence?:number|null;rationale?:string|null}
type DraftDetail = {data_set:{name:string;description:string|null;workspace_id:string;creation_pathway:string};selected_version:{lifecycle_state:string;expiry_at:string|null;generation_settings:Record<string,string|number>}}
type DraftSummary = {id:string;reference_code:string;name:string;description:string|null;created_at?:string;latest_version:{lifecycle_state:string;publication_state?:string;schema_scope:Array<unknown>;selected_fields:string[];provenance:Record<string,string>;record_count:number|null;generation_settings:Record<string,string|number>;expiry_at:string|null}}
const STEP_NAMES=['Requirements','Schema mapping','Query development','Data generation']
const OPERATORS=['eq','ne','lt','lte','gt','gte','between','in','is_null']
const schemaStageLabel=(stage:string,mode:'synthetic'|'source_backed')=>({starting:'Preparing schema generation',resolving_model:'Preparing TIGR',model_ready:'TIGR is ready',requirement_interpretation:'TIGR is interpreting the requirement',requirement_interpretation_complete:'Requirement interpretation complete',schema_grounding:mode==='source_backed'?'TIGR is mapping criteria to selected schema objects':'TIGR is generating fields from the requirement',schema_grounding_complete:'Schema mapping complete',validating_suggestions:mode==='source_backed'?'Checking suggested fields and relationships against approved metadata':'Checking the generated schema response',validating_suggestions_complete:'Schema references checked',saving_coverage_review:'Saving criterion coverage',complete:'Schema suggestions ready',failed:'Schema generation failed'} as Record<string,string>)[stage]??'TIGR is generating schema suggestions'
const splitLines=(value:string)=>value.split('\n').map(item=>item.trim()).filter(Boolean)
const scenarioPlanDatasetIds=(item:Plan)=>[item.dataset_id,typeof item.logical_plan?.draft_data?.draft_dataset_id==='string'?item.logical_plan.draft_data.draft_dataset_id:null].filter((id):id is string=>Boolean(id))
function dedupeScenarioPlans(items:Plan[]):Plan[]{
  const ordered=[...items].sort((a,b)=>Number(Boolean(b.dataset_id))-Number(Boolean(a.dataset_id))||new Date(b.updated_at).getTime()-new Date(a.updated_at).getTime())
  const seenPlans=new Set<string>(),seenDatasets=new Set<string>()
  return ordered.filter(item=>{
    if(seenPlans.has(item.id))return false
    seenPlans.add(item.id)
    const datasetIds=scenarioPlanDatasetIds(item)
    if(datasetIds.some(id=>seenDatasets.has(id)))return false
    datasetIds.forEach(id=>seenDatasets.add(id))
    return true
  })
}
const filterValue=(value:unknown)=>Array.isArray(value)||value!==null&&typeof value==='object'?JSON.stringify(value):String(value??'')
const filterSuggestionValues=(value:unknown):string[]=>{
  if(value===null||['string','number','boolean'].includes(typeof value))return [String(value)]
  if(Array.isArray(value))return value.flatMap(filterSuggestionValues)
  if(typeof value==='object'){
    const record=value as Record<string,unknown>
    for(const key of ['values','value','allowed_values','enum_values'])if(key in record)return filterSuggestionValues(record[key])
    return Object.values(record).flatMap(filterSuggestionValues)
  }
  return []
}
const selectedFilterValues=(filter:Filter):string|string[]=>{
  if(filter.operator!=='in')return filter.value
  const value=parseValue(filter.value)
  return Array.isArray(value)?value.map(String):[]
}
const selectedRangeValues=(filter:Filter):[string,string]=>{
  if(filter.operator!=='between')return ['','']
  const value=parseValue(filter.value)
  return Array.isArray(value)?[String(value[0]??''),String(value[1]??'')]:['','']
}
const filterHasValue=(filter:Filter)=>filter.operator==='is_null'||(filter.operator==='between'?selectedRangeValues(filter).every(value=>value.trim()):filter.value.trim().length>0)
const syntheticQueryFor=(fields:FieldMap[])=>`SELECT ${fields.map(field=>`"${field.target_field.replaceAll('"','""')}"`).join(', ')}\nFROM synthetic_data\nLIMIT 5;`

export default function ScenarioToDataPage({role,workspaceId,piiMaskingEnabled}:{role:HumanRole;workspaceId:string;piiMaskingEnabled:boolean}) {
  const navigate=useNavigate()
  const location=useLocation()
  const [step,setStep]=useState(0),[plan,setPlan]=useState<Plan|null>(null),[sources,setSources]=useState<Source[]>([])
  const [requirementsOpen,setRequirementsOpen]=useState(false)
  const [name,setName]=useState(''),[requirement,setRequirement]=useState(''),[expected,setExpected]=useState(''),[exclusions,setExclusions]=useState(''),[targetUse,setTargetUse]=useState(''),[volume,setVolume]=useState('')
  const [testDataRequestId,setTestDataRequestId]=useState('')
  const [mode,setMode]=useState<'synthetic'|'source_backed'>('synthetic'),[sourceId,setSourceId]=useState(''),[sourceObject,setSourceObject]=useState(''),[selectedSourceObjects,setSelectedSourceObjects]=useState<string[]>([]),[mapping,setMapping]=useState<FieldMap[]>([])
  const [relationships,setRelationships]=useState<Relationship[]>([])
  const [sourceJoins,setSourceJoins]=useState<SourceJoin[]>([]),[manualSchema,setManualSchema]=useState(false)
  const [preview,setPreview]=useState<Preview|null>(null)
  const [queryText,setQueryText]=useState(''),[queryEdited,setQueryEdited]=useState(false)
  const [,setQueryAction]=useState<'generate'|'regenerate'|'validate'|null>(null)
  const queryHighlightRef=useRef<HTMLPreElement>(null),queryEditorRef=useRef<HTMLTextAreaElement>(null)
  const [filters,setFilters]=useState<Filter[]>([]),[datasetName,setDatasetName]=useState(''),[outputFormat,setOutputFormat]=useState<'csv'|'json'>('csv'),[expiry,setExpiry]=useState('')
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('')
  const [downloadBusy,setDownloadBusy]=useState(false),[downloadReview,setDownloadReview]=useState<DownloadReview|null>(null)
  const [queryError,setQueryError]=useState(''),[queryNotice,setQueryNotice]=useState('')
  const [sampleDecisionOpen,setSampleDecisionOpen]=useState(false)
  const [skipSampleGeneration,setSkipSampleGeneration]=useState(false)
  const [queryRepair,setQueryRepair]=useState<SqlRepair|null>(null)
  const [generationFeedback,setGenerationFeedback]=useState('')
  useEffect(()=>{setRequirementsOpen(false)},[step])
  const [schemaClock,setSchemaClock]=useState(Date.now())
  const [schemaFeedback,setSchemaFeedback]=useState('')
  const [savedPlans,setSavedPlans]=useState<Plan[]>([])
  const [quarantiningPlanId,setQuarantiningPlanId]=useState<string|null>(null)
  const [quarantineConfirmation,setQuarantineConfirmation]=useState<{plan:Plan;datasetId?:string}|null>(null)
  const [quarantineError,setQuarantineError]=useState('')
  const [quarantinedPlanIds,setQuarantinedPlanIds]=useState<Set<string>>(new Set())
  const [datasetReferences,setDatasetReferences]=useState<Record<string,string>>({})
  const [datasetReferencesByName,setDatasetReferencesByName]=useState<Record<string,string>>({})
  const [excludedDatasetIds,setExcludedDatasetIds]=useState<Set<string>>(new Set())
  const [draftDatasetIds,setDraftDatasetIds]=useState<Set<string>>(new Set())
  const [draftDatasetId,setDraftDatasetId]=useState('')
  const [editDatasetId,setEditDatasetId]=useState('')
  const [editBaseVersionNumber,setEditBaseVersionNumber]=useState<number|null>(null)
  const draftPlanCreations=useRef(new Map<string,Promise<Plan>>())
  const source=sources.find(item=>item.id===sourceId)
  const mappedObjects=[...new Set(mapping.map(field=>field.source_object).filter((value):value is string=>Boolean(value)))]
  const queryOutputFields=mapping
  const previewSignature=(query:string)=>JSON.stringify({mapping,sourceJoins,filters,mode,sourceId,selectedSourceObjects,volume,query,generationFeedback})
  const querySignature=previewSignature(queryText)
  const sampleGenerationStatus=plan?.logical_plan.sample_generation_status
  const sampleGenerationInProgress=sampleGenerationStatus?.status==='running'
  const scenarioJobInProgress=['queued','running'].includes(plan?.job?.status??'')
  const generationFailed=plan?.state==='failed'||plan?.job?.status==='failed'
  const generationInProgress=!generationFailed&&(busy||sampleGenerationInProgress||plan?.logical_plan.schema_generation_status?.status==='running'||scenarioJobInProgress||(!plan?.job&&plan?.state==='running'))
  const canRetryFailedGeneration=Boolean(generationFailed&&plan?.dataset_id&&(plan.generation_retryable??(draftDatasetId===plan.dataset_id)))
  const generationFailureSummary=plan?.job?.safe_summary?.includes('stopped before completing the synthetic data')
    ?'The model response reached its output limit before completing the records. Retry generation: TIGR now retries with smaller row batches while keeping all reviewed fields together. If it still fails, shorten the correction requirement or review the field descriptions.'
    :plan?.job?.safe_summary??'TIGR could not complete data generation. Review the scenario configuration and retry.'
  const emptySyntheticPreview=Boolean(preview&&mode==='synthetic'&&preview.sample_rows.length===0)
  const requirementCriteria=plan?.logical_plan.requirement_intent?.criteria??[]
  const schemaGenerationStatus=plan?.logical_plan.schema_generation_status
  const coverageById=new Map((plan?.logical_plan.criteria_coverage??[]).map(item=>[item.criterion_id,item]))
  const acceptedCriteria=new Set(plan?.logical_plan.accepted_uncovered_criteria??[])
  const coverageComplete=mode!=='source_backed'||(requirementCriteria.length>0&&requirementCriteria.every(item=>coverageById.get(item.id)?.covered===true||acceptedCriteria.has(item.id)))

  async function refreshPlan(id=plan?.id) {if(id)setPlan(await apiRequest<Plan>(`/scenario-plans/${id}`,role))}
  async function ensureScenarioDraftPlan(dataset:DraftSummary,plans:Plan[]):Promise<Plan>{
    const linked=plans.find(item=>scenarioPlanDatasetIds(item).includes(dataset.id))
    if(linked)return linked
    const pending=draftPlanCreations.current.get(dataset.id)
    if(pending)return pending
    const settings=dataset.latest_version.generation_settings??{}
    const draftData={draft_dataset_id:dataset.id,dataset_name:dataset.name,output_format:settings.output_format==='json'?'json':'csv',expiry_at:dataset.latest_version.expiry_at??'',step:0,manual_schema:false,schema_feedback:'',generation_correction_feedback:'',query_text:'',query_edited:false,mapping:[],filters:[],relationships:[],source_joins:[],selected_source_objects:[]}
    const creation=apiRequest<Plan>(`/workspaces/${workspaceId}/scenario-plans`,role,{method:'POST',body:JSON.stringify({name:dataset.name,requirement:'Describe the test data requirements for this scenario.',target_use:dataset.description??String(settings.target_use??''),volume:Number(settings.maximum_records??100),draft_data:draftData})})
    draftPlanCreations.current.set(dataset.id,creation)
    try{return await creation}finally{draftPlanCreations.current.delete(dataset.id)}
  }
  useEffect(()=>{if(workspaceId&&role==='Test Data Engineer'){
    apiRequest<Source[]>(`/workspaces/${workspaceId}/scenario-sources`,role).then(setSources).catch(err=>setError(message(err)))
    const loadDataSets=async()=>{
      const items:DraftSummary[]=[]
      let page=1
      while(true){
        const result=await apiRequest<{items:DraftSummary[];has_more:boolean}>(`/datasets?workspace_id=${encodeURIComponent(workspaceId)}&creation_pathway=scenario_to_data&page=${page}&page_size=100`,role)
        items.push(...result.items)
        if(!result.has_more)return items
        page+=1
      }
    }
    void Promise.all([apiRequest<Plan[]>(`/workspaces/${workspaceId}/scenario-plans`,role),loadDataSets()]).then(async([plans,datasets])=>{
      const newestByName=new Map<string,DraftSummary>()
      for(const item of [...datasets].sort((a,b)=>new Date(b.created_at??0).getTime()-new Date(a.created_at??0).getTime())){
        const name=item.name.trim().toLocaleLowerCase()
        if(name&&!newestByName.has(name))newestByName.set(name,item)
      }
      const shouldExclude=(item:DraftSummary)=>item.latest_version.lifecycle_state==='published'||item.latest_version.lifecycle_state==='quarantined'
      setDatasetReferences(Object.fromEntries(datasets.map(item=>[item.id,item.reference_code])))
      setDatasetReferencesByName(Object.fromEntries([...newestByName].map(([name,item])=>[name,item.reference_code])))
      const linkedIds=new Set(plans.flatMap(scenarioPlanDatasetIds))
      const unlinked=datasets.filter(item=>item.latest_version.lifecycle_state==='draft'&&item.latest_version.provenance.origin==='not_set'&&!item.latest_version.schema_scope.length&&!item.latest_version.selected_fields.length&&item.latest_version.record_count===null&&!linkedIds.has(item.id))
      const recovered=await Promise.all(unlinked.map(item=>ensureScenarioDraftPlan(item,plans)))
      const excludedIds=new Set(datasets.filter(shouldExclude).map(item=>item.id))
      const draftIds=new Set(datasets.filter(item=>item.latest_version.lifecycle_state==='draft').map(item=>item.id))
      setExcludedDatasetIds(excludedIds)
      setDraftDatasetIds(draftIds)
      setSavedPlans(rows=>dedupeScenarioPlans([...recovered,...plans,...rows.filter(item=>!recovered.some(plan=>plan.id===item.id)&&!plans.some(plan=>plan.id===item.id))]))
    }).catch(()=>undefined)
  }},[workspaceId,role])
  useEffect(()=>{
    let active=true
    const routeParams=new URLSearchParams(location.search)
    const editPlanId=routeParams.get('edit_plan_id')
    if(editPlanId){
      const targetDatasetId=routeParams.get('edit_dataset_id')??''
      const baseVersion=Number(routeParams.get('base_version_number'))
      setEditDatasetId(targetDatasetId);setEditBaseVersionNumber(Number.isInteger(baseVersion)&&baseVersion>0?baseVersion:null)
      setDraftDatasetId('')
      apiRequest<Plan>(`/scenario-plans/${encodeURIComponent(editPlanId)}`,role).then(saved=>{
        if(!active)return
        resume(saved)
        setDraftDatasetId('');setStep(0)
        setDatasetName(saved.logical_plan?.draft_data?.dataset_name as string||saved.name)
        setVolume(typeof saved.logical_plan?.draft_data?.record_count_input==='string'?saved.logical_plan.draft_data.record_count_input:saved.volume>0?String(saved.volume):'')
        setNotice('Edit the data set requirements and continue through the review steps. A fresh version will be generated at the final step.')
      }).catch(err=>{if(active)setError(message(err))})
      return()=>{active=false}
    }
    setEditDatasetId('');setEditBaseVersionNumber(null)
    const draftId=routeParams.get('draft_id')
    if(!draftId){setDraftDatasetId('');return()=>{active=false}}
    apiRequest<DraftDetail>(`/datasets/${encodeURIComponent(draftId)}`,role).then(async draft=>{
      if(!active)return
      if(draft.data_set.workspace_id!==workspaceId||draft.data_set.creation_pathway!=='scenario_to_data'||draft.selected_version.lifecycle_state!=='draft')throw Error('The selected '+SCENARIO_TO_DATA_LABEL+' draft is unavailable in this workspace')
      const settings=draft.selected_version.generation_settings??{}
      const plans=await apiRequest<Plan[]>(`/workspaces/${workspaceId}/scenario-plans`,role)
      if(!active)return
      const draftSummary:DraftSummary={id:draftId,reference_code:datasetReferences[draftId]??'',name:draft.data_set.name,description:draft.data_set.description,latest_version:{...draft.selected_version,schema_scope:[],selected_fields:[],provenance:{origin:'not_set'},record_count:null,generation_settings:settings}}
      const scenarioPlan=await ensureScenarioDraftPlan(draftSummary,plans)
      if(!active)return
      setDraftDatasetId(draftId)
      setSavedPlans(rows=>[scenarioPlan!,...rows.filter(item=>item.id!==scenarioPlan!.id)].slice(0,50))
      resume(scenarioPlan!)
    }).catch(err=>{if(active)setError(message(err))})
    return()=>{active=false}
  },[location.search,role,workspaceId])
  useEffect(()=>{if(mode==='source_backed'&&source)setSelectedSourceObjects(current=>current.length?current:source.objects.map(item=>item.name))},[mode,sourceId,sources])
  useEffect(()=>{if(schemaGenerationStatus?.status!=='running')return;setBusy(true);const poll=window.setInterval(()=>void refreshPlan().catch(()=>undefined),900);return()=>window.clearInterval(poll)},[schemaGenerationStatus?.status,plan?.id,role])
  useEffect(()=>{
    if(schemaGenerationStatus?.status!=='running'&&sampleGenerationStatus?.status!=='running')return
    const clock=window.setInterval(()=>setSchemaClock(Date.now()),1000)
    return()=>window.clearInterval(clock)
  },[schemaGenerationStatus?.status,sampleGenerationStatus?.status,schemaGenerationStatus?.generation_id,sampleGenerationStatus?.generation_id])
  useEffect(()=>{if(!schemaGenerationStatus||schemaGenerationStatus.status==='running')return;setBusy(false);if(schemaGenerationStatus.status==='failed')setError(schemaGenerationStatus.error??'Schema generation failed. Retry and review the approved source metadata.')},[schemaGenerationStatus?.status,schemaGenerationStatus?.generation_id])
  useEffect(()=>{if(schemaGenerationStatus?.status==='complete'&&mode==='synthetic'&&plan?.mapping.length)setMapping(plan.mapping.map(field=>({...field,required:true})))},[schemaGenerationStatus?.status,schemaGenerationStatus?.generation_id,mode,plan?.mapping])
  useEffect(()=>{
    if(!sampleGenerationInProgress||!plan?.id)return
    const planId=plan.id
    const poll=window.setInterval(()=>void refreshPlan(planId).catch(()=>undefined),1000)
    return()=>window.clearInterval(poll)
  },[sampleGenerationStatus?.status,sampleGenerationStatus?.generation_id,plan?.id,role])
  useEffect(()=>{
    if(!sampleGenerationStatus||sampleGenerationStatus.status==='running')return
    setBusy(false)
    setQueryAction(null)
    if(sampleGenerationStatus.status==='failed'){
      setQueryError(sampleGenerationStatus.error??'Sample generation failed. Review the schema and generation requirements, then retry.')
      setQueryNotice('')
    }
    const generatedQuery=plan?.logical_plan.generated_query
    if(generatedQuery)setQueryText(generatedQuery)
    const generated=plan?.logical_plan.synthetic_preview
    if(generated&&!generated.sample_error){
      setPreview({...generated,_signature:previewSignature(queryText)})
      if(sampleGenerationStatus.status==='complete'){
        setQueryError('')
        setQueryNotice(`Sample generation completed in ${((sampleGenerationStatus.elapsed_ms??0)/1000).toFixed(1)} seconds.`)
      }
    }else if(sampleGenerationStatus.status==='complete'){
      setQueryError('')
      setQueryNotice(`SQL review completed in ${((sampleGenerationStatus.elapsed_ms??0)/1000).toFixed(1)} seconds.`)
    }
  },[sampleGenerationStatus?.status,sampleGenerationStatus?.generation_id,plan?.logical_plan.synthetic_preview,querySignature])
  useEffect(()=>{if(!plan?.job||!['queued','running'].includes(plan.job.status))return;const timer=window.setInterval(()=>void refreshPlan().catch(()=>undefined),1200);return()=>window.clearInterval(timer)},[plan?.job?.status,plan?.id,role])

  async function saveRequirement() {
    const predicates=filters.filter(item=>item.field&&item.operator&&filterHasValue(item)).map(({field,operator,value})=>({field,operator,value:operator==='is_null'?null:parseValue(value)}))
    const rootObject=mapping.find(field=>field.source_object)?.source_object??sourceObject
    const completeMapping=mapping.filter(field=>field.source_field?.trim()&&field.target_field?.trim()&&field.data_type?.trim()).map(field=>({...field,required:true}))
    const completeRelationships=relationships.filter(item=>item.parent_field?.trim()&&item.child_field?.trim())
    const completeJoins=sourceJoins.filter(item=>item.left_object?.trim()&&item.left_field?.trim()&&item.right_object?.trim()&&item.right_field?.trim()).map(({left_object,left_field,right_object,right_field})=>({left_object,left_field,right_object,right_field}))
    const body={name,requirement,expected_states:splitLines(expected),exclusions:splitLines(exclusions),target_use:targetUse,volume:Number(volume)||100,
      generation_mode:mode,source_connection_id:mode==='source_backed'?(sourceId||null):null,source_object:mode==='source_backed'?(rootObject||null):null,
      mapping:completeMapping,predicates,relationships:completeRelationships,source_joins:completeJoins,
      draft_data:{draft_dataset_id:draftDatasetId||undefined,edit_dataset_id:editDatasetId||undefined,base_version_number:editBaseVersionNumber??undefined,test_data_request_id:testDataRequestId||undefined,dataset_name:step===0?name:(datasetName||name),output_format:outputFormat,expiry_at:expiry||null,step,manual_schema:manualSchema,schema_feedback:schemaFeedback,generation_correction_feedback:generationFeedback,query_text:queryText,query_edited:queryEdited,query_preview:preview??null,sample_generation_skipped:skipSampleGeneration,record_count_input:volume,mapping,filters,relationships,source_joins:sourceJoins,selected_source_objects:selectedSourceObjects}}
    if(plan)return apiRequest<Plan>(`/scenario-plans/${plan.id}`,role,{method:'PATCH',body:JSON.stringify(body)})
    const created=await apiRequest<Plan>(`/workspaces/${workspaceId}/scenario-plans`,role,{method:'POST',body:JSON.stringify({name,requirement,expected_states:splitLines(expected),exclusions:splitLines(exclusions),target_use:targetUse,volume:Number(volume)||100})})
    return apiRequest<Plan>(`/scenario-plans/${created.id}`,role,{method:'PATCH',body:JSON.stringify(body)})
  }
  async function saveMapping() {
    if(!plan)throw Error('Save the scenario requirement first')
    const rootObject=mapping.find(field=>field.source_object)?.source_object??sourceObject
    const predicates=filters.filter(item=>item.field&&filterHasValue(item)).map(({field,operator,value})=>({field,operator,value:operator==='is_null'?null:parseValue(value)}))
    const joins=sourceJoins.map(({left_object,left_field,right_object,right_field})=>({left_object,left_field,right_object,right_field}))
    return apiRequest<Plan>(`/scenario-plans/${plan.id}`,role,{method:'PATCH',body:JSON.stringify({generation_mode:mode,source_connection_id:mode==='source_backed'?sourceId:null,source_object:mode==='source_backed'?rootObject:null,mapping:mapping.map(field=>({...field,required:true})),predicates,relationships,source_joins:joins,accepted_uncovered_criteria:plan.logical_plan.accepted_uncovered_criteria??[]})})
  }
  async function setCriterionAccepted(criterionId:string,accepted:boolean) {
    if(!plan)return
    const current=plan.logical_plan.accepted_uncovered_criteria??[]
    const next=accepted?[...new Set([...current,criterionId])]:current.filter(id=>id!==criterionId)
    setBusy(true);setError('')
    try{const saved=await apiRequest<Plan>(`/scenario-plans/${plan.id}`,role,{method:'PATCH',body:JSON.stringify({accepted_uncovered_criteria:next})});setPlan(saved);setSavedPlans(rows=>rows.map(item=>item.id===saved.id?saved:item))}catch(err){setError(message(err))}finally{setBusy(false)}
  }
  async function saveDraftProgress(planId:string,draftStep:number,sampleSkipped=skipSampleGeneration) {
    const saved=await apiRequest<Plan>(`/scenario-plans/${planId}`,role,{method:'PATCH',body:JSON.stringify({draft_data:{draft_dataset_id:draftDatasetId||undefined,edit_dataset_id:editDatasetId||undefined,base_version_number:editBaseVersionNumber??undefined,test_data_request_id:testDataRequestId||undefined,dataset_name:step===0?name:(datasetName||name),output_format:outputFormat,expiry_at:expiry||null,step:draftStep,manual_schema:manualSchema,schema_feedback:schemaFeedback,generation_correction_feedback:generationFeedback,query_text:queryText,query_edited:queryEdited,query_preview:preview??null,sample_generation_skipped:sampleSkipped,record_count_input:volume,mapping,filters,relationships,source_joins:sourceJoins,selected_source_objects:selectedSourceObjects}})})
    setPlan(saved);setSavedPlans(rows=>[saved,...rows.filter(item=>item.id!==saved.id)].slice(0,50));return saved
  }
  async function saveQuery(useCurrentSql=false,includePreview=true,queryOverride?:string) {
    if(!plan)throw Error('Save the scenario requirement first')
    const sql=queryOverride??queryText
    let appliedRepair:SqlRepair|null=null
    const saved=await saveMapping()
    setPlan(saved)
    let builtQuery:string|null=null
    if(mode==='source_backed'){
      if(useCurrentSql&&!sql.trim())throw Error('Generate SQL before validating it.')
      const generated=await apiRequest<{plan:Plan;query:string;model:string}>(`/scenario-plans/${plan.id}/query`,role,{method:'POST',body:JSON.stringify(useCurrentSql?{query:sql}:{})})
      setPlan(generated.plan)
      builtQuery=generated.query
    } else {
      if(!sql.trim())throw Error('Enter a query against synthetic_data to generate a sample preview.')
      const generated=await apiRequest<{status?:'running';generation_id?:string;job_id?:string;plan?:Plan;query:string|null;model?:string;validation_repair?:SqlRepair|null}>(`/scenario-plans/${plan.id}/query`,role,{method:'POST',body:JSON.stringify({query:sql,background_sample:includePreview,correction_feedback:generationFeedback})})
      if(generated.status==='running'&&generated.generation_id){
        setQueryText(sql)
        setQueryEdited(false)
        setPreview(null)
        setPlan(current=>current?{...current,logical_plan:{...current.logical_plan,sample_generation_status:{generation_id:generated.generation_id!,job_id:generated.job_id,status:'running',stage:'reviewing_sql',include_sample:includePreview}}}:current)
        return appliedRepair
      }
      if(!generated.plan)throw Error('The SQL review job could not be started.')
      setPlan(generated.plan)
      builtQuery=generated.query
      appliedRepair=generated.validation_repair??null
      setQueryRepair(appliedRepair)
      if(generated.validation_repair)setQueryText(generated.query??queryText)
    }
    if(!includePreview){setQueryText(builtQuery??sql);setQueryEdited(false);return appliedRepair}
    const result=await apiRequest<Preview|PreviewStart>(`/scenario-plans/${plan.id}/preview`,role,{method:'POST',...(mode==='synthetic'?{body:JSON.stringify({correction_feedback:generationFeedback})}:{})})
    if('status' in result&&result.status==='running'){
      setQueryText(builtQuery??sql)
      setQueryEdited(false)
      setPreview(null)
      setPlan(current=>current?{...current,logical_plan:{...current.logical_plan,sample_generation_status:{generation_id:result.generation_id,job_id:result.job_id,status:'running',stage:'generating_sample'}}}:current)
      return appliedRepair
    }
    const query=('query' in result?result.query:null)??builtQuery??''
    const editorQuery=mode==='source_backed'?query:(builtQuery??query)
    setQueryText(editorQuery)
    setQueryEdited(false)
    setPreview({...result as Preview,_signature:previewSignature(editorQuery)})
    return appliedRepair
  }
  async function runQueryAction(useCurrentSql:boolean,regenerating=false) {
    const validatingSql=useCurrentSql
    if(mode==='synthetic'&&!validatingSql)setSkipSampleGeneration(false)
    setError('');setNotice('')
    setBusy(true);setPreview(null);setQueryRepair(null);setQueryAction(regenerating?'regenerate':useCurrentSql?'validate':'generate');setQueryError('');setQueryNotice(regenerating?'Applying your correction requirement to the reviewed schema and generating a fresh sample.':validatingSql?(mode==='source_backed'?'Validating SQL against the connected PostgreSQL source.':'Validating SQL against the reviewed synthetic schema.'):(mode==='source_backed'?'Generating SQL from the selected source fields, joins, and filters.':'Asking TIGR to generate data guided by your SQL.'))
    try{if(mode==='synthetic'&&!validatingSql&&plan)await saveDraftProgress(plan.id,3,false);const repair=await saveQuery(useCurrentSql,mode==='synthetic'&&!validatingSql);setQueryNotice(repair?'TIGR fixed the SQL syntax. The corrected query is loaded in the editor.':validatingSql?(mode==='source_backed'?'SQL is valid for the connected source.':'SQL syntax is valid against the reviewed fields. Generate a sample to see the output.'):(mode==='source_backed'?'SQL generated from the reviewed schema.':regenerating?'TIGR is regenerating sample data in the background using your correction.':'TIGR is generating a sample in the background using your reviewed schema and SQL requirements.'))}
    catch(err){setQueryError(message(err));setQueryNotice('')}
    finally{setBusy(false);setQueryAction(null)}
  }
  async function nextStep(skipOptionalSample=false) {
    const sampleCurrent=Boolean(preview&&preview._signature===querySignature&&!queryEdited&&!preview.sample_error)
    const skipSample=skipOptionalSample||skipSampleGeneration
    if(step===2&&mode==='synthetic'&&!skipSample&&!sampleCurrent){setSampleDecisionOpen(true);return}
    setBusy(true);setError('');setNotice('')
    if(step===2){setQueryError('');setQueryNotice('')}
    let progressPlanId=plan?.id
    try {
      if(step===0){if(mode==='source_backed'&&!selectedSourceObjects.length)throw Error('Select at least one source object for this scenario.');const saved=await saveRequirement();progressPlanId=saved.id;setPlan(saved);setQueryText('');setQueryEdited(false);setQueryRepair(null);setQueryError('');setQueryNotice('');setPreview(null);setDatasetName(current=>current||saved.name);if(saved.clarifying_questions.length){setNotice('Answer the clarification prompts before continuing.');return}}
      if(step===1){if(mode==='source_backed'&&!mapping.length)throw Error('Select at least one suggested source field before continuing.');if(mode==='source_backed'&&!coverageComplete)throw Error('Map each uncovered requirement criterion or accept its gap in Step 2 before continuing.');const saved=await saveMapping();progressPlanId=saved.id;setPlan(saved);if(saved.clarifying_questions.length){setStep(0);setNotice('Complete the requirement clarifications before continuing.');return}setPreview(null);setQueryRepair(null);setQueryError('');setQueryEdited(false);if(mode==='synthetic'){if(!mapping.length)throw Error('Add at least one output field before continuing.');const sql=syntheticQueryFor(mapping);setQueryText(sql);setQueryNotice('SQL refreshed for the selected output fields. Click Generate sample when you want TIGR to create sample data.');setNotice('SQL refreshed for the selected fields. Generate a sample in Query Development when you are ready.')}else{setQueryText('');const generated=await apiRequest<{plan:Plan;query:string}>(`/scenario-plans/${saved.id}/query`,role,{method:'POST',body:JSON.stringify({})});progressPlanId=generated.plan.id;setPlan(generated.plan);setQueryText(generated.query);setQueryNotice('SQL refreshed for the selected source fields. Continue to Step 4 to generate the data set.');setNotice('SQL refreshed from the current source field mapping.')}}
      if(step===2&&!skipSample&&mode==='synthetic'&&(!preview||preview._signature!==querySignature||queryEdited))throw Error('Generate sample data for this SQL before continuing, or choose to skip sample generation.')
      if(step===2&&mode==='source_backed'&&queryEdited)await saveQuery(true,false)
      if(step===2&&mode==='synthetic'&&skipSample&&queryEdited)await saveQuery(true,false)
      if(!progressPlanId)throw Error('Save the scenario requirement before continuing.')
      await saveDraftProgress(progressPlanId,Math.min(3,step+1),step===2&&skipSample?true:skipSampleGeneration);setNotice('Draft saved. Your progress is available in Saved scenario plans.')
      if(step===2&&skipSample){setSkipSampleGeneration(true);setPreview(null)}
      setStep(value=>Math.min(3,value+1))
    } catch(err){if(step===2){setQueryError(message(err));setQueryNotice('')}else setError(message(err))} finally{setBusy(false)}
  }
  function generateOptionalSample(){if(mode!=='synthetic')return;setSampleDecisionOpen(false);setSkipSampleGeneration(false);void runQueryAction(false,true)}
  async function saveDraft() {
    setBusy(true);setError('');setNotice('')
    try{const saved=await saveRequirement();setPlan(saved);setSavedPlans(rows=>[saved,...rows.filter(item=>item.id!==saved.id)].slice(0,50));setNotice('Scenario draft saved.')}
    catch(err){setError(message(err))}finally{setBusy(false)}
  }
  async function quarantineSavedDraft(saved:Plan,datasetId?:string){
    setQuarantiningPlanId(saved.id);setQuarantineError('');setError('');setNotice('')
    try{
      const result=await apiRequest<Plan>(`/scenario-plans/${saved.id}/quarantine`,role,{method:'POST'})
      if(result.state!=='quarantined')throw Error('TIGR did not confirm that the scenario draft was quarantined. Refresh the plan list and retry.')
      setSavedPlans(rows=>rows.filter(item=>item.id!==saved.id))
      setQuarantinedPlanIds(current=>new Set([...current,saved.id]))
      if(datasetId)setExcludedDatasetIds(current=>new Set([...current,datasetId]))
      setPlan(current=>current?.id===saved.id?null:current)
      setDraftDatasetId(current=>datasetId&&current===datasetId?'':current)
      setQuarantineConfirmation(null)
      setNotice(datasetId?'Draft version moved to Quarantined.':'Saved scenario draft moved to Quarantined.')
    }catch(err){const reason=message(err);setQuarantineError(reason);setError(reason)}finally{setQuarantiningPlanId(null)}
  }
  function resume(saved:Plan){const draft=saved.logical_plan?.draft_data??{};setPlan(saved);setDraftDatasetId(typeof draft.draft_dataset_id==='string'?draft.draft_dataset_id:(saved.state==='failed'&&saved.dataset_id?saved.dataset_id:''));setName(saved.name);setDatasetName(typeof draft.dataset_name==='string'?draft.dataset_name:saved.name);setTestDataRequestId(typeof draft.test_data_request_id==='string'?draft.test_data_request_id:'');setRequirement(saved.logical_plan?.draft_data?.draft_dataset_id&&saved.requirement==='Describe the test data requirements for this scenario.'?'':saved.requirement);setExpected(saved.expected_states.join('\n'));setExclusions(saved.exclusions.join('\n'));setTargetUse(saved.target_use);setVolume(typeof draft.record_count_input==='string'?draft.record_count_input:saved.volume>0?String(saved.volume):'');setMode(saved.generation_mode);setSourceId(saved.source_connection_id??'');setSourceObject(saved.source_object??'');setSelectedSourceObjects(Array.isArray(draft.selected_source_objects)?draft.selected_source_objects as string[]:(saved.logical_plan?.selected_source_objects??[]));setMapping(Array.isArray(draft.mapping)?draft.mapping as FieldMap[]:saved.mapping);setSchemaFeedback(typeof draft.schema_feedback==='string'?draft.schema_feedback:(saved.logical_plan?.schema_generation_feedback??''));setGenerationFeedback(typeof draft.generation_correction_feedback==='string'?draft.generation_correction_feedback:(saved.logical_plan?.generation_correction_feedback??''));setRelationships(Array.isArray(draft.relationships)?draft.relationships as Relationship[]:(saved.logical_plan?.relationships??[]));setSourceJoins(Array.isArray(draft.source_joins)?draft.source_joins as SourceJoin[]:(saved.logical_plan?.source_joins??[]));setFilters(Array.isArray(draft.filters)?draft.filters as Filter[]:(saved.logical_plan?.predicates??[]).map(item=>({field:item.field,operator:item.operator,value:String(item.value??'')})));setQueryText(typeof draft.query_text==='string'?draft.query_text:(saved.logical_plan?.generated_query??''));setQueryEdited(draft.query_edited===true);setSkipSampleGeneration(draft.sample_generation_skipped===true);setOutputFormat(draft.output_format==='json'?'json':'csv');setExpiry(typeof draft.expiry_at==='string'?draft.expiry_at:'');setManualSchema(draft.manual_schema===true);setQueryRepair(null);setPreview(saved.generation_mode==='synthetic'&&draft.query_preview&&typeof draft.query_preview==='object'?draft.query_preview as Preview:null);setStep(saved.dataset_id?3:(typeof draft.step==='number'?Math.max(0,Math.min(3,draft.step)):0));setRequirementsOpen(false);setNotice('Saved scenario plan reopened.')}
  async function startGeneration(event:FormEvent) {
    event.preventDefault();if(!plan)return
    const requestedCount=Number(volume)
    if(datasetName.trim().length<2){setError('Enter a data set name with at least 2 characters.');return}
    if(!targetUse.trim()){setError('Enter the target use for this data set.');return}
    if(!Number.isInteger(requestedCount)||requestedCount<1||requestedCount>10000){setError('Enter a whole record count from 1 to 10,000.');return}
    setBusy(true);setError('');setNotice('')
    try{if(mode==='source_backed'&&!canRetryFailedGeneration&&(queryEdited||!queryText.trim()))await saveQuery(queryEdited,false);const queued=await apiRequest<{job_id:string;status:string;dataset_id:string;dataset_reference_code:string;version_number:number}>(`/scenario-plans/${plan.id}/generate`,role,{method:'POST',body:JSON.stringify({dataset_name:datasetName,target_use:targetUse,maximum_records:requestedCount,output_format:outputFormat,expiry_at:expiry?new Date(expiry).toISOString():null,test_data_request_id:testDataRequestId||undefined,draft_dataset_id:canRetryFailedGeneration?plan.dataset_id:(draftDatasetId||undefined),edit_dataset_id:editDatasetId||undefined,base_version_number:editBaseVersionNumber??undefined})});setDraftDatasetId(queued.dataset_id);setEditDatasetId('');setEditBaseVersionNumber(null);setPlan(current=>current?{...current,state:'running',dataset_id:queued.dataset_id,dataset_reference_code:queued.dataset_reference_code,job:{id:queued.job_id,status:queued.status,progress:0,safe_summary:`Generation queued for version ${queued.version_number}`}}:current);await refreshPlan();setNotice('Generation started. Job state will update here.')}
    catch(err){setError(message(err))}finally{setBusy(false)}
  }
  async function suggestMapping() {
    if(!plan)return
    if(mode==='source_backed'&&!selectedSourceObjects.length){setError('Select at least one source object for schema mapping.');return}
    setBusy(true);setError('');setNotice('');setMapping([]);setSourceObject('');setSourceJoins([]);setFilters([]);setRelationships([]);setQueryText('');setQueryEdited(false);setQueryRepair(null);setQueryError('');setQueryNotice('');setPreview(null)
    setPlan(current=>current?{...current,state:'draft',mapping:[],suggested_mapping:[],logical_plan:{...current.logical_plan,predicates:[],relationships:[],source_joins:[],suggested_source_joins:[],suggested_filters:[],schema_generation_notes:[],requirement_intent:undefined,criteria_coverage:[],schema_generation_feedback:schemaFeedback,generated_query:undefined,generated_query_fields:undefined,generated_query_field_types:undefined}}:current)
    try{const saved=await apiRequest<Plan>(`/scenario-plans/${plan.id}/schema`,role,{method:'POST',body:JSON.stringify({generation_mode:mode,source_connection_id:mode==='source_backed'?sourceId:null,selected_source_objects:mode==='source_backed'?selectedSourceObjects:[],feedback:schemaFeedback})});setPlan(saved);setSchemaFeedback(saved.logical_plan.schema_generation_feedback??schemaFeedback);setMapping(mode==='source_backed'?[]:saved.mapping.map(field=>({...field,required:true})));setSourceObject(saved.source_object??'');setSourceJoins([]);setFilters([]);setQueryText('');setQueryEdited(false);setPreview(null);setDatasetName(current=>current||saved.name)}
    catch(err){setError(message(err));setBusy(false)}
  }
  async function download() {
    if(!plan?.dataset_id)return
    setError('')
    try {
      const dataset=await apiRequest<{data_set:{name:string;reference_code:string};selected_version:{version_number:number}}>(`/datasets/${plan.dataset_id}`,role)
      const versionNumber=dataset.selected_version.version_number
      const access=await apiRequest<DownloadAccess>(`/datasets/${plan.dataset_id}/versions/${versionNumber}/download-access`,role)
      const review:DownloadReview={datasetId:plan.dataset_id,datasetName:dataset.data_set.name,referenceCode:dataset.data_set.reference_code,versionNumber,format:outputFormat,access}
      if(access.blocked_record_count>0||!access.can_download){setDownloadReview(review);return}
      await saveScenarioArtifact(review)
    } catch(err){setError(message(err))}
  }
  async function saveScenarioArtifact(review:DownloadReview){
    if(!plan?.id)return
    setDownloadBusy(true)
    try{const result=await apiDownload(`/scenario-plans/${plan.id}/artifact`,role);const url=URL.createObjectURL(result.blob);const anchor=document.createElement('a');anchor.href=url;anchor.download=result.filename??`${review.referenceCode}.${review.format}`;anchor.click();URL.revokeObjectURL(url);setDownloadReview(null)}catch(err){setError(message(err))}finally{setDownloadBusy(false)}
  }
  function editField(index:number,key:keyof FieldMap,value:string|boolean){setMapping(rows=>rows.map((row,i)=>i===index?{...row,[key]:value}:row))}
  function addNewField(){setMapping(rows=>{const nextNumber=rows.reduce((maximum,field)=>{const match=/^newfield(\d+)$/i.exec(field.target_field.trim());return match?Math.max(maximum,Number(match[1])):maximum},0)+1;const fieldName=`newfield${nextNumber}`;return [...rows,{source_field:fieldName,target_field:fieldName,data_type:'TEXT',required:true,source_object:null}]})}
  function toggleSourceObject(objectName:string,checked:boolean){setSelectedSourceObjects(current=>checked?current.includes(objectName)?current:[...current,objectName]:current.filter(name=>name!==objectName));setMapping([]);setPreview(null);setQueryText('')}
  function addOutputField(){if(mode==='synthetic'){addNewField();return}setMapping(rows=>[...rows,{source_field:'',source_object:null,target_field:'',data_type:'TEXT',required:true,confidence:null}])}

  const visibleSavedPlans=dedupeScenarioPlans(savedPlans).filter(saved=>{
    if(quarantinedPlanIds.has(saved.id)||saved.state==='quarantined')return false
    const datasetIds=scenarioPlanDatasetIds(saved)
    const hasDraftDataset=datasetIds.some(id=>draftDatasetIds.has(id))
    if((saved.state==='running'||saved.state==='succeeded')&&!hasDraftDataset)return false
    return !datasetIds.some(id=>excludedDatasetIds.has(id))
  })

  if(role!=='Test Data Engineer')return <section className="panel scenario-empty"><Sparkles size={24}/><h2>Test Data Engineer access required</h2><p>Scenario plans can be created and reviewed by a Test Data Engineer in an active workspace.</p></section>
  if(!workspaceId)return <section className="panel scenario-empty"><Sparkles size={24}/><h2>Select a workspace</h2><p>Scenario plans use workspace approved schema metadata.</p></section>
  return <div className="scenario-page">
    <section className="scenario-hero panel"><div><div className="eyebrow"><ScenarioToDataLabel/></div><h2>Build a reviewed generation plan</h2><p>Describe the test scenario, review its schema and query, then generate a versioned data set.</p></div><div className="scenario-hero-actions"><div className="scenario-provider"><Sparkles size={16}/><span>Generation mode</span><strong>AI assisted</strong></div></div></section>
    {step===0&&visibleSavedPlans.length>0&&<section className="saved-plans-panel-v3 panel">
      <header className="saved-plans-heading-v3">
        <div><span className="eyebrow">YOUR WORK</span><h3>Saved scenario plans</h3><p>Pick up where you left off. Drafts created from this pathway appear here too.</p></div>
        <span className="saved-plans-count-v3">{visibleSavedPlans.length} saved</span>
      </header>
      <div className="saved-plans-rail-v3">
        {visibleSavedPlans.sort((a,b)=>new Date(b.updated_at).getTime()-new Date(a.updated_at).getTime()).map(saved=>{
          const draft=saved.logical_plan?.draft_data??{}
          const datasetName=typeof draft.dataset_name==='string'&&draft.dataset_name.trim()?draft.dataset_name.trim():saved.name
          const draftId=typeof draft.draft_dataset_id==='string'?draft.draft_dataset_id:saved.dataset_id
          const referenceCode=(draftId?datasetReferences[draftId]:undefined)||datasetReferencesByName[datasetName.trim().toLocaleLowerCase()]
          const resumeStep=saved.job||saved.dataset_id?3:(typeof draft.step==='number'?Math.max(0,Math.min(STEP_NAMES.length-1,draft.step)):0)
          const isDraftDataset=Boolean(draftId&&draftDatasetIds.has(draftId))
          const statusTone=saved.state==='failed'?'failed':saved.state==='draft'||isDraftDataset?'draft':'saved'
          return <SavedPlanCard
            key={saved.id}
            icon={saved.generation_mode==='synthetic'?<Sparkles size={19}/>:<Database size={19}/>}
            title={datasetName}
            titleAdornment={<DatasetActivityIcons datasetId={draftId}/>}
            pathway={saved.generation_mode==='synthetic'?'Synthetic':'Source-backed'}
            step={`Step ${resumeStep+1} · ${STEP_NAMES[resumeStep]}`}
            referenceCode={referenceCode||'Data set ID pending'}
            status={saved.state==='failed'?'Failed':saved.state==='draft'||isDraftDataset?'Draft':'Saved'}
            statusTone={statusTone}
            updatedAt={new Date(saved.updated_at).toLocaleDateString()}
            onResume={()=>resume(saved)}
            onQuarantine={()=>setQuarantineConfirmation({plan:saved,datasetId:draftId||undefined})}
            quarantineDisabled={quarantiningPlanId===saved.id}
          />
        })}
      </div>
    </section>}
    <section className="scenario-steps panel" aria-label="Scenario workflow steps">{STEP_NAMES.map((label,index)=><button type="button" key={label} className={index===step?'current':index<step?'complete':''} onClick={()=>index<step&&!generationInProgress&&setStep(index)} disabled={index>step||generationInProgress}><span>{index<step?<Check size={15}/>:index+1}</span><small>{label}</small></button>)}</section>
    <div className="scenario-step-overview"><div><div className="eyebrow">{step===0?'STEP 1 · REQUIREMENTS':step===1?'STEP 2 · SCHEMA MAPPING':step===2?'STEP 3 · QUERY DEVELOPMENT':'STEP 4 · DATA GENERATION'}</div><h2>{step===0?"Describe the data your test needs":step===1?"Review and edit the required fields":step===2?(mode==='source_backed'?"Review the SQL":"Review the SQL and sample output"):"Set up the data set"}</h2><p>{step===0?"Enter the scenario as a prompt. Choose synthetic generation or an approved source connection.":step===1?(mode==='source_backed'?"TIGR suggests source objects, fields, relationships, and filters using only the objects selected in Step 1. Review the suggestions and choose what applies.":"Your schema fields from Step 1 are carried forward. Add fields or define relationships here, then continue to Step 3 for sample data generation."):step===2?(mode==='source_backed'?"Review or edit PostgreSQL SQL for the selected source connection, then validate it.":"Enter a SQL query as requirements for TIGR to generate synthetic data. Validate the SQL or generate a sample preview."):"Choose the same delivery settings used when creating a data set draft."}</p></div><div className="scenario-step-overview-actions"><button type="button" className="button button-secondary" onClick={()=>void saveDraft()} disabled={busy||generationInProgress||Boolean(step===3&&plan&&plan.state!=='ready')}><Save size={15}/>Save draft</button>{step===1&&<button type="button" className="button button-confirm scenario-schema-generate" disabled={busy||!plan||(mode==='source_backed'&&(!sourceId||!selectedSourceObjects.length))||(mode==='synthetic'&&manualSchema)} onClick={()=>void suggestMapping()}><Sparkles size={15}/>{busy?'Generating…':plan?.logical_plan.requirement_intent?'Regenerate':'Generate'}</button>}</div></div>
    {(requirement.trim()||expected.trim()||exclusions.trim())?<details className="scenario-mode-panel" open={requirementsOpen} onToggle={event=>setRequirementsOpen(event.currentTarget.open)}><summary className={`scenario-mode-indicator is-${mode}`} aria-label={`Generation mode: ${mode==='synthetic'?'Synthetic':'Source-backed'}`}><span className="scenario-mode-indicator-icon">{mode==='synthetic'?<Sparkles size={16}/>:<Database size={16}/>}</span><div className="scenario-mode-indicator-context"><span><small>{(draftDatasetId&&datasetReferences[draftDatasetId])|| (plan?.dataset_id&&datasetReferences[plan.dataset_id]) || datasetReferencesByName[(datasetName.trim()||name.trim()||plan?.name||'').toLocaleLowerCase()] || 'ID not assigned'}</small><strong>{datasetName.trim()||name.trim()||plan?.name||'Untitled scenario'}<DatasetActivityIcons datasetId={draftDatasetId||plan?.dataset_id}/></strong></span>{mode==='source_backed'&&<span><small>Source connection</small><strong>{source?.name??'Not selected'}</strong></span>}</div><span className="scenario-mode-indicator-copy"><small>Generation mode</small><strong>{mode==='synthetic'?'Synthetic':'Source-backed'}</strong></span></summary><div className="scenario-requirement-content">{requirement.trim()&&<section><strong>Test data requirements</strong><p>{requirement}</p></section>}{expected.trim()&&<section><strong>Expected states</strong><p>{expected}</p></section>}{exclusions.trim()&&<section><strong>Exclusions</strong><p>{exclusions}</p></section>}</div></details>:<div className="scenario-mode-panel"><div className={`scenario-mode-indicator is-${mode}`} role="status" aria-label={`Generation mode: ${mode==='synthetic'?'Synthetic':'Source-backed'}`}><span className="scenario-mode-indicator-icon">{mode==='synthetic'?<Sparkles size={16}/>:<Database size={16}/>}</span><div className="scenario-mode-indicator-context"><span><small>{(draftDatasetId&&datasetReferences[draftDatasetId])|| (plan?.dataset_id&&datasetReferences[plan.dataset_id]) || datasetReferencesByName[(datasetName.trim()||name.trim()||plan?.name||'').toLocaleLowerCase()] || 'ID not assigned'}</small><strong>{datasetName.trim()||name.trim()||plan?.name||'Untitled scenario'}<DatasetActivityIcons datasetId={draftDatasetId||plan?.dataset_id}/></strong></span>{mode==='source_backed'&&<span><small>Source connection</small><strong>{source?.name??'Not selected'}</strong></span>}</div><span className="scenario-mode-indicator-copy"><small>Generation mode</small><strong>{mode==='synthetic'?'Synthetic':'Source-backed'}</strong></span></div></div>}
        <form noValidate className="scenario-step-panel panel" onSubmit={event=>{event.preventDefault();step===3?void startGeneration(event):void nextStep()}}>
      

      {step===0&&<><div className="scenario-section-heading"><div><div className="eyebrow">STEP 1 · REQUIREMENTS</div><h2>Describe the data your test needs</h2><p>Enter the scenario as a prompt. Choose synthetic generation or an approved source connection.</p></div><button type="button" className="button button-secondary" onClick={()=>void saveDraft()} disabled={busy||generationInProgress}><Save size={15}/>Save draft</button></div><label className="form-field">Data set name<input required minLength={2} value={name} onChange={e=>{const nextName=e.target.value;setName(nextName);setDatasetName(current=>!current||current.trim()===name.trim()?nextName:current)}} placeholder="e.g. NRI customer account scenario"/></label><TestDataRequestPicker role={role} workspaceId={workspaceId} value={testDataRequestId} disabled={busy||generationInProgress} onChange={ticket=>{setTestDataRequestId(ticket?.id??"");if(ticket){setRequirement(testDataRequestRequirements(ticket));if(ticket.record_count!=null)setVolume(String(ticket.record_count));const objective=testDataRequestObjective(ticket);if(objective)setTargetUse(objective);const requestedExpiry=testDataRequestExpiry(ticket);if(requestedExpiry)setExpiry(requestedExpiry)}}}/><div className="scenario-form-grid"><label className="form-field scenario-span-two">Test data requirements<textarea required minLength={10} maxLength={4000} rows={5} value={requirement} onChange={e=>setRequirement(e.target.value)} placeholder="Describe the customer types, account relationships, business rules, and states the test must cover."/></label><label className="form-field">Expected states (optional)<textarea rows={3} value={expected} onChange={e=>setExpected(e.target.value)} placeholder={'One per line\nActive account\nDormant account'}/></label><label className="form-field">Exclusions (optional)<textarea rows={3} value={exclusions} onChange={e=>setExclusions(e.target.value)} placeholder="One exclusion per line"/></label></div><div className="scenario-mode-switch" role="group" aria-label="Generation mode"><label><input type="radio" checked={mode==='synthetic'} onChange={()=>{setMode('synthetic');setSourceId('');setSelectedSourceObjects([]);setMapping([])}}/>Synthetic</label><label><input type="radio" checked={mode==='source_backed'} onChange={()=>{setMode('source_backed');setMapping([])}}/>Source-backed</label></div>{mode==='source_backed'&&<div className="scenario-form-grid"><label className="form-field scenario-span-two">Source connection<select value={sourceId} onChange={e=>{const nextId=e.target.value;setSourceId(nextId);setSourceObject('');setSelectedSourceObjects(sources.find(item=>item.id===nextId)?.objects.map(item=>item.name)??[]);setMapping([])}}><option value="">Choose an enabled connection</option>{sources.map(item=><option key={item.id} value={item.id}>{item.name} · {item.connector_type}</option>)}</select></label></div>}{mode==='source_backed'&&source&&<section className="scenario-object-scope"><div className="scenario-object-scope-heading"><div><strong>Source schema objects</strong><small>Select the tables or objects relevant to this scenario. TIGR will use only the selected objects and their schema metadata.</small></div><div className="scenario-object-scope-actions"><span>{selectedSourceObjects.length} of {source.objects.length} selected</span><button type="button" className="text-button" onClick={()=>{setSelectedSourceObjects(source.objects.map(item=>item.name));setMapping([]);setPreview(null)}}>Select all</button><button type="button" className="text-button" onClick={()=>{setSelectedSourceObjects([]);setMapping([]);setPreview(null)}}>Clear</button></div></div><div className="scenario-object-options">{source.objects.map(item=>{const selected=selectedSourceObjects.includes(item.name);return <label key={item.name} className={`scenario-object-option${selected?' selected':''}`}><input type="checkbox" checked={selected} onChange={event=>toggleSourceObject(item.name,event.target.checked)}/><strong>{item.name}</strong><small>{item.fields.length} fields</small></label>})}</div>{!selectedSourceObjects.length&&<small className="scenario-object-scope-empty">Select at least one object to continue.</small>}</section>}{mode==='synthetic'&&<div className="scenario-manual-schema"><label><input type="checkbox" checked={manualSchema} onChange={e=>setManualSchema(e.target.checked)}/>Build the schema fields now</label><small>Leave this unchecked to have TIGR propose fields in the next step.</small>{manualSchema&&<div className="scenario-mapping-header"><h3>Initial schema fields</h3><button type="button" className="button button-secondary" onClick={addNewField}><Plus size={15}/>Add field</button></div>}{manualSchema&&<div className="scenario-mapping-table"><table><thead><tr><th>Field name</th><th>Type</th><th/></tr></thead><tbody>{mapping.map((field,index)=><tr key={'initial-'+index}><td><input value={field.target_field} placeholder="e.g. account_id" onChange={e=>setMapping(rows=>rows.map((row,i)=>i===index?{...row,source_field:e.target.value,target_field:e.target.value}:row))}/></td><td><input value={field.data_type} onChange={e=>editField(index,'data_type',e.target.value)}/></td><td><button type="button" className="text-button" onClick={()=>setMapping(rows=>rows.filter((_,i)=>i!==index))}>Remove</button></td></tr>)}{!mapping.length&&<tr><td colSpan={3} className="table-empty">Add the schema fields to start.</td></tr>}</tbody></table></div>}</div>}</>}

      {step===1&&<>
        <div className="scenario-section-heading"><div><div className="eyebrow">STEP 2 · SCHEMA MAPPING</div><h2>Review and edit the required fields</h2><p>{mode==='source_backed'?'Review the source field suggestions and select the output fields to carry into Step 3.':'Your schema fields from Step 1 are carried forward. Add fields or define relationships here, then continue to Step 3.'}</p></div><button type="button" className="button button-secondary" onClick={()=>void saveDraft()} disabled={busy||generationInProgress}><Save size={15}/>Save draft</button></div>
        {!(mode==='synthetic'&&manualSchema)&&schemaGenerationStatus&&<div className="scenario-job-status" role="status" aria-live="polite"><div><strong>Job {(schemaGenerationStatus.job_id??'').slice(0,8)||'status unavailable'}</strong><span className="status-badge"><i/>{schemaGenerationStatus.status}</span></div><progress max="100" value={schemaGenerationStatus.status==='running'?20:100}/><small>{schemaStageLabel(schemaGenerationStatus.stage,mode)}</small><div className="scenario-job-meta"><span className="scenario-job-id-label">Job ID: <code>{schemaGenerationStatus.job_id??'Earlier run; no job ID'}</code></span><button type="button" className="text-button" onClick={()=>navigate('/jobs')}>Open Jobs</button></div>{schemaGenerationStatus.status==='running'&&<small className="scenario-job-elapsed"><span className="scenario-elapsed-time">Elapsed: {Math.max(0,Math.floor((schemaClock-Date.parse(schemaGenerationStatus.started_at??new Date(schemaClock).toISOString()))/1000))} sec</span> · TIGR is working in the background. This page updates automatically when the job finishes.</small>}{schemaGenerationStatus.status==='complete'&&<small>Completed in {((schemaGenerationStatus.elapsed_ms??0)/1000).toFixed(1)} sec.</small>}{schemaGenerationStatus.status==='failed'&&<small className="scenario-generation-error">{schemaGenerationStatus.error}</small>}</div>}
        {!(mode==='synthetic'&&manualSchema)&&<section className="scenario-feedback-layout"><div className="scenario-feedback-notes"><h3>TIGR notes</h3>{plan?.logical_plan.schema_generation_notes?.length?<ul>{plan.logical_plan.schema_generation_notes.map((note,index)=><li key={index}>{note}</li>)}</ul>:<p>TIGR notes will appear here after schema generation.</p>}</div><label className="form-field scenario-feedback-input">Corrections on schema mapping<textarea rows={5} maxLength={4000} disabled={busy||generationInProgress} value={schemaFeedback} onChange={event=>setSchemaFeedback(event.target.value)} placeholder="Describe what TIGR should correct, add, remove, or reconsider in its previous suggestions."/><small>TIGR uses your feedback with the original requirement, approved schema, and previous suggestions when regenerating.</small></label></section>}
        {mode==='source_backed'&&plan?.logical_plan.requirement_intent&&<section className="scenario-coverage"><div><h3>Requirement interpretation</h3><p>{plan.logical_plan.requirement_intent.summary}</p>{plan.logical_plan.requirement_intent.output_grain&&<small>One output row represents: {plan.logical_plan.requirement_intent.output_grain}</small>}</div><div className="scenario-coverage-original"><strong>Compare against your original requirement</strong><p>{requirement}</p></div>{!!plan.logical_plan.requirement_intent.entities?.length&&<div className="scenario-coverage-entities"><strong>Entities TIGR identified</strong><ul>{plan.logical_plan.requirement_intent.entities.map((entity,index)=><li key={entity.name+index}><b>{entity.name}</b><span>{entity.purpose}</span></li>)}</ul></div>}{!!requirementCriteria.length&&<div className="scenario-coverage-criteria"><strong>Requirement coverage · map a gap or accept it to continue</strong>{requirementCriteria.map((criterion,index)=>{const coverage=coverageById.get(criterion.id);const accepted=acceptedCriteria.has(criterion.id);return <article key={criterion.id+index} className={coverage?.covered?'covered':accepted?'accepted':'uncovered'}><span className="scenario-coverage-status">{coverage?.covered?'Mapped':accepted?'Gap accepted':'Needs review'}</span><div><b>{criterion.requirement}</b><small>{coverage?.source_object&&coverage?.source_field?coverage.source_object+'.'+coverage.source_field+' · ':''}{coverage?.explanation??'TIGR did not report coverage for this criterion.'}</small>{!coverage?.covered&&<label className="scenario-coverage-accept"><input type="checkbox" checked={accepted} disabled={busy||generationInProgress} onChange={event=>void setCriterionAccepted(criterion.id,event.target.checked)}/><span>Accept this gap and allow continuing to Step 3.</span></label>}</div></article>})}</div>}</section>}
        {mode==='source_backed'?<SourceFieldMappingEditor suggestions={plan?.suggested_mapping??[]} values={mapping} objects={source?.objects.filter(object=>selectedSourceObjects.includes(object.name))??[]} onChange={setMapping} onAdd={addOutputField} disabled={busy||generationInProgress}/>:<><div className="scenario-mapping-header"><h3>Output fields · {mapping.length}</h3><button type="button" className="button button-secondary" onClick={addOutputField} disabled={busy||generationInProgress}><Plus size={15}/>Add field</button></div><div className="scenario-mapping-table"><table><thead><tr><th>Field name</th><th>Type</th><th/></tr></thead><tbody>{mapping.map((field,index)=><tr key={'output-'+index}><td><input value={field.target_field} onChange={event=>setMapping(rows=>rows.map((row,i)=>i===index?{...row,source_field:event.target.value,target_field:event.target.value}:row))}/></td><td><input value={field.data_type} onChange={event=>editField(index,'data_type',event.target.value)}/></td><td><button type="button" className="text-button" onClick={()=>setMapping(rows=>rows.filter((_,i)=>i!==index))}>Remove</button></td></tr>)}{!mapping.length&&<tr><td colSpan={3} className="table-empty">Add at least one output field.</td></tr>}</tbody></table></div></>}
        {mode==='source_backed'&&!!plan?.logical_plan.suggested_source_joins?.length&&<section className="scenario-ai-suggestions"><div><h3>Suggested relationships</h3><p>Select the relationships that apply to the chosen source objects.</p></div>{plan.logical_plan.suggested_source_joins.map((candidate,index)=>{const selected=sourceJoins.some(join=>join.left_object===candidate.left_object&&join.left_field===candidate.left_field&&join.right_object===candidate.right_object&&join.right_field===candidate.right_field);return <label className="scenario-ai-field" key={'suggested-join-'+index}><input type="checkbox" checked={selected} disabled={busy||generationInProgress} onChange={event=>setSourceJoins(rows=>event.target.checked?[...rows,{left_object:candidate.left_object,left_field:candidate.left_field,right_object:candidate.right_object,right_field:candidate.right_field,confidence:candidate.confidence,rationale:candidate.rationale}]:rows.filter(join=>!(join.left_object===candidate.left_object&&join.left_field===candidate.left_field&&join.right_object===candidate.right_object&&join.right_field===candidate.right_field)))}/><span><strong>{candidate.left_object}.{candidate.left_field} → {candidate.right_object}.{candidate.right_field}</strong>{candidate.rationale&&<small>{candidate.rationale}</small>}</span><span className="scenario-confidence">{typeof candidate.confidence!=='number'?'Not scored':Math.round(candidate.confidence*100)+'%'}</span></label>})}</section>}
        {mode==='source_backed'&&!!plan?.logical_plan.suggested_filters?.length&&<section className="scenario-ai-suggestions"><div><h3>Suggested filters</h3><p>Choose filters that should apply to the selected source fields.</p></div><label className="form-field scenario-filter-suggestion-select">Select a suggested filter<select value="" disabled={busy||generationInProgress} onChange={event=>{if(event.target.value==='')return;const candidate=plan.logical_plan.suggested_filters?.[Number(event.target.value)];if(!candidate)return;const matched=mapping.find(field=>field.source_object===candidate.source_object&&field.source_field===candidate.source_field)||mapping.find(field=>field.target_field===candidate.field);if(!matched){setError('Select the suggested source field in the mapping before applying its filter.');return}const values=filterSuggestionValues(candidate.value);if(candidate.operator!=='is_null'&&!values.length){setError('TIGR returned a filter value that cannot be selected. Review the suggestion or add a filter in Step 3.');return}const value=candidate.operator==='is_null'?'':candidate.operator==='in'?JSON.stringify(values):values.length===1?values[0]:'';const duplicate=filters.some(filter=>filter.field===matched.target_field&&filter.operator===candidate.operator&&filter.value===value);if(!duplicate)setFilters(rows=>[...rows,{field:matched.target_field,source_object:candidate.source_object,source_field:candidate.source_field,operator:candidate.operator,value,suggested_values:values,confidence:candidate.confidence,rationale:candidate.rationale}]);setError('')}}><option value="">Choose a filter suggestion</option>{plan.logical_plan.suggested_filters.map((candidate,index)=>{const matched=mapping.some(field=>field.source_object===candidate.source_object&&field.source_field===candidate.source_field)||mapping.some(field=>field.target_field===candidate.field);const values=filterSuggestionValues(candidate.value);return <option key={'suggested-filter-'+index} value={index} disabled={!matched||(candidate.operator!=='is_null'&&!values.length)}>{candidate.source_object&&candidate.source_field?candidate.source_object+'.'+candidate.source_field:candidate.field} · {candidate.operator}{values.length?' '+values.join(', '):''}{!matched?' · select its field first':''}</option>})}</select></label>{!!filters.filter(filter=>filter.source_object||filter.source_field).length&&<div className="scenario-ai-filter-list">{filters.filter(filter=>filter.source_object||filter.source_field).map((filter,index)=><div className="scenario-ai-filter-chip" key={'applied-filter-'+index}><span><strong>{filter.source_object&&filter.source_field?filter.source_object+'.'+filter.source_field:filter.field} · {filter.operator} {filterValue(parseValue(filter.value))}</strong>{filter.rationale&&<small>{filter.rationale}</small>}</span><button type="button" className="text-button" disabled={busy||generationInProgress} onClick={()=>setFilters(rows=>rows.filter(item=>item!==filter))}>Remove</button></div>)}</div>}</section>}
        {mode==='source_backed'&&<section className="scenario-relationships"><div className="scenario-relationships-heading"><div><h3>Selected source relationships</h3><p>Review the joins TIGR will use to connect the selected source objects.</p></div><button type="button" className="button button-secondary" disabled={busy||generationInProgress} onClick={()=>setSourceJoins(rows=>[...rows,{left_object:'',left_field:'',right_object:'',right_field:''}])}><Plus size={15}/>Add relationship</button></div>{sourceJoins.map((join,index)=><div className="scenario-relationship-row" key={'join-'+index}><label className="form-field">Left object<select value={join.left_object} disabled={busy||generationInProgress} onChange={event=>setSourceJoins(rows=>rows.map((row,i)=>i===index?{...row,left_object:event.target.value,left_field:''}:row))}><option value="">Select object</option>{source?.objects.filter(object=>selectedSourceObjects.includes(object.name)).map(object=><option key={object.name} value={object.name}>{object.name}</option>)}</select></label><label className="form-field">Left field<select value={join.left_field} disabled={busy||generationInProgress} onChange={event=>setSourceJoins(rows=>rows.map((row,i)=>i===index?{...row,left_field:event.target.value}:row))}><option value="">Select field</option>{source?.objects.find(object=>object.name===join.left_object)?.fields.map(field=><option key={field.name} value={field.name}>{field.name}</option>)}</select></label><label className="form-field">Right object<select value={join.right_object} disabled={busy||generationInProgress} onChange={event=>setSourceJoins(rows=>rows.map((row,i)=>i===index?{...row,right_object:event.target.value,right_field:''}:row))}><option value="">Select object</option>{source?.objects.filter(object=>selectedSourceObjects.includes(object.name)).map(object=><option key={object.name} value={object.name}>{object.name}</option>)}</select></label><label className="form-field">Right field<select value={join.right_field} disabled={busy||generationInProgress} onChange={event=>setSourceJoins(rows=>rows.map((row,i)=>i===index?{...row,right_field:event.target.value}:row))}><option value="">Select field</option>{source?.objects.find(object=>object.name===join.right_object)?.fields.map(field=><option key={field.name} value={field.name}>{field.name}</option>)}</select></label><button type="button" className="text-button" disabled={busy||generationInProgress} onClick={()=>setSourceJoins(rows=>rows.filter((_,i)=>i!==index))}>Remove</button></div>)}</section>}
        {mode==='synthetic'&&<RelationshipEditor values={relationships} fields={mapping.map(field=>field.target_field).filter(Boolean)} onChange={setRelationships} disabled={busy||generationInProgress}/>}
      </>}
      {step===2&&<><div className="scenario-section-heading"><div><div className="eyebrow">STEP 3 · QUERY DEVELOPMENT</div><h2>{mode==='source_backed'?'Review the SQL':'Review the SQL and sample output'}</h2><p>{mode==='source_backed'?'Review or edit PostgreSQL SQL for the selected source connection, then validate it.':'Enter a SQL query as requirements for TIGR to generate synthetic data. Validate the SQL or generate a sample preview.'}</p></div><button type="button" className="button button-secondary" onClick={()=>void saveDraft()} disabled={busy||generationInProgress}><Save size={15}/>Save draft</button></div><div className="scenario-required-fields"><strong>Required output fields</strong><div>{queryOutputFields.map(field=><span key={field.target_field}>{field.target_field} <small>{field.data_type}</small></span>)}</div></div><div className="scenario-filters-heading"><div><h3>Filters</h3><p>Review the filters that will be included in the SQL.</p></div><button type="button" className="button button-secondary" onClick={()=>setFilters(rows=>[...rows,{field:'',operator:'eq',value:''}])}><Plus size={15}/>Add filter</button></div>{filters.map((filter,index)=><div className="scenario-filter-grid" key={'filter-'+index}><label className="form-field">Output field<select value={filter.field} onChange={e=>setFilters(rows=>rows.map((row,i)=>i===index?{...row,field:e.target.value}:row))}><option value="">Choose field</option>{mapping.map(item=><option key={item.target_field} value={item.target_field}>{item.target_field}</option>)}</select></label><label className="form-field">Operator<select value={filter.operator} onChange={e=>setFilters(rows=>rows.map((row,i)=>i===index?{...row,operator:e.target.value}:row))}>{OPERATORS.map(item=><option key={item}>{item}</option>)}</select></label>{filter.operator!=='is_null'&&<label className="form-field">{filter.operator==='between'?'Filter range':'Filter value'}{filter.operator==='between'?<div className="scenario-between-values"><input aria-label="Range start" placeholder="From" value={selectedRangeValues(filter)[0]} onChange={e=>setFilters(rows=>rows.map((row,i)=>{if(i!==index)return row;const values=selectedRangeValues(row);values[0]=e.target.value;return {...row,value:JSON.stringify(values)}}))}/><input aria-label="Range end" placeholder="To" value={selectedRangeValues(filter)[1]} onChange={e=>setFilters(rows=>rows.map((row,i)=>{if(i!==index)return row;const values=selectedRangeValues(row);values[1]=e.target.value;return {...row,value:JSON.stringify(values)}}))}/></div>:filter.suggested_values?.length?<select multiple={filter.operator==='in'} value={selectedFilterValues(filter)} onChange={e=>{const value=filter.operator==='in'?JSON.stringify(Array.from(e.target.selectedOptions,option=>option.value)):e.target.value;setFilters(rows=>rows.map((row,i)=>i===index?{...row,value}:row))}}>{filter.suggested_values.map((value,valueIndex)=><option key={value+'-'+valueIndex} value={value}>{value}</option>)}</select>:<input value={filter.value} onChange={e=>setFilters(rows=>rows.map((row,i)=>i===index?{...row,value:e.target.value}:row))}/>}</label>}<button type="button" className="text-button" onClick={()=>setFilters(rows=>rows.filter((_,i)=>i!==index))}>Remove</button></div>)}<div className="scenario-query-actions">{mode==='source_backed'&&<button type="button" className="button button-primary" onClick={()=>void runQueryAction(false)} disabled={busy||!mapping.length}><Sparkles size={15}/>{busy?'Generating SQL…':'Generate SQL'}</button>}{mode==='source_backed'&&<button type="button" className="button button-confirm" onClick={()=>void runQueryAction(true)} disabled={busy||!mapping.length||!queryText.trim()}><Check size={15}/>{busy?'Validating SQL…':'Validate SQL'}</button>}{mode==='synthetic'&&<button type="button" className="button button-primary" onClick={()=>void runQueryAction(false,Boolean(preview))} disabled={busy||sampleGenerationInProgress||!mapping.length||!queryText.trim()}><Sparkles size={15}/>{sampleGenerationInProgress?'Generating sample…':busy?'Starting generation…':preview?'Regenerate':'Generate sample'}</button>}{mode==='synthetic'&&<button type="button" className="button button-confirm scenario-button-disabled" onClick={()=>void runQueryAction(true)} disabled={busy||sampleGenerationInProgress||!mapping.length||!queryText.trim()}><Check size={15}/>Validate &amp; fix SQL</button>}</div>{queryNotice&&<div className="scenario-notice scenario-query-message" role="status">{queryNotice}</div>}{mode==='synthetic'&&sampleGenerationStatus&&<div className="scenario-job-status" role="status" aria-live="polite"><div><strong>Job {(sampleGenerationStatus.job_id??'').slice(0,8)||'status unavailable'}</strong><span className="status-badge"><i/>{sampleGenerationStatus.status}</span></div><progress max="100" value={sampleGenerationStatus.status==='running'?45:100}/><small>{sampleGenerationStatus.stage==='reviewing_sql'?'Reviewing the SQL query':sampleGenerationStatus.status==='running'?'Generating sample rows':sampleGenerationStatus.status==='failed'?'Sample generation failed':sampleGenerationStatus.include_sample===false?'SQL review complete':'Sample generation complete'}</small><div className="scenario-job-meta"><span className="scenario-job-id-label">Job ID: <code>{sampleGenerationStatus.job_id??'Earlier run; no job ID'}</code></span><button type="button" className="text-button" onClick={()=>navigate('/jobs')}>Open Jobs</button></div>{sampleGenerationStatus.status==='running'&&<small className="scenario-job-elapsed"><span className="scenario-elapsed-time">Elapsed: {Math.max(0,Math.floor((schemaClock-Date.parse(sampleGenerationStatus.started_at??new Date(schemaClock).toISOString()))/1000))} sec</span> · TIGR is hunting in the background. This page updates automatically when the job finishes.</small>}{sampleGenerationStatus.status==='complete'&&<small>Completed in {((sampleGenerationStatus.elapsed_ms??0)/1000).toFixed(1)} seconds.</small>}{sampleGenerationStatus.status==='failed'&&<small className="scenario-generation-error">{sampleGenerationStatus.error}</small>}</div>}{mode==='synthetic'&&<><div className="scenario-synthetic-editor-layout"><div className="source-query-editor-shell scenario-generated-sql scenario-synthetic-query"><pre ref={queryHighlightRef} className="source-query-highlight" aria-hidden="true" dangerouslySetInnerHTML={{__html:queryText?highlightSql(queryText):'<span className="sql-placeholder">SELECT * FROM synthetic_data LIMIT 5</span>'}}/><textarea ref={queryEditorRef} className="source-query-editor" spellCheck={false} wrap="soft" aria-label="Synthetic sample SQL query" value={queryText} onChange={event=>{setQueryText(event.target.value);setQueryEdited(true);setPreview(null);setQueryError('');setQueryNotice('');setQueryRepair(null)}} onScroll={event=>{if(queryHighlightRef.current){queryHighlightRef.current.scrollTop=event.currentTarget.scrollTop;queryHighlightRef.current.scrollLeft=event.currentTarget.scrollLeft}}}/></div><label className="form-field scenario-correction-field">Correction requirement<textarea rows={8} maxLength={4000} disabled={busy||sampleGenerationInProgress} value={generationFeedback} onChange={event=>setGenerationFeedback(event.target.value)} placeholder="Provide your corrections input on the generation, add sample values for fields."/></label></div>{queryError?<div className="alert alert-warning scenario-query-message" role="alert">{queryError}</div>:queryRepair?<div className="scenario-sql-repair" role="status"><strong>SQL syntax issue fixed by TIGR</strong><p><b>Error:</b> {queryRepair.original_error}</p><p><b>Fix:</b> {queryRepair.fix_summary} The corrected query is loaded in the SQL editor.</p></div>:<small>TIGR uses this SQL as generation guidance and the reviewed fields as the complete output schema. Every reviewed field is included in generated records; add new fields in Schema mapping first. Validate & fix SQL checks syntax without a source connection.</small>}</>}{mode==='source_backed'&&<><div className="source-query-editor-shell scenario-generated-sql"><pre ref={queryHighlightRef} className="source-query-highlight" aria-hidden="true" dangerouslySetInnerHTML={{__html:queryText?highlightSql(queryText):'<span className="sql-placeholder">PostgreSQL source query</span>'}}/><textarea ref={queryEditorRef} className="source-query-editor" spellCheck={false} wrap="soft" aria-label="PostgreSQL source query" value={queryText} onChange={event=>{setQueryText(event.target.value);setQueryEdited(true);setPreview(null);setQueryError('');setQueryNotice('')}} onScroll={event=>{if(queryHighlightRef.current){queryHighlightRef.current.scrollTop=event.currentTarget.scrollTop;queryHighlightRef.current.scrollLeft=event.currentTarget.scrollLeft}}}/></div>{queryError?<div className="alert alert-warning scenario-query-message" role="alert">{queryError}</div>:<small>Review the generated PostgreSQL SQL here. SQL validation checks it against the selected source connection. {piiMaskingEnabled?'Source records are shown after PII masking.':'Source records are available in the preview.'}</small>}</>}{preview&&<div className="scenario-query-preview"><div className="scenario-preview-summary"><span>Source: <strong>{preview.source}</strong></span><span>Objects: <strong>{mode==='synthetic'?'synthetic_data':mappedObjects.join(', ')||preview.objects?.join(', ')||preview.object||'Generated schema'}</strong></span><span>{mode==='source_backed'?'Matching rows':'Sample rows'}: <strong>{preview.sample_count??preview.sample_rows.length}</strong></span><span>Query limit: <strong>{preview.row_limit.toLocaleString()}</strong></span>{mode==='source_backed'&&<button type="button" className="button button-secondary" disabled={!preview.query} onClick={()=>{if(preview.query)void navigator.clipboard.writeText(queryText||preview.query).then(()=>setQueryNotice('SQL query copied.')).catch(()=>setQueryError('Unable to copy the SQL query.'))}}><Copy size={14}/>Copy SQL</button>}</div>{mode==='source_backed'&&<><div className="source-query-editor-shell scenario-generated-sql"><pre ref={queryHighlightRef} className="source-query-highlight" aria-hidden="true" dangerouslySetInnerHTML={{__html:queryText?highlightSql(queryText):'<span class="sql-placeholder">PostgreSQL source query</span>'}}/><textarea ref={queryEditorRef} className="source-query-editor" spellCheck={false} wrap="soft" aria-label="PostgreSQL source query" value={queryText} onChange={event=>{setQueryText(event.target.value);setQueryEdited(true);setPreview(null);setQueryError('');setQueryNotice('')}} onScroll={event=>{if(queryHighlightRef.current){queryHighlightRef.current.scrollTop=event.currentTarget.scrollTop;queryHighlightRef.current.scrollLeft=event.currentTarget.scrollLeft}}}/></div>{queryError?<div className="alert alert-warning scenario-query-message" role="alert">{queryError}</div>:<small>Read-only PostgreSQL SQL is validated and executed against {preview.source}. Edits are used to check matching rows and generate the data set.</small>}</>}{preview.sample_error&&<div className="scenario-questions" role="status">{preview.sample_error}{mode==='source_backed'?' The SQL is still available above for review.':' Review the schema fields and generation requirements, then try again.'}</div>}{preview.sample_values_withheld&&<div className="alert alert-info scenario-query-empty-result" role="status">Source sample values are withheld until PII masking is complete.</div>}{preview.sample_rows.length>0?<div className="table-shell scenario-sample-table"><table><thead><tr>{preview.fields.map(field=><th key={field}>{field}</th>)}</tr></thead><tbody>{preview.sample_rows.map((row,index)=><tr key={index}>{preview.fields.map(field=><td key={field}>{String(row[field]??'—')}</td>)}</tr>)}</tbody></table></div>:!preview.sample_values_withheld&&!preview.sample_error&&<div className="alert alert-info scenario-query-empty-result" role="status"><strong>Query executed successfully, but returned no rows.</strong> Review the SQL filters and selected source fields, or adjust them to find matching source records.</div>}</div>}</>}
      {step===3&&<><div className="scenario-section-heading"><div><div className="eyebrow">STEP 4 · DATA GENERATION</div><h2>Set up the data set</h2><p>Choose the same delivery settings used when creating a data set draft.</p></div><button type="button" className="button button-secondary" onClick={()=>void saveDraft()} disabled={busy||generationInProgress||Boolean(plan&&plan.state!=="ready")}><Save size={15}/>Save draft</button></div>{mode==='synthetic'&&preview&&(preview.sample_count??preview.sample_rows.length)===0&&!canRetryFailedGeneration&&!skipSampleGeneration&&<div className="alert alert-warning scenario-empty-preview-warning" role="alert"><strong>No sample records were generated.</strong> Go back to Step 3 and review the query before continuing.</div>}{mode==='synthetic'&&!preview&&!canRetryFailedGeneration&&!skipSampleGeneration&&<div className="alert alert-warning scenario-empty-preview-warning" role="alert"><strong>Sample generation was not run.</strong> Go back to Step 3 to generate a sample, or continue without a sample.</div>}{mode==='synthetic'&&skipSampleGeneration&&<div className="alert alert-info" role="status">Sample generation was skipped. You can continue with full data set generation.</div>}{canRetryFailedGeneration&&<div className="alert alert-info" role="status">This failed run has an empty draft version. Retry generation to continue using the reviewed scenario.</div>}<div className="scenario-form-grid"><label className="form-field">Data set name<input required minLength={2} maxLength={180} value={datasetName} onChange={e=>setDatasetName(e.target.value)} placeholder="e.g. Customer account test data"/></label><label className="form-field">Creation pathway<input readOnly value={SCENARIO_TO_DATA_LABEL}/></label><label className="form-field scenario-span-two">Target use<input required maxLength={80} value={targetUse} onChange={e=>setTargetUse(e.target.value)} placeholder="e.g. Regression testing"/></label><label className="form-field">Record Count<input required type="number" inputMode="numeric" step={1} min={1} max={10000} value={volume} onChange={e=>setVolume(e.target.value)} placeholder="e.g. 100"/></label><label className="form-field">Output format<select value={outputFormat} onChange={e=>setOutputFormat(e.target.value as 'csv'|'json')}><option value="csv">CSV</option><option value="json">JSON</option></select></label><label className="form-field scenario-span-two">Expiry (optional)<input type="datetime-local" value={expiry} onChange={e=>setExpiry(e.target.value)}/></label></div>{mode==='source_backed'&&<div className="alert alert-info scenario-source-masking-note" role="status">{piiMaskingEnabled ? "Source records are not previewed before PII masking. After generation, complete masking before reviewing the records." : "Validated source records are available for review after generation."}{notice&&<><br/><strong>{notice}</strong></>}</div>}{plan?.job&&<div className="scenario-job-status"><div><strong>Job {plan.job.id.slice(0,8)}</strong><span className="status-badge"><i/>{plan.job.status}</span></div><progress max="100" value={plan.job.progress}/><small>{plan.job.safe_summary??'Generation is processing.'}</small></div>}{plan?.state==='succeeded'&&<div className="scenario-complete"><Check size={18}/><div><strong>{mode==='source_backed'?'Source extraction validated':'Generation completed'}</strong><p>{mode==='source_backed'?(piiMaskingEnabled?'Complete PII masking before source-backed records are stored or previewed.':'Source data passed validation and is ready for publication.'):'The output is available as data set '+(plan.dataset_reference_code??'')}.</p></div></div>}{plan?.state==='quarantined'&&<div className="alert alert-warning">Output failed schema validation and was quarantined.</div>}{generationFailed&&<div className="alert alert-warning scenario-generation-failure" role="alert"><strong>Generation failed</strong><span>{generationFailureSummary}</span>{canRetryFailedGeneration&&<span>This empty draft can be retried with the reviewed scenario.</span>}</div>}</>}
      <footer className="scenario-step-actions">{(error||(notice&&!(mode==='source_backed'&&step===3)))&&<div className="scenario-step-feedback">{error&&<div className="alert alert-warning" role="alert">{error}</div>}{notice&&!(mode==='source_backed'&&step===3)&&<div className="scenario-notice" role="status">{notice}</div>}</div>}<div className="scenario-step-control-row">{step>0&&<button type="button" className="button button-secondary" onClick={()=>setStep(value=>Math.max(value-1,0))} disabled={generationInProgress}><ArrowLeft size={15}/>Back</button>}<span className="scenario-step-spacer"/>{step<3?<button type="button" className="button button-confirm" onClick={()=>void nextStep()} disabled={generationInProgress}>{generationInProgress&&<LoaderCircle className="scenario-spin" size={15}/>}{generationInProgress?(sampleGenerationInProgress?'TIGR is hunting…':'Working…'):(step===1&&mode==='synthetic'&&manualSchema?'Continue to sample data':'Continue')}{!generationInProgress&&<ArrowRight size={15}/> }</button>:plan?.state==='succeeded'?<>{mode==='synthetic'&&<button type="button" className="button button-secondary" onClick={()=>void download()}><Download size={15}/>Download output</button>}<button type="button" className="button button-confirm" disabled={!plan.dataset_id} onClick={()=>plan.dataset_id&&navigate(`/datasets?dataset_id=${encodeURIComponent(plan.dataset_id)}`)}><GitBranch size={15}/>Open data set</button></>:<button type="submit" className="button button-confirm" disabled={generationInProgress||!plan||(!preview&&!skipSampleGeneration&&!canRetryFailedGeneration&&mode==='synthetic')||(emptySyntheticPreview&&!skipSampleGeneration&&!canRetryFailedGeneration)}><Sparkles size={15}/>{generationInProgress?'Generation in progress…':canRetryFailedGeneration?'Retry generation':'Generate data'}</button>}</div></footer>
    </form>
    {sampleDecisionOpen&&mode==='synthetic'&&<div className="dataset-overlay saved-plan-confirm-overlay" role="presentation" onMouseDown={event=>{if(event.target===event.currentTarget)setSampleDecisionOpen(false)}}><section className="dataset-modal saved-plan-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="sample-decision-title" aria-describedby="sample-decision-copy"><header className="dataset-modal-header"><div><div className="eyebrow">STEP 3 · SAMPLE DATA</div><h2 id="sample-decision-title">Generate sample data?</h2><p id="sample-decision-copy">Sample generation is optional. You can continue to Step 4 without a sample, or generate sample rows now and review them here first.</p></div><button type="button" className="icon-button" aria-label="Close sample generation question" onClick={()=>setSampleDecisionOpen(false)}><X size={18}/></button></header><footer className="dataset-modal-footer"><span>Full data set generation happens in Step 4.</span><div className="saved-plan-confirm-actions"><button type="button" className="button button-secondary" onClick={()=>{setSampleDecisionOpen(false);void nextStep(true)}}>Continue without sample</button><button type="button" className="button button-confirm" onClick={generateOptionalSample}><Sparkles size={15}/>Generate sample · stay on Step 3</button></div></footer></section></div>}
    {quarantineConfirmation&&<div className="dataset-overlay saved-plan-confirm-overlay" role="presentation" onMouseDown={event=>{if(event.target===event.currentTarget&&quarantiningPlanId!==quarantineConfirmation.plan.id){setQuarantineConfirmation(null);setQuarantineError('')}}}><section className="dataset-modal saved-plan-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="saved-plan-confirm-title" aria-describedby="saved-plan-confirm-copy"><header className="dataset-modal-header"><div><div className="eyebrow">SAVED SCENARIO PLAN</div><h2 id="saved-plan-confirm-title">Quarantine this draft?</h2><p id="saved-plan-confirm-copy">{quarantineConfirmation.datasetId?'The data set version will remain in Data Sets with Quarantined status and be removed from Saved scenario plans.':'This saved scenario plan will move to Quarantined and disappear from Saved scenario plans.'}</p></div><button type="button" className="icon-button" aria-label="Close confirmation" disabled={quarantiningPlanId===quarantineConfirmation.plan.id} onClick={()=>{setQuarantineConfirmation(null);setQuarantineError('')}}><X size={18}/></button></header>{quarantineError&&<div className="alert alert-warning saved-plan-confirm-error" role="alert">Quarantine failed: {quarantineError}</div>}<footer className="dataset-modal-footer"><span>This action cannot be undone.</span><div className="saved-plan-confirm-actions"><button type="button" className="button button-secondary" disabled={quarantiningPlanId===quarantineConfirmation.plan.id} onClick={()=>{setQuarantineConfirmation(null);setQuarantineError('')}}>Cancel</button><button type="button" className="button saved-plan-confirm-submit" disabled={quarantiningPlanId===quarantineConfirmation.plan.id} onClick={()=>void quarantineSavedDraft(quarantineConfirmation.plan,quarantineConfirmation.datasetId)}>{quarantiningPlanId===quarantineConfirmation.plan.id?<LoaderCircle className="scenario-spin" size={15}/>:<Trash2 size={15}/>} {quarantiningPlanId===quarantineConfirmation.plan.id?'Quarantining…':'Quarantine draft'}</button></div></footer></section></div>}
    {downloadReview&&<DownloadReviewDialog review={downloadReview} busy={downloadBusy} onCancel={()=>setDownloadReview(null)} onDownload={()=>void saveScenarioArtifact(downloadReview)}/>}
  </div>
}

function SourceFieldMappingEditor({suggestions,values,objects,onChange,onAdd,disabled}:{suggestions:FieldMap[];values:FieldMap[];objects:Source['objects'];onChange:(values:FieldMap[])=>void;onAdd:()=>void;disabled:boolean}){
  const usedMappingIndexes=new Set<number>()
  const rows=suggestions.map((candidate,index)=>{
    const mappingIndex=values.findIndex((field,valueIndex)=>!usedMappingIndexes.has(valueIndex)&&(field.source_object??null)===(candidate.source_object??null)&&field.source_field===candidate.source_field)
    if(mappingIndex>=0)usedMappingIndexes.add(mappingIndex)
    return {key:`suggestion-${candidate.source_object??''}-${candidate.source_field}-${index}`,candidate,mappingIndex:mappingIndex<0?null:mappingIndex}
  })
  values.forEach((field,index)=>{if(!usedMappingIndexes.has(index))rows.push({key:`additional-${index}`,candidate:field,mappingIndex:index})})
  const groups=new Map<string,typeof rows>()
  rows.forEach(row=>{const objectName=row.candidate.source_object||'Additional fields';groups.set(objectName,[...(groups.get(objectName)??[]),row])})
  return <section className="scenario-ai-suggestions scenario-unified-field-review"><div className="scenario-mapping-header"><div><h3>Source fields and output mapping · {values.length} selected</h3><p>Select fields and edit their output names and types here.</p></div><button type="button" className="button button-secondary" disabled={disabled} onClick={onAdd}><Plus size={15}/>Add field</button></div>{groups.size?Array.from(groups.entries()).map(([objectName,objectRows])=>{
    const groupIndexes=objectRows.map(row=>row.mappingIndex).filter((index):index is number=>index!==null)
    const allSelected=objectRows.length>0&&groupIndexes.length===objectRows.length
    return <details className="scenario-ai-object" key={objectName}><summary><strong>{objectName}</strong><span>{groupIndexes.length} selected · {objectRows.length} fields</span></summary><div className="scenario-mapping-table"><table><thead><tr><th><button type="button" className="text-button" disabled={disabled||!objectRows.length} onClick={()=>{if(allSelected){const indexes=new Set(groupIndexes);onChange(values.filter((_,index)=>!indexes.has(index)))}else{const additions=objectRows.filter(row=>row.mappingIndex===null).map(({candidate})=>({...candidate,required:true}));onChange([...values,...additions])}}}>{allSelected?'Clear all':'Select all'}</button></th><th>Source field</th><th>Confidence</th><th>Output field</th><th>Type</th><th>Remove</th></tr></thead><tbody>{objectRows.map(({key,candidate,mappingIndex})=>{
    const selectedIndex=mappingIndex
    const selected=selectedIndex!==null
    const field=selectedIndex!==null?values[selectedIndex]:candidate
    const confidence=field.confidence??candidate.confidence
    const currentSource=JSON.stringify([field.source_object??'',field.source_field??''])
    const currentIsAvailable=objects.some(object=>object.name===field.source_object&&object.fields.some(sourceField=>sourceField.name===field.source_field))
    return <tr key={key}>
      <td><input type="checkbox" aria-label={`Select ${candidate.source_object??'source object'} ${candidate.source_field}`} checked={selected} disabled={disabled} onChange={event=>{
        if(event.target.checked){onChange([...values,{source_field:candidate.source_field,target_field:candidate.target_field,data_type:candidate.data_type,required:true,primary_key:candidate.primary_key===true,source_object:candidate.source_object??null,confidence:candidate.confidence??null,rationale:candidate.rationale??null}])}
        else if(selectedIndex!==null)onChange(values.filter((_,index)=>index!==selectedIndex))
      }}/></td>
      <td><div className="scenario-unified-source-cell"><strong>{field.source_field||'Choose source field'}</strong>{selected&&<select aria-label={`Source object and field for ${field.target_field||'output field'}`} value={currentSource} disabled={disabled} onChange={event=>{
        if(selectedIndex===null)return
        const [objectName,fieldName]=JSON.parse(event.target.value) as [string,string]
        const metadata=objects.find(object=>object.name===objectName)?.fields.find(sourceField=>sourceField.name===fieldName)
        onChange(values.map((row,index)=>index===selectedIndex?{...row,source_object:objectName,source_field:fieldName,data_type:metadata?.type??row.data_type,primary_key:metadata?.primary_key===true}:row))
      }}><option value={currentSource} disabled={!currentIsAvailable}>{currentIsAvailable?'Selected source field':`Unavailable: ${field.source_object??''} · ${field.source_field||'Choose a field'}`}</option>{objects.flatMap(object=>object.fields.map(sourceField=><option key={`${object.name}.${sourceField.name}`} value={JSON.stringify([object.name,sourceField.name])}>{object.name} · {sourceField.name}</option>))}</select>}</div></td>
      <td><span className={'scenario-confidence '+(confidence==null?'unscored':confidence<0.5?'low':confidence<0.75?'medium':'high')}>{confidence==null?'Not scored':`${Math.round(confidence*100)}%`}</span></td>
      <td><input aria-label="Output field" value={field.target_field} disabled={!selected||disabled} onChange={event=>selectedIndex!==null&&onChange(values.map((row,index)=>index===selectedIndex?{...row,target_field:event.target.value}:row))} placeholder="Output field"/></td>
      <td><input aria-label="Output field type" value={field.data_type} disabled={!selected||disabled} onChange={event=>selectedIndex!==null&&onChange(values.map((row,index)=>index===selectedIndex?{...row,data_type:event.target.value}:row))} placeholder="Type"/></td>
      <td>{selected&&<button type="button" className="text-button" disabled={disabled} onClick={()=>selectedIndex!==null&&onChange(values.filter((_,index)=>index!==selectedIndex))}>Remove</button>}</td>
    </tr>
  })}{!objectRows.length&&<tr><td colSpan={6} className="table-empty">No fields in this object.</td></tr>}</tbody></table></div></details>}) :<div className="empty-state"><p>No field suggestions yet. Generate suggestions or add a source field.</p></div>}</section>
}

function RelationshipEditor({values,fields,onChange,disabled}:{values:Relationship[];fields:string[];onChange:(values:Relationship[])=>void;disabled:boolean}){return <section className="scenario-relationships"><div><h3>Key relationships</h3><p>For synthetic output, referenced key values are generated consistently. Source output is checked for referential coverage.</p></div>{values.map((relationship,index)=><div className="scenario-relationship-row" key={index}><label className="form-field">Parent key<select value={relationship.parent_field} onChange={event=>onChange(values.map((row,i)=>i===index?{...row,parent_field:event.target.value}:row))}><option value="">Select parent field</option>{fields.map(field=><option key={field}>{field}</option>)}</select></label><label className="form-field">Child foreign key<select value={relationship.child_field} onChange={event=>onChange(values.map((row,i)=>i===index?{...row,child_field:event.target.value}:row))}><option value="">Select child field</option>{fields.map(field=><option key={field}>{field}</option>)}</select></label><button type="button" className="text-button" onClick={()=>onChange(values.filter((_,i)=>i!==index))}>Remove</button></div>)}<button type="button" className="button button-secondary" disabled={disabled} onClick={()=>onChange([...values,{parent_field:'',child_field:''}])}><Plus size={15}/>Add relationship</button></section>}
function parseValue(value:string):unknown{try{return JSON.parse(value)}catch{return value}}
function message(error:unknown){return error instanceof Error?error.message:'Scenario request failed'}
