import { useEffect, useState, type FormEvent } from 'react'
import { AlertCircle, Check, Database, Eye, Pencil, ShieldCheck, Trash2 } from 'lucide-react'
import { apiRequest, type HumanRole } from './api'

type Connection = {id: string; name: string; connector_type: string; operation_scope: 'read_only' | 'write_only' | 'read_write'; status: string}
type GrantableMember = {subject: string; display_name: string | null; email: string | null; role: HumanRole}

export default function SsdGrantForm({workspaceId, role, members, onGranted}: {
  workspaceId: string
  role: HumanRole
  members: GrantableMember[]
  onGranted: () => Promise<void>
}) {
  const [connections, setConnections] = useState<Connection[]>([])
  const [subject, setSubject] = useState('')
  const [connectionId, setConnectionId] = useState('')
  const [select, setSelect] = useState(false)
  const [write, setWrite] = useState(false)
  const [remove, setRemove] = useState(false)
  const [expiry, setExpiry] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [failed, setFailed] = useState(false)
  const selectedConnection = connections.find(connection => connection.id === connectionId)

  useEffect(() => {
    let active = true
    apiRequest<Connection[]>(`/workspaces/${workspaceId}/connections?include_disabled=true`, role)
      .then(rows => { if (active) setConnections(rows.filter(connection => connection.connector_type === 'postgresql')) })
      .catch(error => { if (active) { setFailed(true); setMessage((error as Error).message) } })
    return () => { active = false }
  }, [workspaceId, role])

  async function save(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setMessage('')
    setFailed(false)
    try {
      const permissions = [
        ...(select ? ['ssd:select'] : []),
        ...(write ? ['ssd:insert', 'ssd:update'] : []),
        ...(remove ? ['ssd:delete'] : []),
      ]
      await apiRequest(`/workspaces/${workspaceId}/grants`, role, {
        method: 'POST',
        body: JSON.stringify({
          subject, scope_type: 'connection', scope_id: connectionId, permissions,
          valid_until: expiry ? new Date(expiry).toISOString() : null,
          reason: 'Self Servicing Data connection operations',
        }),
      })
      await onGranted()
      setMessage('Access granted. The grant is listed below and recorded in the audit trail.')
      setSelect(false)
      setWrite(false)
      setRemove(false)
    } catch (error) {
      setFailed(true)
      setMessage((error as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return <section className="panel ssd-grant-panel">
    <header className="ssd-grant-heading">
      <span className="ssd-grant-icon"><ShieldCheck size={19}/></span>
      <div><span className="ssd-kicker">CONNECTION ACCESS</span><h2>Self Servicing Data</h2><p>Choose what a QA Engineer can do on one PostgreSQL connection.</p></div>
    </header>
    <form onSubmit={event => void save(event)}>
      <div className="ssd-grant-targets">
        <label className="form-field">QA Engineer
          <select required value={subject} onChange={event => setSubject(event.target.value)}>
            <option value="">Select a QA user</option>
            {members.filter(member => member.role === 'QA Engineer').map(member => <option key={member.subject} value={member.subject}>{member.display_name ?? member.email ?? member.subject}</option>)}
          </select>
        </label>
        <label className="form-field">PostgreSQL connection
          <select required value={connectionId} onChange={event => setConnectionId(event.target.value)}>
            <option value="">Select a connection</option>
            {connections.map(connection => <option key={connection.id} value={connection.id}>{connection.name} · {connection.status}</option>)}
          </select>
        </label>
      </div>
      {selectedConnection && <div className="ssd-grant-connection-summary">
        <Database size={15}/><span>{selectedConnection.name}</span>
        <span className={`ssd-scope-badge ${selectedConnection.operation_scope === 'read_write' ? 'is-write' : ''}`}>
          {selectedConnection.operation_scope === 'read_write' ? 'Read and write' : selectedConnection.operation_scope === 'write_only' ? 'Write only' : 'Read only'}
        </span>
      </div>}
      <fieldset className="ssd-permission-set">
        <legend>Allowed operations</legend>
        <div className="ssd-permission-grid">
          <label className={`ssd-permission-card ${select ? 'is-selected' : ''}`}>
            <input type="checkbox" checked={select} onChange={event => setSelect(event.target.checked)}/>
            <span className="ssd-permission-icon"><Eye size={17}/></span>
            <span className="ssd-permission-copy"><strong>Select</strong><small>Run read queries and inspect samples.</small></span>
            <span className="ssd-check-mark" aria-hidden="true"><Check size={13}/></span>
          </label>
          <label className={`ssd-permission-card ${write ? 'is-selected' : ''}`}>
            <input type="checkbox" checked={write} onChange={event => setWrite(event.target.checked)}/>
            <span className="ssd-permission-icon"><Pencil size={17}/></span>
            <span className="ssd-permission-copy"><strong>Insert and update</strong><small>Run statements that create or change records.</small></span>
            <span className="ssd-check-mark" aria-hidden="true"><Check size={13}/></span>
          </label>
          <label className={`ssd-permission-card ${remove ? 'is-selected is-destructive' : ''}`}>
            <input type="checkbox" checked={remove} onChange={event => setRemove(event.target.checked)}/>
            <span className="ssd-permission-icon"><Trash2 size={17}/></span>
            <span className="ssd-permission-copy"><strong>Delete</strong><small>Allow statements that remove records.</small></span>
            <span className="ssd-check-mark" aria-hidden="true"><Check size={13}/></span>
          </label>
        </div>
      </fieldset>
      <div className="ssd-grant-footer">
        <label className="form-field ssd-grant-expiry">Access expires <span>(optional)</span><input type="datetime-local" value={expiry} onChange={event => setExpiry(event.target.value)}/></label>
        <button className="button button-confirm" disabled={busy || !(select || write || remove)}><ShieldCheck size={15}/>{busy ? 'Saving access…' : 'Grant access'}</button>
      </div>
      {selectedConnection && selectedConnection.status !== 'enabled' && <p className="ssd-grant-note"><AlertCircle size={15}/> This connection is {selectedConnection.status}; enable it before using it in SSD.</p>}
      {selectedConnection && write && selectedConnection.operation_scope === 'read_only' && <p className="ssd-grant-note"><AlertCircle size={15}/> This connection is read only. An Admin or Test Data Engineer must change its operation scope, and its database login must permit writes.</p>}
      {selectedConnection && write && selectedConnection.operation_scope !== 'read_only' && <p className="ssd-grant-note"><AlertCircle size={15}/> TIGR grants access by operation. PostgreSQL still checks the saved login's database privileges when a query runs.</p>}
      {message && <p className={`ssd-grant-result ${failed ? 'is-error' : 'is-success'}`} role={failed ? 'alert' : 'status'}>{message}</p>}
    </form>
  </section>
}
