import { useEffect, useState } from 'react'
import { Check, Plus, RefreshCw, Save } from 'lucide-react'
import { apiRequest, type HumanRole } from './api'
import { FederatedPlanBuilder, type FederatedPlanDraft } from './FederatedPlanBuilder'
import './federated-connections.css'

type Profile = {id:string;name:string;connector_type:string;environment?:string;structure_version:number;databases:Array<{name:string;objects:Array<{name:string;schema?:string;fields:Array<{name:string;type?:string;primary_key?:boolean}>}>}>}
type SavedPlan = {id:string;name:string;state:'draft'|'saved';plan_data:Record<string,unknown>;updated_at:string}
const emptyPlan:FederatedPlanDraft={sources:[],joins:[]}

export default function FederatedConnectionPlans({workspaceId,role}:{workspaceId:string;role:HumanRole}){
  const [profiles,setProfiles]=useState<Profile[]>([])
  const [plans,setPlans]=useState<SavedPlan[]>([])
  const [selectedId,setSelectedId]=useState('')
  const [name,setName]=useState('')
  const [plan,setPlan]=useState<FederatedPlanDraft>(emptyPlan)
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const [notice,setNotice]=useState('')
  const [refreshToken,setRefreshToken]=useState(0)
  useEffect(()=>{
    let active=true
    Promise.all([
      apiRequest<Profile[]>(`/workspaces/${workspaceId}/source-data-sources`,role),
      apiRequest<SavedPlan[]>(`/workspaces/${workspaceId}/source-plans`,role),
    ]).then(([sources,saved])=>{if(!active)return;setProfiles(sources.filter(item=>['postgresql','mysql'].includes(item.connector_type)));setPlans(saved.filter(item=>item.plan_data.kind==='federated_config'))})
      .catch(cause=>{if(active)setError(cause instanceof Error?cause.message:'Unable to load federated sources')})
    return()=>{active=false}
  },[workspaceId,role,refreshToken])
  function selectPlan(id:string){
    setSelectedId(id);setError('');setNotice('')
    const saved=plans.find(item=>item.id===id)
    if(!saved){setName('');setPlan(emptyPlan);return}
    setName(saved.name)
    const raw=saved.plan_data.federatedPlan as FederatedPlanDraft|undefined
    setPlan(raw?{sources:raw.sources??[],joins:raw.joins??[]}:emptyPlan)
  }
  async function savePlan(){
    setBusy(true);setError('');setNotice('')
    try{
      if(name.trim().length<2)throw Error('Enter a configuration name with at least two characters.')
      if(plan.sources.length<2||plan.sources.some(source=>!source.fields.length))throw Error('Choose fields from at least two source objects.')
      if(plan.joins.length!==plan.sources.length-1||plan.joins.some(join=>!join.left_field||!join.right_field))throw Error('Complete the join keys for each source.')
      const body={name:name.trim(),state:'saved' as const,plan_data:{kind:'federated_config',federatedPlan:plan}}
      const saved=selectedId
        ?await apiRequest<SavedPlan>(`/source-plans/${selectedId}`,role,{method:'PATCH',body:JSON.stringify(body)})
        :await apiRequest<SavedPlan>(`/workspaces/${workspaceId}/source-plans`,role,{method:'POST',body:JSON.stringify(body)})
      setPlans(current=>[saved,...current.filter(item=>item.id!==saved.id)])
      setSelectedId(saved.id);setNotice('Federated configuration saved and available in Source to Data Step 1.')
    }catch(cause){setError(cause instanceof Error?cause.message:'Unable to save federated configuration')}
    finally{setBusy(false)}
  }
  return <section className="federated-config-tab">
    <header className="federated-config-header"><div><h2>Federated joins</h2><p>Save a reusable join across approved PostgreSQL and MySQL source objects.</p></div><div className="federated-config-header-actions"><button type="button" className="button button-secondary" onClick={()=>setRefreshToken(value=>value+1)}><RefreshCw size={15}/>Refresh sources</button><button type="button" className="button button-secondary" onClick={()=>selectPlan('')}><Plus size={15}/>New configuration</button></div></header>
    <div className="federated-config-controls"><label className="form-field">Saved federated configuration<select value={selectedId} onChange={event=>selectPlan(event.target.value)}><option value="">New configuration</option>{plans.map(item=><option value={item.id} key={item.id}>{item.name}</option>)}</select></label><label className="form-field">Configuration name<input value={name} onChange={event=>setName(event.target.value)} placeholder="e.g. Customer account join"/></label><button type="button" className="button button-confirm" onClick={()=>void savePlan()} disabled={busy}><Save size={15}/>{busy?'Saving…':selectedId?'Save changes':'Save configuration'}</button></div>
    {error&&<div className="alert alert-warning" role="alert">{error}</div>}{notice&&<div className="federated-config-notice" role="status"><Check size={15}/>{notice}</div>}
    <FederatedPlanBuilder workspaceId={workspaceId} role={role} profiles={profiles} initialPlan={plan} onPlanChange={setPlan}/>
  </section>
}
