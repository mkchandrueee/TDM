import { useEffect, useState } from 'react'
import { ArrowRight, Check, Database, Plus, Search, Trash2 } from 'lucide-react'
import { apiRequest, type HumanRole } from './api'

type Field = {name:string;type?:string;primary_key?:boolean}
type ObjectRef = {name:string;path?:string;database?:string;schema?:string;type?:string;fields:Field[]}
type Binding = {name:string;objects:ObjectRef[]}
type Profile = {id:string;name:string;connector_type:string;environment?:string;structure_version:number;databases:Binding[]}
export type FederatedSourceSelection = {connection_id:string;structure_version:number;database:string;schema:string;object_name:string;fields:string[];alias:string}
export type FederatedJoinSelection = {left_alias:string;left_field:string;right_alias:string;right_field:string;join_type:'inner'|'left';cardinality:'one_to_one'|'one_to_many'|'many_to_one'|'unknown'}
export type FederatedPlanDraft = {sources:FederatedSourceSelection[];joins:FederatedJoinSelection[]}
type Pick = FederatedSourceSelection
type Preview = {valid:boolean;execution_supported:boolean;sources:Array<{alias:string;connection_name:string;database:string;schema:string;object:string;fields:string[];read_template:string}>;joins:Array<{left_alias:string;left_field:string;right_alias:string;right_field:string;join_type:string;cardinality:string}>;row_limit_per_source:number;join_location:string;estimates_available:boolean;warnings:string[];disclosure:string}

