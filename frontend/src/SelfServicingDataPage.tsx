import { createPortal } from 'react-dom'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Check, Code2, Database, ShieldCheck, TriangleAlert, Play, Save, Rows3 } from 'lucide-react'
import { apiRequest, type HumanRole } from './api'
import { highlightSql } from './sqlHighlight'
import './scenario.css'
import './ssd.css'
import './dataset-refresh.css'
import DatasetRefreshButton from './DatasetRefreshButton'

type Connection = {entity_locking_enabled: boolean; operation_scope: string; available: boolean; unavailable_reasons: string[]; granted_operations: string[]; id: string; name: string; environment: string; connector: string; operations: string[]; databases: string[]; schemas: Record<string, string[]>}
type Execution = {id: string; job_id: string; kind: string; job_status: string; outcome: string; evidence: {affected_rows?: number; returned_rows?: number; started_at?: string; ended_at?: string}}
type Draft = {id: string; name: string; description: string | null; visibility: string; revision: number; state: string; connection_id: string; database: string; sql: string; parameters: Record<string, unknown>; validation: {row_limit?: number; operation?: string; operations?: string[]; objects?: string[][]}; dataset_id: string | null; dataset_version_number: number | null; generation_settings: {maximum_records?: number; output_format?: string}; executions: Execution[]}
type Sample = {rows: Record<string, unknown>[]; fields: {field: string; data_type: string}[]; privacy: string; revision: number; expires_at: string; generated_at: string}
type Job = {id: string; status: string; safe_summary: string | null; progress: number}

