import { Fragment, useCallback, useEffect, useState } from 'react'
import { Activity, RefreshCw, Search } from 'lucide-react'
import { apiRequest, type HumanRole } from './api'

type JobRow = {id:string;job_type:string;status:string;progress:number;workspace_id:string|null;actor_role:string|null;request_id:string;safe_summary:string|null;created_at:string|null;dataset_id?:string;dataset_reference_code?:string;dataset_name?:string}
type JobDetail = JobRow & {steps:Array<{id:string;name:string;status:string;safe_summary:string|null;created_at:string|null}>}

export default function JobsPage({role,workspaceId}:{role:HumanRole;workspaceId:string}){
  const [items,setItems]=useState<JobRow[]>([])
  const [selected,setSelected]=useState<JobDetail|null>(null)
  const [status,setStatus]=useState('')
  const [search,setSearch]=useState('')
  const [query,setQuery]=useState('')
  const [page,setPage]=useState(1)
  const [total,setTotal]=useState(0)
  const [hasMore,setHasMore]=useState(false)
  const [loading,setLoading]=useState(false)
  const [error,setError]=useState('')
  const load=useCallback(async()=>{
    setLoading(true);setError('')
    const params=new URLSearchParams({page:String(page),page_size:'20'})
    if(workspaceId)params.set('workspace_id',workspaceId)
    if(status)params.set('status',status)
    if(query.trim())params.set('search',query.trim())
    try{const result=await apiRequest<{items:JobRow[];total:number;has_more:boolean}>(`/jobs?${params}`,role);setItems(result.items);setTotal(result.total);setHasMore(result.has_more)}
    catch(cause){setError(cause instanceof Error?cause.message:'Unable to load jobs')}
    finally{setLoading(false)}
  },[workspaceId,status,query,page,role])
  useEffect(()=>{void load()},[load])
  async function inspect(job:JobRow){
    if(selected?.id===job.id){setSelected(null);return}
    try{setSelected(await apiRequest<JobDetail>(`/jobs/${job.id}`,role))}
    catch(cause){setError(cause instanceof Error?cause.message:'Unable to load job detail')}
  }
  return <div className="datasets-page">
    <section className="dataset-intro panel"><div><div className="eyebrow">WORKFLOW EXECUTION</div><h2>Jobs</h2><p>Review role-scoped job status, progress, safe summaries, and execution steps.</p></div><button className="button button-secondary" onClick={()=>void load()}><RefreshCw size={15}/>Refresh</button></section>
    <section className="panel dataset-catalog"><div className="dataset-toolbar"><div><h2>Job inventory</h2><p>{total} visible jobs · filtered before count and paging</p></div><Activity size={18}/></div>
      <form className="dataset-filters" onSubmit={event=>{event.preventDefault();setPage(1);setQuery(search)}}><label className="dataset-search"><Search size={16}/><input aria-label="Search jobs" value={search} onChange={event=>setSearch(event.target.value)} placeholder="Search job type, status, or summary"/></label><select aria-label="Filter job status" value={status} onChange={event=>{setPage(1);setStatus(event.target.value)}}><option value="">All statuses</option>{['queued','validating','running','succeeded','succeeded_with_warnings','failed','cancel_requested','cancelled','expired'].map(item=><option key={item} value={item}>{humanize(item)}</option>)}</select><button className="button button-confirm">Filter</button></form>
      {error&&<div className="alert alert-warning">{error}</div>}
      <div className="table-shell"><table><thead><tr><th>Job</th><th>Status</th><th>Type</th><th>Progress</th><th>Role</th><th>Started</th></tr></thead><tbody>{items.map(item=><Fragment key={item.id}><tr className="job-row"><td><button className="dataset-name-button job-dataset-button" aria-expanded={selected?.id===item.id} onClick={()=>void inspect(item)}><strong className="job-unique-id" title={item.id}>{item.id}</strong><small className="job-dataset-name">{item.dataset_name||'No data set linked'}</small><small className="job-dataset-reference">{item.dataset_reference_code||'Data set ID unavailable'}</small></button></td><td><span className={`status-badge ${item.status==='failed'?'status-warning':item.status==='succeeded'?'status-success':'status-neutral'}`}><i/>{humanize(item.status)}</span></td><td>{humanize(item.job_type)}</td><td><progress max={100} value={item.progress}/><small>{item.progress}%</small></td><td>{item.actor_role||'Service'}</td><td>{item.created_at?new Date(item.created_at).toLocaleString():'—'}</td></tr>{selected?.id===item.id&&<tr><td colSpan={6}><div className="job-detail"><strong>Execution steps</strong>{selected.steps.length?selected.steps.map(step=><div key={step.id}><span>{humanize(step.name)}</span><span>{humanize(step.status)}</span><small>{step.safe_summary||'No additional safe detail'}</small></div>):<p>No execution steps have been recorded.</p>}</div></td></tr>}</Fragment>)}{!loading&&!items.length&&<tr><td colSpan={6} className="table-empty">No jobs match this workspace and filter.</td></tr>}{loading&&<tr><td colSpan={6} className="table-empty">Loading authorized jobs…</td></tr>}</tbody></table></div>
      <div className="pagination"><span>Page {page} of {Math.max(1,Math.ceil(total/20))}</span><div><button className="button button-secondary compact" disabled={page<=1||loading} onClick={()=>setPage(value=>value-1)}>Previous</button><button className="button button-secondary compact" disabled={!hasMore||loading} onClick={()=>setPage(value=>value+1)}>Next</button></div></div>
    </section>
  </div>
}

function humanize(value:string){return value.replaceAll('_',' ').replace(/\b\w/g,char=>char.toUpperCase())}