export function FederatedPlanBuilder({workspaceId,role,profiles,initialPlan,onPlanChange}:{workspaceId:string;role:HumanRole;profiles:Profile[];initialPlan?:FederatedPlanDraft;onPlanChange:(plan:FederatedPlanDraft)=>void}) {
  const [selected,setSelected]=useState<Pick[]>(initialPlan?.sources??[])
  const [joins,setJoins]=useState<FederatedJoinSelection[]>(initialPlan?.joins??[])
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const [preview,setPreview]=useState<Preview|null>(null)
  const [fieldSearch,setFieldSearch]=useState<Record<string,string>>({})
  useEffect(()=>{setSelected(initialPlan?.sources??[]);setJoins(initialPlan?.joins??[])},[initialPlan])

  function addSource(){
    if(selected.length>=10)return
    const available=profiles.flatMap(profile=>profile.databases.flatMap(binding=>binding.objects.map(object=>({profile,binding,object}))))
      .filter(({profile,binding,object})=>!selected.some(source=>source.connection_id===profile.id&&source.database===binding.name&&source.schema===(object.schema??'')&&source.object_name===object.name))
    const otherConnector=available.find(({profile})=>selected.length>0&&selected.every(source=>profileFor(source)?.connector_type!==profile.connector_type))
    const otherConnection=available.find(({profile})=>!selected.some(source=>source.connection_id===profile.id))
    const candidate=otherConnector??otherConnection??available[0]
    if(!candidate){setError('No additional discovered source objects are available. Refresh source structures or remove an existing object.');return}
    const {profile,binding,object}=candidate
    const source={connection_id:profile.id,structure_version:profile.structure_version,database:binding.name,schema:object.schema??'',object_name:object.name,fields:[],alias:`source_${selected.length+1}`}
    const next=[...selected,source]
    const nextJoins=selected.length?[...joins,{left_alias:selected[0].alias,left_field:'',right_alias:source.alias,right_field:'',join_type:'inner' as const,cardinality:'unknown' as const}]:joins
    setSelected(next);setJoins(nextJoins);setPreview(null);onPlanChange({sources:next,joins:nextJoins})
  }
  function updateSource(index:number,patch:Partial<Pick>){const next=selected.map((row,i)=>i===index?{...row,...patch}:row);const identityChanged=['connection_id','database','schema','object_name'].some(key=>key in patch);const changedAlias=selected[index]?.alias;const nextJoins=identityChanged?joins.map(join=>join.left_alias===changedAlias?{...join,left_field:''}:join.right_alias===changedAlias?{...join,right_field:''}:join):joins;setSelected(next);setJoins(nextJoins);setPreview(null);onPlanChange({sources:next,joins:nextJoins})}
  function updateJoin(index:number,patch:Partial<FederatedJoinSelection>){const next=joins.map((join,i)=>i===index?{...join,...patch}:join);setJoins(next);setPreview(null);onPlanChange({sources:selected,joins:next})}
  function removeSource(index:number){const next=selected.filter((_,i)=>i!==index);const nextJoins=next.slice(1).map(source=>({left_alias:next[0].alias,left_field:'',right_alias:source.alias,right_field:'',join_type:'inner' as const,cardinality:'unknown' as const}));setSelected(next);setJoins(nextJoins);onPlanChange({sources:next,joins:nextJoins});setPreview(null)}
  function profileFor(row:Pick){return profiles.find(profile=>profile.id===row.connection_id)}
  function bindingFor(row:Pick){return profileFor(row)?.databases.find(binding=>binding.name===row.database)}
  function objectsFor(row:Pick){return bindingFor(row)?.objects??[]}
  async function validate(){
    setError('');setPreview(null)
    try{
      if(selected.length<2)throw Error('Add at least two source objects.')
      if(joins.length!==selected.length-1||joins.some(join=>!join.left_field||!join.right_field))throw Error('Choose a join key for each source.')
      setBusy(true)
      const result=await apiRequest<Preview>(`/workspaces/${workspaceId}/federated-plans/validate`,role,{method:'POST',body:JSON.stringify({sources:selected,joins,row_limit:1000})})
      setPreview(result)
    }catch(cause){setError(cause instanceof Error?cause.message:'Unable to validate this plan')}
    finally{setBusy(false)}
  }
  return <section className="panel federated-plan-builder">
    <header className="federated-builder-header">
      <div className="federated-builder-heading"><h2>Select source objects and fields</h2><p>Add multiple objects from the same connection, or combine connections. Choose the fields and join keys for each object.</p></div>
      <div className="federated-builder-actions"><span className="federated-source-count">{selected.length} of 10 objects</span><button type="button" className="button button-confirm" onClick={addSource} disabled={selected.length>=10||profiles.length===0}><Plus size={15}/>Add object</button></div>
    </header>
    {!selected.length&&<p className="federated-empty-state">Add the first object to get started.</p>}
    <div className="federated-source-list">{selected.map((item,index)=>{
      const profile=profileFor(item),object=objectsFor(item).find(ref=>ref.name===item.object_name&&(!ref.schema||ref.schema===item.schema));
      const objects=objectsFor(item),selectedCount=item.fields.length,query=fieldSearch[item.alias]??''
      const visibleFields=(object?.fields??[]).filter(field=>`${field.name} ${field.type??''}`.toLocaleLowerCase().includes(query.toLocaleLowerCase()))
      const objectValue=(ref:ObjectRef)=>`${ref.schema??''}::${ref.name}`
      return <article className="federated-source-card" key={item.alias}>
        <header className="federated-source-card-header"><strong>Object {index+1}</strong><span>{profile?.name??'Choose a connection'}</span><button type="button" className="federated-remove-source" onClick={()=>removeSource(index)} aria-label={`Remove ${profile?.name??item.alias}`}><Trash2 size={14}/><span>Remove object</span></button></header>
        <div className="federated-source-selectors">
          <label className="form-field">Connection<select value={item.connection_id} onChange={event=>{const nextProfile=profiles.find(row=>row.id===event.target.value),nextBinding=nextProfile?.databases[0],ref=nextBinding?.objects[0];updateSource(index,{connection_id:event.target.value,structure_version:nextProfile?.structure_version??1,database:nextBinding?.name??'',schema:ref?.schema??'',object_name:ref?.name??'',fields:[]})}}><option value="">Choose connection</option>{profiles.filter(row=>['postgresql','mysql'].includes(row.connector_type)).map(row=><option value={row.id} key={row.id}>{row.name} · {row.connector_type.toUpperCase()}</option>)}</select></label>
          <label className="form-field">Database<select value={item.database} onChange={event=>{const nextBinding=profile?.databases.find(row=>row.name===event.target.value),ref=nextBinding?.objects[0];updateSource(index,{database:event.target.value,schema:ref?.schema??'',object_name:ref?.name??'',fields:[]})}}><option value="">Choose database</option>{profile?.databases.map(row=><option key={row.name} value={row.name}>{row.name} · {row.objects.length} objects</option>)}</select></label>
          <label className="form-field">Object<select value={object?objectValue(object):''} onChange={event=>{const ref=objects.find(row=>objectValue(row)===event.target.value);updateSource(index,{object_name:ref?.name??'',schema:ref?.schema??'',fields:[]})}}><option value="">Choose object</option>{objects.map(ref=><option value={objectValue(ref)} key={objectValue(ref)}>{ref.schema?`${ref.schema}.`:''}{ref.name} · {ref.fields.length} fields</option>)}</select></label>
        </div>
        <fieldset className="federated-fields">
          <legend>Fields <small>{selectedCount} selected · {object?.fields.length??0} available</small></legend>
          <div className="federated-fields-toolbar"><label className="federated-field-search"><Search size={14}/><input type="search" value={query} onChange={event=>setFieldSearch(current=>({...current,[item.alias]:event.target.value}))} placeholder="Search fields" aria-label={`Search fields in ${item.alias}`}/></label><div><button type="button" className="text-button" disabled={!visibleFields.length} onClick={()=>updateSource(index,{fields:[...new Set([...item.fields,...visibleFields.map(field=>field.name)])]})}>Select all</button><button type="button" className="text-button" disabled={!item.fields.length} onClick={()=>updateSource(index,{fields:[]})}>Clear</button></div></div>
          {!object?<p className="federated-field-empty">Choose an object to view its fields.</p>:visibleFields.length?<div className="federated-field-grid">{visibleFields.map(field=><label className={`federated-field-option${item.fields.includes(field.name)?' is-selected':''}`} key={field.name}><input type="checkbox" checked={item.fields.includes(field.name)} onChange={event=>updateSource(index,{fields:event.target.checked?[...item.fields,field.name]:item.fields.filter(name=>name!==field.name)})}/><span className="federated-field-check" aria-hidden="true">{item.fields.includes(field.name)&&<Check size={12}/>}</span><span className="federated-field-copy"><strong>{field.name}</strong><small>{field.type??'Unknown type'}{field.primary_key?' · Key':''}</small></span></label>)}</div>:<p className="federated-field-empty">No matching fields.</p>}
        </fieldset>
      </article>
    })}</div>
    {selected.length>=2&&<section className="federated-joins-panel"><div className="federated-joins-heading"><h3>Join objects</h3><span>Choose matching fields between objects</span></div>
      <div className="federated-joins-list">{selected.slice(1).map((item,index)=>{const join=joins[index],left=selected.find(source=>source.alias===join?.left_alias)??selected[0],right=selected.find(source=>source.alias===join?.right_alias)??item,leftProfile=profileFor(left),rightProfile=profileFor(right);return <article className="federated-join-card" key={`${left.alias}-${right.alias}`}>
        <div className="federated-join-route"><span className="federated-join-node">{leftProfile?.name??left.alias}<small>{left.database}.{left.schema}.{left.object_name}</small></span><ArrowRight size={16}/><span className="federated-join-node">{rightProfile?.name??right.alias}<small>{right.database}.{right.schema}.{right.object_name}</small></span></div>
        <div className="federated-join-selectors"><label className="form-field">Left key<select value={join?.left_field??''} onChange={event=>updateJoin(index,{left_alias:left.alias,left_field:event.target.value,right_alias:right.alias,right_field:join?.right_field??''})}><option value="">Choose field</option>{left.fields.map(name=><option key={name} value={name}>{name}</option>)}</select></label><span className="federated-equals">=</span><label className="form-field">Right key<select value={join?.right_field??''} onChange={event=>updateJoin(index,{left_alias:left.alias,left_field:join?.left_field??'',right_alias:right.alias,right_field:event.target.value})}><option value="">Choose field</option>{right.fields.map(name=><option key={name} value={name}>{name}</option>)}</select></label><label className="form-field">Join type<select value={join?.join_type??'inner'} onChange={event=>updateJoin(index,{join_type:event.target.value as FederatedJoinSelection['join_type']})}><option value="inner">Inner</option><option value="left">Left</option></select></label><label className="form-field">Expected relationship<select value={join?.cardinality??'unknown'} onChange={event=>updateJoin(index,{cardinality:event.target.value as FederatedJoinSelection['cardinality']})}><option value="unknown">Not specified</option><option value="one_to_one">One to one</option><option value="one_to_many">One to many</option><option value="many_to_one">Many to one</option></select></label></div>
      </article>})}</div>
    </section>}
    <footer className="federated-builder-footer"><div className="federated-plan-status">{selected.length>=2?`${selected.length} sources · ${joins.length} joins · ${selected.reduce((count,source)=>count+source.fields.length,0)} fields selected`:'Add at least two sources and choose their fields.'}</div><button type="button" className="button button-primary" disabled={busy||selected.length<2||selected.some(source=>!source.fields.length)||joins.length!==selected.length-1||joins.some(join=>!join.left_field||!join.right_field)} onClick={()=>void validate()}>{busy?'Validating…':'Validate plan'}</button></footer>
    {error&&<div className="alert alert-warning federated-plan-error" role="alert">{error}</div>}
    {preview&&<div className="federated-preview" role="status"><header className="federated-preview-header"><span><Check size={16}/></span><div><h3>Plan validated</h3><p>Review the source reads and join configuration before continuing.</p></div><strong>{preview.sources.length} sources</strong></header><div className="federated-preview-stats"><div><small>Read limit</small><strong>{preview.row_limit_per_source.toLocaleString()} rows per source</strong></div><div><small>Join location</small><strong>{preview.join_location.replaceAll('_',' ')}</strong></div><div><small>Estimates</small><strong>{preview.estimates_available?'Available':'Not available'}</strong></div></div><div className="federated-preview-sources">{preview.sources.map(source=><article key={source.alias}><div><Database size={15}/><strong>{source.connection_name}</strong><code>{source.alias}</code></div><p>{source.database} · {source.schema} · {source.object}</p><small>{source.fields.length} fields · {source.fields.join(', ')||'none selected'}</small><details><summary>Read details</summary><code>{source.read_template}</code></details></article>)}</div>{preview.joins.length>0&&<div className="federated-preview-join-summary"><strong>Join configuration</strong>{preview.joins.map((join,index)=><span key={index}>{join.left_alias}.{join.left_field} <b>{join.join_type.toUpperCase()} JOIN</b> {join.right_alias}.{join.right_field} <small>{join.cardinality.replaceAll('_',' ')}</small></span>)}</div>}{preview.warnings.length>0&&<ul className="federated-preview-warnings">{preview.warnings.map((warning,index)=><li key={index}>{warning}</li>)}</ul>}<p className="federated-preview-disclosure">{preview.disclosure}</p></div>}
  </section>
}