export default function SelfServicingDataPage({role, workspaceId}: {role: HumanRole; workspaceId: string; piiMaskingEnabled: boolean}) {
  const [connections, setConnections] = useState<Connection[]>([])
  const [drafts, setDrafts] = useState<{id: string; name: string; state: string}[]>([])
  const [history, setHistory] = useState<{revision: number; sql: string; operation: string; executions: {job_id: string; outcome: string; kind: string}[]}[]>([])
  const [draft, setDraft] = useState<Draft | null>(null)
  const [connectionId, setConnectionId] = useState('')
  const [database, setDatabase] = useState('')
  const [requestedCount, setRequestedCount] = useState(1000)
  const [outputFormat, setOutputFormat] = useState('json')
  const [name, setName] = useState('')
  const [nameError, setNameError] = useState('')
  const [description, setDescription] = useState('')
  const [visibility, setVisibility] = useState('private')
  const [sql, setSql] = useState('SELECT * FROM schema_name.table_name')
  const [parameters, setParameters] = useState('{}')
  const [sample, setSample] = useState<Sample | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  const [jobId, setJobId] = useState('')
  const publishAfterSave = useRef(false)
  const [jobKind, setJobKind] = useState<'execute' | 'sample' | 'datasets' | ''>('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [acknowledgeUnknown, setAcknowledgeUnknown] = useState(false)
  const [reconciliationReference, setReconciliationReference] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [executeConfirmOpen, setExecuteConfirmOpen] = useState(false)
  const executeDialog = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const dialog = executeDialog.current
    if (executeConfirmOpen && dialog && !dialog.open) dialog.showModal()
    if (!executeConfirmOpen && dialog?.open) dialog.close()
  }, [executeConfirmOpen])
  const [dirty, setDirty] = useState(false)
  const highlight = useRef<HTMLPreElement>(null)
  const availableConnections = connections.filter(c => c.available)
  const unavailableConnections = connections.filter(c => !c.available)
  const selected = availableConnections.find(c => c.id === connectionId)
  const running = jobId !== '' && !['succeeded', 'failed', 'cancelled', 'expired'].includes(job?.status ?? '')
  const execution = draft?.executions.filter(e => e.kind === 'query').at(-1)
  const mutation = ['insert', 'update', 'delete'].includes(draft?.validation.operation ?? '')

  async function load(id: string) {
    const d = await apiRequest<Draft>(`/ssd/drafts/${id}`, role)
    setDraft(d); setConnectionId(d.connection_id); setDatabase(d.database); setName(d.name)
    setRequestedCount(d.generation_settings.maximum_records ?? 1000); setOutputFormat(d.generation_settings.output_format ?? 'json')
    setDescription(d.description ?? ''); setVisibility(d.visibility); setSql(d.sql); setParameters(JSON.stringify(d.parameters, null, 2))
    publishAfterSave.current = false
    setDirty(false); setConfirmed(false); setSample(null); setJob(null)
    setJobId(''); setJob(null); setJobKind('')
    if (d.validation.operation === 'select' && ['sample_ready', 'ready_to_publish', 'published'].includes(d.state)) {
      try { setSample(await apiRequest<Sample>(`/ssd/drafts/${id}/sample`, role)) } catch (e) { setNotice((e as Error).message) }
    }
  }

  useEffect(() => {
    if (!workspaceId) { setLoading(false); return }
    let active = true
    let refreshing = false
    const refresh = async () => {
      if (refreshing) return
      refreshing = true
      try {
        const [c, d] = await Promise.all([apiRequest<Connection[]>(`/workspaces/${workspaceId}/ssd/connections?include_unavailable=true`, role), apiRequest<typeof drafts>(`/workspaces/${workspaceId}/ssd/drafts`, role)])
        if (active) { setConnections(c); setDrafts(d) }
      } catch (e) {
        if (active) { setConnections([]); setError((e as Error).message) }
      } finally { refreshing = false; if (active) setLoading(false) }
    }
    void refresh()
    const timer = window.setInterval(() => void refresh(), 15000)
    window.addEventListener('focus', refresh)
    return () => { active = false; window.clearInterval(timer); window.removeEventListener('focus', refresh) }
  }, [workspaceId, role])

  useEffect(() => {
    if (!draft) { setHistory([]); return }
    let active = true
    apiRequest<typeof history>(`/ssd/drafts/${draft.id}/revisions`, role).then(rows => { if (active) setHistory(rows) }).catch(() => { if (active) setHistory([]) })
    return () => { active = false }
  }, [draft?.id, draft?.revision, draft?.state, role])

  useEffect(() => {
    if (!jobId || !draft) return
    let active = true
    const id = draft.id
    const poll = async () => {
      try {
        const current = await apiRequest<Job>(`/jobs/${jobId}`, role)
        if (!active) return
        setJob(current)
        if (['succeeded', 'failed', 'cancelled', 'expired'].includes(current.status)) {
          window.clearInterval(timer)
          const d = await apiRequest<Draft>(`/ssd/drafts/${id}`, role)
          if (!active) return
          setDraft(d)
          if (current.status !== 'succeeded') publishAfterSave.current = false
          if (current.status === 'succeeded' && jobKind === 'execute' && d.validation.operation === 'select') {
            const read = d.executions.filter(item => item.kind === 'query').at(-1)
            if (read?.outcome === 'read_complete') {
              const queued = await apiRequest<{job_id: string}>(`/ssd/drafts/${id}/sample`, role, {method: 'POST', body: JSON.stringify({expected_revision: d.revision, idempotency_key: crypto.randomUUID()})})
              if (active) { setJob(null); setJobKind('sample'); setJobId(queued.job_id); setNotice('Query completed. Generating the five-row sample…') }
            }
          } else if (current.status === 'succeeded' && jobKind === 'sample') {
            const s = await apiRequest<Sample>(`/ssd/drafts/${id}/sample`, role)
            if (active) setSample(s)
          } else if (current.status === 'succeeded' && jobKind === 'datasets') {
            if (active && d.dataset_id) {
              setDrafts(await apiRequest<typeof drafts>(`/workspaces/${workspaceId}/ssd/drafts`, role))
              if (publishAfterSave.current) {
                publishAfterSave.current = false
                setBusy(true)
                try { await publishDataset(d) } finally { setBusy(false) }
              } else {
                setNotice(`Dataset saved as an unpublished draft: ${d.dataset_id}`)
              }
            }
          }
        }
      } catch (e) { if (active) { publishAfterSave.current = false; setError((e as Error).message); setJobId('') } }
    }
    const timer = window.setInterval(() => void poll(), 1500)
    void poll()
    return () => { active = false; window.clearInterval(timer) }
  }, [jobId, jobKind, role, draft?.id])

  useEffect(() => {
    if (!sample || !draft) return
    let active = true
    const recheck = () => {
      apiRequest<Sample>(`/ssd/drafts/${draft.id}/sample`, role)
        .then(current => { if (active && current.revision !== sample.revision) { setSample(null); setError('The draft revision changed. Reload it before continuing.') } })
        .catch(e => { if (active) { setSample(null); setError((e as Error).message) } })
    }
    const timer = window.setInterval(recheck, 5000)
    window.addEventListener('focus', recheck)
    return () => { active = false; window.clearInterval(timer); window.removeEventListener('focus', recheck) }
  }, [sample, draft?.id, role])

  // An expired preview is removed from the browser, even while this page remains open.
  useEffect(() => {
    if (!sample) return
    const timer = window.setTimeout(() => { setSample(null); setNotice('Sample expired. Regenerate it using a fresh SELECT execution.') }, Math.max(0, Date.parse(sample.expires_at) - Date.now()))
    return () => window.clearTimeout(timer)
  }, [sample])

  async function action(work: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('')
    try { await work() } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  function edited() { publishAfterSave.current = false; setDirty(true); setSample(null); setConfirmed(false); setJobId(''); setJob(null); setJobKind('') }
  async function save() {
    const parsed: unknown = JSON.parse(parameters)
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error('Parameters must be a JSON object of named scalar values.')
    const selectedDatabase = database || selected?.databases[0] || ''
    if (!selectedDatabase) throw new Error('The selected connection has no available database.')
    const d = await apiRequest<Draft>(draft ? `/ssd/drafts/${draft.id}` : `/workspaces/${workspaceId}/ssd/drafts`, role, {method: draft ? 'PATCH' : 'POST', body: JSON.stringify({name, description, visibility, connection_id: connectionId, database: selectedDatabase, sql, parameters: parsed, expected_revision: draft?.revision ?? 1})})
    setDraft(d); setRequestedCount(d.generation_settings.maximum_records ?? 1000); setOutputFormat(d.generation_settings.output_format ?? 'json'); setDirty(false); setJobId(''); setJob(null)
    setDrafts(await apiRequest<typeof drafts>(`/workspaces/${workspaceId}/ssd/drafts`, role))
    return d
  }
  async function saveSettings(targetDraft = draft) {
    if (!targetDraft) return
    await apiRequest(`/ssd/drafts/${targetDraft.id}/metadata`, role, {method: 'PATCH', body: JSON.stringify({expected_revision: targetDraft.revision, name, description, visibility, maximum_records: requestedCount, output_format: outputFormat})})
    setDraft({...targetDraft, name, description, visibility})
  }
  async function submit(kind: 'execute' | 'sample' | 'datasets', confirmMutation = confirmed) {
    if (!draft || dirty) throw new Error('Validate your current query before executing.')
    if (kind === 'execute') await saveSettings()
    const result = await apiRequest<{job_id: string}>(`/ssd/drafts/${draft.id}/${kind}`, role, {method: 'POST', body: JSON.stringify({expected_revision: draft.revision, idempotency_key: crypto.randomUUID(), confirm_mutation: confirmMutation, acknowledge_unknown: acknowledgeUnknown, reconciliation_reference: reconciliationReference || null})})
    setJob(null); setJobKind(kind); setJobId(result.job_id); if (kind !== 'datasets') setSample(null)
  }

  async function publishDataset(datasetDraft: Draft) {
    if (!datasetDraft.dataset_id || !datasetDraft.dataset_version_number) return
    await apiRequest(`/datasets/${datasetDraft.dataset_id}/visibility`, role, {method: 'PATCH', body: JSON.stringify({visibility})})
    await apiRequest(`/datasets/${datasetDraft.dataset_id}/versions/${datasetDraft.dataset_version_number}/publish`, role, {method: 'POST'})
    setDraft({...datasetDraft, name, description, visibility, state: 'published'})
    setDrafts(await apiRequest<typeof drafts>(`/workspaces/${workspaceId}/ssd/drafts`, role))
    setNotice(`Published · ${datasetDraft.dataset_id}`)
  }

  async function publishExecutedDataset() {
    if (!draft || dirty) throw new Error('Validate and execute the current query before publishing.')
    if (name.trim().length < 2) { setNameError('Enter a data set name with at least two characters.'); return }
    await saveSettings()
    if (draft.dataset_id && draft.state === 'ready_to_publish') {
      await publishDataset({...draft, name, description, visibility})
      return
    }
    const completed = draft.validation.operation === 'select'
      ? execution?.outcome === 'read_complete' && !!sample && sample.revision === draft.revision
      : mutation && execution?.outcome === 'committed'
    if (!completed) throw new Error('Execute the current query successfully before publishing.')
    publishAfterSave.current = true
    try {
      await submit('datasets')
      setNotice('Saving the executed dataset and publishing it automatically?')
    } catch (error) { publishAfterSave.current = false; throw error }
  }

  async function validateQuery() {
    if (!name.trim()) {
      setNameError('Enter a data set name before validating the query.')
      return
    }
    setNameError('')
    const d = dirty || !draft ? await save() : draft
    await saveSettings(d)
    const validation = await apiRequest<Draft['validation']>(`/ssd/drafts/${d.id}/validate`, role, {method: 'POST', body: JSON.stringify({expected_revision: d.revision, idempotency_key: crypto.randomUUID()})})
    setDraft({...d, validation, state: 'validated'})
    setDirty(false)
    setNotice('Query validated and saved automatically. Select Execute to run it.')
  }

  async function saveDataset() {
    if (name.trim().length < 2) {
      setNameError('Enter a data set name with at least two characters before saving.')
      return
    }
    setNameError('')
    if (!draft || dirty) {
      const saved = await save()
      setNotice(`Query draft saved (revision ${saved.revision}). Validate and execute it before publishing.`)
      return
    }
    await saveSettings()
    if (draft.dataset_id && ['ready_to_publish', 'published'].includes(draft.state)) {
      setNotice('Draft settings saved.')
      return
    }
    const canGenerate = draft.validation.operation === 'select'
      ? !!sample && sample.revision === draft.revision
      : ['insert', 'update', 'delete'].includes(draft.validation.operation ?? '') && execution?.outcome === 'committed'
    if (!canGenerate) {
      setNotice('Query draft saved. Validate and execute it before saving the publishable dataset.')
      return
    }
    await submit('datasets')
  }

  return <div className="page-stack ssd-page view-data-page">
    {createPortal(<dialog ref={executeDialog} className="dataset-modal dataset-refresh-dialog" aria-label="Confirm source changes" onCancel={() => setExecuteConfirmOpen(false)} onClose={() => setExecuteConfirmOpen(false)}><header className="dataset-modal-header"><h2>Execute source changes?</h2></header><div className="dataset-refresh-dialog-body"><p>This query will change records in {selected?.name} ({selected?.environment}). Committed changes cannot be undone by cancelling the job.</p></div><footer className="dataset-modal-actions"><button type="button" className="button button-secondary" autoFocus onClick={() => setExecuteConfirmOpen(false)}>Cancel</button><button type="button" className="button button-confirm" disabled={busy || running || dirty || !mutation} onClick={() => {setExecuteConfirmOpen(false); setConfirmed(true); void action(() => submit('execute', true))}}>Confirm execution</button></footer></dialog>, document.body)}
    <section className="view-data-intro ssd-view-intro">
      <div className="view-data-intro-copy"><span className="ssd-kicker">QA WORKSPACE · SELF SERVICING DATA</span><h1>Build a reusable dataset from SQL</h1><p>Run a SELECT, INSERT or UPDATE, then save and publish it for future refreshes.</p></div>
      <label className="view-data-dataset-picker ssd-connection-picker"><span>Connection</span><select value={connectionId} disabled={busy || running} onChange={e => {setConnectionId(e.target.value); setDatabase(connections.find(c => c.id === e.target.value)?.databases[0] ?? ''); edited()}} aria-label="Select an authorized connection"><option value="">Select a connection</option>{availableConnections.map(c => <option key={c.id} value={c.id}>{c.name} · {c.environment}</option>)}</select></label>
      <label className="view-data-dataset-picker ssd-resume-picker"><span>Resume draft</span><select value={draft?.id ?? ''} disabled={busy || running} onChange={e => {if (e.target.value) void action(() => load(e.target.value)); else {setDraft(null); setSample(null); setDirty(true); setJobId(''); setJob(null); setJobKind('')}}} aria-label="Resume an SSD draft"><option value="">Start a new dataset</option>{drafts.map(d => <option key={d.id} value={d.id}>{d.name} · {d.state}</option>)}</select></label>
      <label className="view-data-dataset-picker ssd-name-picker"><span>Data set name</span><input value={name} minLength={2} maxLength={180} disabled={busy || running || !!draft?.dataset_id} aria-invalid={!!nameError} aria-describedby={nameError?'ssd-dataset-name-error':undefined} onChange={e => {setName(e.target.value); if(e.target.value.trim())setNameError('')}} placeholder="e.g. Customer accounts"/>{nameError&&<small id="ssd-dataset-name-error" className="ssd-name-error" role="alert">{nameError}</small>}</label>
      <label className="view-data-dataset-picker ssd-visibility-picker"><span>Visibility</span><select value={visibility} disabled={busy || running || draft?.state === 'published'} onChange={e => setVisibility(e.target.value)}><option value="private">Private · only me</option><option value="public">Public · eligible workspace users</option></select></label>
    </section>
    {(draft || history.length > 0) && <div className="ssd-draft-toolbar">{draft && <span className={`ssd-revision-chip ${dirty ? 'is-dirty' : ''}`}>Revision {draft.revision} · {dirty ? 'Unsaved query changes' : draft.state.replaceAll('_', ' ')}</span>}{history.length > 0 && <details className="ssd-history"><summary>Query history <span>{history.length} revisions</span></summary>{history.map(h => <div className="ssd-history-entry" key={h.revision}><h3>Revision {h.revision} · {h.operation ?? 'Not validated'}</h3><pre>{h.sql}</pre>{h.executions.map(e => <p key={e.job_id}>{e.kind} · {e.outcome} · <Link to="/jobs">Open job {e.job_id}</Link></p>)}</div>)}</details>}</div>}
    {unavailableConnections.length > 0 && <section className="ssd-unavailable-section" aria-label="Unavailable granted connections"><div className="ssd-unavailable-heading"><span><TriangleAlert size={17}/></span><div><h2>Some granted connections need attention</h2><p>Ask your workspace Admin to review connection settings or access grants. For entity-lock key configuration, contact a Test Data Engineer.</p></div></div>{unavailableConnections.map(c => <article className="ssd-unavailable-card" key={c.id}><div><strong>{c.name}</strong><small>Granted operations: {c.granted_operations.join(', ')}</small></div><ul>{c.unavailable_reasons.map(reason => <li key={reason}>{reason}</li>)}</ul></article>)}</section>}
    {loading ? <p role="status">Loading authorized connections…</p> : !availableConnections.length ? <>{error && <div role="alert" className="ssd-feedback is-error"><TriangleAlert size={15}/><span>{error}</span></div>}<section className="panel ssd-empty-state"><span><Database size={21}/></span><h2>No available connections yet</h2><p>Ask your workspace Admin to enable a supported database connection and grant you the operations you need.</p><span className="ssd-empty-help">SSD supports PostgreSQL, MySQL, Oracle and SQL Server.</span></section></> : <>
      <div className="ssd-editor-layout">
        <section className="panel ssd-workspace-panel ssd-query-panel"><header className="ssd-panel-heading"><span className="ssd-panel-icon"><Code2 size={17}/></span><div><h2>SQL query</h2><p>{selected ? `${selected.name} · ${selected.environment} · ${database || selected.databases[0] || 'Database unavailable'}` : 'Select a connection above to begin.'} · Cross-schema queries are supported within the selected database.</p></div><button type="button" className="button button-confirm ssd-editor-save-button" disabled={busy || running || !selected || !sql.trim()} onClick={() => void action(saveDataset)}><Save size={13}/>{busy ? 'Saving?' : 'Save draft'}</button></header>
          {selected?.operation_scope === 'read_only' && <p className="ssd-source-note is-warning"><TriangleAlert size={15}/> This connection is read-only. Ask your Admin to set Operation scope to Read and write in Connections, then test and enable it. The database login must also allow writes.</p>}
          <label className="form-field source-query-label">SQL editor<div className="source-query-editor-shell"><pre ref={highlight} className="source-query-highlight" aria-hidden="true" dangerouslySetInnerHTML={{__html: highlightSql(sql)}}/><textarea className="source-query-editor" aria-label="SSD SQL query" spellCheck={false} wrap="off" value={sql} disabled={running} onChange={e => {setSql(e.target.value); edited()}} onScroll={e => {if (highlight.current) {highlight.current.scrollTop = e.currentTarget.scrollTop; highlight.current.scrollLeft = e.currentTarget.scrollLeft}}}/></div></label>
          <label className="form-field ssd-parameters-field">Named parameters (JSON)<textarea aria-label="SQL parameters" value={parameters} disabled={running} onChange={e => {setParameters(e.target.value); edited()}}/></label>
          {mutation && !dirty && selected?.entity_locking_enabled && <p className="ssd-source-note is-warning"><ShieldCheck size={15}/> Entity locks apply to this write. TIGR pauses it if another user holds a conflicting source-entity lock.</p>}
          {mutation && !dirty && <label className="checkbox-field ssd-mutation-confirm"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)}/>I intend to change records in {selected?.name} ({selected?.environment}). This executes once. Publish afterward to save this as a reusable dataset.</label>}
          {mutation && history.some(h => h.executions.some(e => e.outcome === 'outcome_unknown')) && <div className="ssd-unknown-commit"><p role="alert">An earlier source commit is uncertain. It will not be retried automatically.</p><label className="form-field">Source investigation reference<input value={reconciliationReference} maxLength={180} onChange={e => setReconciliationReference(e.target.value)} placeholder="source-transaction:reference"/></label><label className="checkbox-field"><input type="checkbox" checked={acknowledgeUnknown} onChange={e => setAcknowledgeUnknown(e.target.checked)}/>I investigated the earlier uncertain commit and intend this new mutation.</label></div>}
        </section>
        <aside className="ssd-action-panel">
          <section className="panel ssd-workspace-panel ssd-action-card"><header className="ssd-panel-heading"><span className="ssd-panel-icon"><Play size={16}/></span><div><h2>Query actions</h2><p>Validate before execution. SELECT results generate a protected five-row sample.</p></div></header>
            <div className="ssd-side-actions"><button type="button" className="button button-secondary" disabled={busy || running || !selected} onClick={() => void action(validateQuery)}>Validate</button><button type="button" className="button button-confirm" disabled={busy || running || !draft || dirty || !draft.validation.operation} onClick={() => {if (mutation && !confirmed) setExecuteConfirmOpen(true); else void action(() => submit('execute'))}}>Execute</button><button type="button" className="button ssd-publish-button ssd-publish-inline" disabled={!draft || dirty || draft.state === 'published' || busy || running || !(draft.state === 'ready_to_publish' && draft.dataset_id || (mutation ? execution?.outcome === 'committed' : execution?.outcome === 'read_complete' && !!sample && sample.revision === draft.revision))} onClick={() => void action(publishExecutedDataset)}>Publish data set</button></div>
            {draft?.dataset_id && <p className="ssd-dataset-id">Data set ID <strong>{draft.dataset_id}</strong></p>}
            {draft?.dataset_id && draft.state === 'published' && <DatasetRefreshButton datasetId={draft.dataset_id} version={draft.dataset_version_number!} role={role} operation={draft.validation.operation} onRefreshed={() => void load(draft.id)}/>}{draft?.state === 'published' && <p className="ssd-published-state"><Check size={14}/>Published and available in View Data <Link to="/view-data">Open in View Data</Link></p>}
            {error && <div role="alert" className="ssd-feedback is-error"><TriangleAlert size={15}/><span>{error}</span></div>}
            {notice && <div role="status" className="ssd-feedback is-success"><Check size={15}/><span>{notice}</span></div>}
            {draft?.validation.operation && <div className="ssd-query-facts"><div><span>Statement</span><strong>{draft.validation.operation.toUpperCase()}</strong></div><div><span>Required access</span><strong>{draft.validation.operations?.join(', ') ?? '—'}</strong></div><div><span>Objects</span><strong>{draft.validation.objects?.map(o => o.join('.')).join(', ') ?? '—'}</strong></div></div>}
            {execution && <p className={`ssd-execution-summary ${execution.outcome === 'committed' || execution.outcome === 'read_complete' ? 'is-success' : 'is-warning'}`}><span className="ssd-execution-mark">{execution.outcome === 'committed' || execution.outcome === 'read_complete' ? <Check size={15}/> : <TriangleAlert size={15}/>}</span><span>Execution: {execution.outcome} · Affected: {execution.evidence.affected_rows ?? '—'} · Returned: {execution.evidence.returned_rows ?? '—'}<br/>{execution.evidence.started_at} – {execution.evidence.ended_at}</span></p>}
            {jobId && <div className="ssd-job-status" aria-live="polite"><div><span className="ssd-job-indicator"/><strong>{job?.status ?? 'Queued'} · {job?.progress ?? 0}%</strong><Link to="/jobs">Open Jobs</Link></div><p>{job?.safe_summary ?? 'Your request is queued for processing.'}</p>{running && <button type="button" className="button button-secondary" onClick={() => void action(async () => {await apiRequest(`/ssd/jobs/${jobId}/cancel`, role, {method: 'POST'}); setNotice('Cancellation requested; committed source changes cannot be undone')})}>Cancel job</button>}</div>}
          </section>
        </aside>
      </div>
      <section className="panel ssd-sample-panel"><div className="ssd-sample-heading"><span className="ssd-panel-icon"><Rows3 size={17}/></span><div><h2>Sample records</h2><p>{sample ? `Showing ${sample.rows.length} of up to 5 records · Query revision ${sample.revision} · ${new Date(sample.generated_at).toLocaleString()}` : 'A sample of up to five rows from the pre-masked source appears here after a successful SELECT.'}</p></div></div>
        {sample ? <><p className="ssd-sample-privacy">{sample.privacy}</p>{sample.rows.length ? <div className="table-shell view-data-table-shell ssd-sample-table-shell"><table className="view-data-table"><thead><tr>{sample.fields.map(f => <th key={f.field}><span>{f.field}</span><small> {f.data_type}</small></th>)}</tr></thead><tbody>{sample.rows.slice(0, 5).map((row, i) => <tr key={i}>{sample.fields.map(f => {const value = row[f.field]; const text = value == null ? 'NULL' : String(value); return <td key={f.field} title={text}>{value == null ? <em>NULL</em> : text.length > 120 ? <details><summary>{text.slice(0, 100)}…</summary>{text}</details> : <span>{text}</span>}</td>})}</tr>)}</tbody></table></div> : <div className="view-data-empty"><Rows3 size={20}/><strong>No records matched</strong><p>Change the SELECT query and execute it again before saving a dataset.</p></div>}</> : <div className="view-data-empty"><Rows3 size={20}/><strong>No sample yet</strong><p>Validate and execute a SELECT query to preview up to five records from the pre-masked source.</p></div>}
      </section>
    </>}
  </div>
}
