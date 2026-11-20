import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { ArrowRight, CircleAlert, Database, ShieldCheck } from 'lucide-react'
import { getDevelopmentLoginOptions, loginDevelopmentUser, type DevelopmentAccess } from './api'
import './login.css'

export default function LoginPage({onLogin}:{onLogin:(session:DevelopmentAccess)=>void}) {
  const [options,setOptions]=useState<DevelopmentAccess[]>([])
  const [subject,setSubject]=useState('')
  const [workspaceId,setWorkspaceId]=useState('')
  const [password,setPassword]=useState('')
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const users=useMemo(()=>Array.from(new Map(options.map(item=>[item.subject,item])).values()),[options])
  const workspaces=useMemo(()=>options.filter(item=>item.subject===subject),[options,subject])
  const selectedOption=useMemo(()=>workspaces.find(item=>item.workspace_id===workspaceId),[workspaces,workspaceId])

  useEffect(()=>{
    let active=true
    getDevelopmentLoginOptions()
      .then(rows=>{if(active){setOptions(rows);if(rows.length){setSubject(rows[0].subject);setWorkspaceId(rows[0].workspace_id)}}})
      .catch(cause=>{if(active)setError(cause instanceof Error?cause.message:'Unable to load available development users')})
    return()=>{active=false}
  },[])

  async function submit(event:FormEvent){
    event.preventDefault()
    if(!selectedOption)return
    setBusy(true);setError('')
    try{
      const session=await loginDevelopmentUser(selectedOption.subject,selectedOption.workspace_id,password)
      onLogin(session)
    }catch(cause){setError(cause instanceof Error?cause.message:'Sign in failed')}
    finally{setBusy(false)}
  }

  return <main className="login-screen">
    <section className="login-card" aria-labelledby="login-title">
      <header className="login-brand"><div className="login-mark">T</div><div><strong>TIGR</strong><span>Test Data Intelligence, Generation<br/>and Readiness platform</span></div></header>
      <div className="login-welcome"><span className="login-eyebrow"><Database size={14}/> TEST DATA WORKSPACE</span><h1 id="login-title">Sign in to TIGR</h1><p>Choose an active workspace identity to continue.</p></div>
      {error&&<div className="login-error" role="alert"><CircleAlert size={16}/><span>{error}</span></div>}
      <form onSubmit={event=>void submit(event)}>
        <label className="login-field">User
          <select required value={subject} onChange={event=>{const nextSubject=event.target.value;setSubject(nextSubject);setWorkspaceId(options.find(item=>item.subject===nextSubject)?.workspace_id??'')}} disabled={!users.length||busy}>
            {options.length===0&&<option value="">{error?'No development access available':'Loading active users…'}</option>}
            {users.map(item=><option key={item.subject} value={item.subject}>{item.display_name}</option>)}
          </select>
        </label>
        <label className="login-field">Password
          <input type="password" autoComplete="current-password" value={password} onChange={event=>setPassword(event.target.value)} placeholder="Leave blank for local development" disabled={busy}/>
          <small>Local development users do not have passwords. Leave this field empty.</small>
        </label>
        <label className="login-field">Workspace
          <select required value={workspaceId} onChange={event=>setWorkspaceId(event.target.value)} disabled={!workspaces.length||busy}>
            {workspaces.map(item=><option key={item.workspace_id} value={item.workspace_id}>{item.workspace_name}</option>)}
          </select>
        </label>
        <button className="login-submit" type="submit" disabled={busy||!selectedOption}>
          {busy?'Signing in…':'Continue'}<ArrowRight size={16}/>
        </button>
      </form>
      <div className="login-security-note"><ShieldCheck size={15}/><span>Passwordless sign-in is available only in the local development environment.</span></div>
    </section>
    <footer className="login-footer"><span>TIGR · Test Data Intelligence, Generation and Readiness</span><span>Development sign-in</span></footer>
  </main>
}
