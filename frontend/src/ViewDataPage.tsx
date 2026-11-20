import DatasetRefreshButton from './DatasetRefreshButton'
import { createPortal } from 'react-dom'
import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { Check, ChevronLeft, ChevronRight, Copy, Download, FileCode2, Grid2X2, KeyRound, LockKeyhole, RefreshCw, Rows3, ShieldCheck, X } from 'lucide-react'
import { apiDownload, apiRequest, type HumanRole } from './api'
import { generationModeClass, generationModeLabel } from './datasetLabels'
import DownloadReviewDialog, { type DownloadAccess, type DownloadReview } from './DownloadReviewDialog'
import './view-data-query.css'

type DataSet = {
  id: string
  reference_code: string
  name: string
  creation_pathway: 'scenario_to_data' | 'source_to_data' | 'self_servicing_data'
  latest_version: { version_number: number; lifecycle_state: string; provenance: Record<string, string | number | boolean | null>; record_count: number | null; schema_scope: Field[]; expiry_at: string | null }
}
type Field = { field: string; data_type: string; entity?: string | null; source_field?: string | null; json_path?: string | null; is_entity_key?: boolean; privacy_classification?: 'sensitive' | 'non_sensitive' | 'not_assessed'; is_masked?: boolean }
type ViewResult = {
  dataset_id: string
  reference_code: string
  dataset_name: string
  version_number: number
  fields: Field[]
  rows: Array<Record<string, unknown>>
  record_count: number
  available_record_count: number
  blocked_record_count: number
  protected_row_indices: number[]
  page: number
  page_size: number
  has_more: boolean
}

const PAGE_SIZE = 50

function cellValue(row: Record<string, unknown>, field: Field): unknown {
  const path = field.json_path || (field.entity ? `${field.entity}.${field.field}` : field.field)
  const parts = path.split('.').filter(Boolean)
  let value: unknown = row
  for (const part of parts) {
    if (!value || typeof value !== 'object' || !(part in value)) return row[field.field]
    value = (value as Record<string, unknown>)[part]
  }
  return value
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

export default function ViewDataPage({ role, workspaceId }: { role: HumanRole; workspaceId: string }) {
  const [datasets, setDatasets] = useState<DataSet[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [selectedRows, setSelectedRows] = useState<number[]>([])
  const [page, setPage] = useState(1)
  const [refreshKey, setRefreshKey] = useState(0)
  const [data, setData] = useState<ViewResult | null>(null)
  const [loading, setLoading] = useState(false)
  const loadedView = useRef<{key: string; result: ViewResult} | null>(null)
  const [downloading, setDownloading] = useState(false)
  const [downloadReview, setDownloadReview] = useState<DownloadReview | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [queryOpen, setQueryOpen] = useState(false)
  const [savedQuery, setSavedQuery] = useState<{query: string | null; parameters: string[]; version_number: number; kind: 'sql' | 'source_mapping'} | null>(null)
  const [queryLoading, setQueryLoading] = useState(false)
  const [queryError, setQueryError] = useState('')
  const isTde = role === 'Test Data Engineer'

  const loadCatalog = useCallback(async () => {
    if (!workspaceId) { setDatasets([]); setSelectedId(''); return }
    setError('')
    try {
      const availableDatasets = await apiRequest<DataSet[]>(`/workspaces/${workspaceId}/viewable-datasets`, role)
      setDatasets(current => JSON.stringify(current) === JSON.stringify(availableDatasets) ? current : availableDatasets)
      setSelectedId(current => availableDatasets.some(item => item.id === current) ? current : '')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to load data sets available to you')
    }
  }, [workspaceId, role])

  useEffect(() => { void loadCatalog() }, [loadCatalog])
  useEffect(() => { setSelectedRows([]) }, [selectedId, page])
  useEffect(() => {
    if (!workspaceId) return
    const timer = window.setInterval(() => void loadCatalog(), 15000)
    return () => window.clearInterval(timer)
  }, [workspaceId, loadCatalog])

  useEffect(() => {
    const selected = datasets.find(item => item.id === selectedId)
    const expiryAt = selected?.latest_version.expiry_at
    if (!expiryAt) return
    const delay = new Date(expiryAt).getTime() - Date.now()
    if (delay <= 0) {
      loadedView.current = null
      setData(null)
      setSelectedRows([])
      setSelectedId('')
      setError('This data set has expired and is no longer available to view.')
      void loadCatalog()
      return
    }
    const timer = window.setTimeout(() => {
      loadedView.current = null
      setData(null)
      setSelectedRows([])
      setSelectedId('')
      setError('This data set has expired and is no longer available to view.')
      void loadCatalog()
    }, Math.min(delay + 25, 2_147_483_647))
    return () => window.clearTimeout(timer)
  }, [datasets, selectedId, loadCatalog])

  useEffect(() => {
    const selected = datasets.find(item => item.id === selectedId)
    if (!selected) { loadedView.current = null; setData(null); setLoading(false); return }
    let active = true
    const key = `${workspaceId}:${role}:${selected.id}:${selected.latest_version.version_number}:${page}`
    // Keep access/lock checks silent while the same page is already displayed.
    if (loadedView.current?.key !== key) { loadedView.current = null; setLoading(true); setData(null) }
    apiRequest<ViewResult>(`/datasets/${selected.id}/versions/${selected.latest_version.version_number}/view-data?page=${page}&page_size=${PAGE_SIZE}`, role)
      .then(result => {
        if (!active) return
        setError('')
        if (loadedView.current?.key === key && JSON.stringify(loadedView.current.result) === JSON.stringify(result)) return
        loadedView.current = {key, result}
        setData(result); setSelectedRows([])
      })
      .catch((cause: unknown) => {
        if (!active) return
        // Never retain records after an access check fails.
        loadedView.current = null; setData(null); setSelectedRows([])
        setError(cause instanceof Error ? cause.message : 'Unable to load available records')
      })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [datasets, selectedId, page, role, workspaceId, refreshKey])

  useEffect(() => {
    if (!selectedId) return
    const recheck = () => setRefreshKey(current => current + 1)
    const timer = window.setInterval(recheck, 5000)
    window.addEventListener('focus', recheck)
    return () => { window.clearInterval(timer); window.removeEventListener('focus', recheck) }
  }, [selectedId])

  async function copyText(value: string, label: string) {
    try {
      await navigator.clipboard.writeText(value)
      setNotice(`${label} copied`)
      window.setTimeout(() => setNotice(''), 2500)
    } catch {
      setNotice('Clipboard access is unavailable in this browser')
      window.setTimeout(() => setNotice(''), 3500)
    }
  }

  async function copySelectedRows() {
    if (!data || !selectedRows.length) return
    const selected = new Set(selectedRows)
    const rowsText = data.rows
      .filter((_, rowIndex) => selected.has((page - 1) * PAGE_SIZE + rowIndex + 1))
      .map(row => data.fields.map(field => displayValue(cellValue(row, field))).join('\t'))
      .join('\n')
    if (rowsText) await copyText(rowsText, `${selected.size} selected rows`)
  }

  function copyRow(row: Record<string, unknown>) {
    void copyText(data?.fields.map(field => displayValue(cellValue(row, field))).join('\t') ?? '', 'Row')
  }

  function handleCopyShortcut(event: KeyboardEvent<HTMLButtonElement>, copy: () => void) {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'c') {
      event.preventDefault()
      copy()
    }
  }

  function selectDataset(id: string) { setSelectedId(id); setPage(1); setSelectedRows([]); setNotice('') }

  function refreshView() {
    setSelectedRows([])
    setRefreshKey(current => current + 1)
    void loadCatalog()
  }

  async function showSavedQuery() {
    if (!data) return
    setQueryOpen(true); setQueryLoading(true); setQueryError(''); setSavedQuery(null)
    try {
      setSavedQuery(await apiRequest<{query: string | null; parameters: string[]; version_number: number; kind: 'sql' | 'source_mapping'}>(
        `/datasets/${data.dataset_id}/versions/${data.version_number}/query`, role,
      ))
    } catch (cause) { setQueryError(cause instanceof Error ? cause.message : 'Unable to load the saved query') }
    finally { setQueryLoading(false) }
  }

  async function saveCsv(review: DownloadReview) {
    setDownloading(true)
    setError('')
    try {
      const result = await apiDownload(`/datasets/${review.datasetId}/versions/${review.versionNumber}/artifact?format=csv`, role)
      const url = URL.createObjectURL(result.blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = result.filename ?? `${review.referenceCode}.csv`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
      setNotice('CSV download started')
      window.setTimeout(() => setNotice(''), 2500)
      setDownloadReview(null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to download this data set')
    } finally {
      setDownloading(false)
    }
  }

  async function reviewCsvDownload() {
    const selected = datasets.find(item => item.id === selectedId)
    if (!selected || !data || (isTde ? data.record_count === 0 : data.available_record_count === 0)) return
    setError('')
    try {
      const access = await apiRequest<DownloadAccess>(`/datasets/${selected.id}/versions/${selected.latest_version.version_number}/download-access`, role)
      const review: DownloadReview = {datasetId:selected.id,datasetName:data.dataset_name,referenceCode:data.reference_code,versionNumber:data.version_number,format:'csv',access}
      if (access.blocked_record_count > 0 || !access.can_download) setDownloadReview(review)
      else await saveCsv(review)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to check download access')
    }
  }

  function toggleRowSelection(rowNumber: number) {
    setSelectedRows(current => current.includes(rowNumber) ? current.filter(item => item !== rowNumber) : [...current, rowNumber].sort((a, b) => a - b))
  }

  function moveRowSelection(event: KeyboardEvent<HTMLButtonElement>, rowNumber: number) {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'c') {
      event.preventDefault()
      if (selectedRows.length) void copySelectedRows()
      else {
        const rowIndex = rowNumber - (page - 1) * PAGE_SIZE - 1
        const row = data?.rows[rowIndex]
        if (row) copyRow(row)
      }
      return
    }
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return
    event.preventDefault()
    const nextRow = rowNumber + (event.key === 'ArrowDown' ? 1 : -1)
    const nextButton = event.currentTarget.closest('table')?.querySelector<HTMLButtonElement>(`[data-row-number="${nextRow}"]`)
    if (!nextButton) return
    setSelectedRows(current => [...new Set([...current, rowNumber, nextRow])].sort((a, b) => a - b))
    nextButton.focus()
  }

  return <div className="view-data-page">
    <section className="view-data-intro"><div className="view-data-intro-icon"><Grid2X2 size={20}/></div><div className="view-data-intro-copy"><h2>{isTde ? 'View workspace records' : 'View your data sets'}</h2><p>{isTde ? 'Yellow highlights mark entity keys held by any active QA lock in this workspace, including locks on other data sets. TDE can still inspect and download them.' : 'Private data sets you own are available anytime without a reservation. Other published data sets require an approved active reservation and lock. Records protected by another active user lock remain hidden.'}</p></div><label className="view-data-dataset-picker"><span>Data set name and ID</span><select value={selectedId} onChange={event => selectDataset(event.target.value)} disabled={!datasets.length} aria-label="Select a data set by name and ID">{datasets.length ? <><option value="">Select a data set</option>{datasets.map(dataset => <option key={dataset.id} value={dataset.id}>{dataset.name} · {dataset.reference_code}{dataset.creation_pathway === 'self_servicing_data' ? ' · Self-service Query' : ''}</option>)}</> : <option value="">No data sets available</option>}</select></label></section>
    {error && <div className="alert alert-warning" role="alert">{error}</div>}
    <div className="view-data-layout">
      <section className="panel view-data-grid-panel"><div className="view-data-grid-heading"><div className="view-data-grid-title"><h2>{data?.dataset_name ?? datasets.find(item => item.id === selectedId)?.name ?? 'Select a data set'}</h2><p>{data ? `Data set ID: ${data.reference_code}` : isTde ? 'Choose a published data set in this workspace.' : 'Choose a private data set you own, or a shared data set you reserved and locked.'}</p>{data && <div className="view-data-counts"><span className={`dataset-mode-value ${generationModeClass(datasets.find(item => item.id === selectedId)?.latest_version.provenance, datasets.find(item => item.id === selectedId)?.creation_pathway)}`}>{generationModeLabel(datasets.find(item => item.id === selectedId)?.latest_version.provenance, datasets.find(item => item.id === selectedId)?.creation_pathway)}</span><span className="status-badge dataset-version-status lifecycle-published"><span>v{data.version_number}</span><span className="dataset-version-separator" aria-hidden="true">·</span><span>Published</span></span><span className={data.available_record_count === 0 ? 'view-data-count-available-empty' : ''}><strong>{data.available_record_count.toLocaleString()}</strong> {isTde ? 'available to QA' : 'available'}</span><span className={data.blocked_record_count ? 'blocked view-data-count-protected' : 'view-data-count-protected-empty'}><strong>{data.blocked_record_count.toLocaleString()}</strong> {isTde ? 'protected for QA' : 'locked'}</span>{notice && <span className="view-data-notice" role="status"><Check size={14}/>{notice}</span>}</div>}</div><div className="view-data-grid-actions">{data&&(()=>{const selected=datasets.find(item=>item.id===selectedId);const sourceBacked=selected?.creation_pathway==='source_to_data'||selected?.latest_version.provenance.origin==='source_backed';const canRefreshSource=(sourceBacked||Boolean(selected?.latest_version.provenance.scenario_id))&&isTde;return (selected?.creation_pathway==='self_servicing_data'||sourceBacked)&&<><button type="button" className="icon-button" aria-label={sourceBacked?'View saved source SQL or mapping':'View saved SQL query'} title={sourceBacked?'View source SQL or mapping':'View SQL query'} onClick={() => void showSavedQuery()}><FileCode2 size={16}/></button>{(selected?.creation_pathway==='self_servicing_data'||canRefreshSource)&&<DatasetRefreshButton datasetId={data.dataset_id} version={data.version_number} role={role} operation={String(selected?.latest_version.provenance.ssd_operation??'')} onRefreshed={refreshView} iconOnly requiresReview={canRefreshSource}/>}</>})()}{data && selectedRows.length > 0 && <button type="button" className="button button-secondary view-data-copy-selected" onClick={() => void copySelectedRows()}><Copy size={14}/>Copy selected ({selectedRows.length})</button>}<button type="button" className="icon-button view-data-download-csv" aria-label={downloading ? 'Downloading CSV' : 'Download CSV'} title={downloading ? 'Downloading CSV' : 'Download CSV'} disabled={!data || (isTde ? data.record_count === 0 : data.available_record_count === 0) || loading || downloading} onClick={() => void reviewCsvDownload()}><Download size={16}/></button></div></div>
        {data && <div className="view-data-field-legend"><span className="view-data-record-count"><Rows3 size={14}/>Record Count: {data.record_count.toLocaleString()}</span><span><ShieldCheck size={14}/>Privacy masking applied</span><span><KeyRound size={14}/>Entity key used for record locking</span></div>}
        {data && data.blocked_record_count > 0 && <div className="view-data-lock-note"><LockKeyhole size={15}/><span>{isTde ? 'Yellow rows match entity keys held by active QA locks, including locks acquired through other data sets. They remain visible and downloadable to TDE.' : 'Records locked by other user(s) are not included in this view.'}</span></div>}
        {loading ? <div className="view-data-empty">Loading available records…</div> : data && (isTde ? data.record_count === 0 : data.available_record_count === 0) ? <div className="view-data-empty"><LockKeyhole size={22}/><strong>{!isTde && data.blocked_record_count ? 'All records are currently in use' : 'No records available'}</strong><p>{!isTde && data.blocked_record_count ? 'Try again after the active lock is released or expires.' : 'This data set does not contain any records.'}</p></div> : data ? <>
          <div className="table-shell view-data-table-shell"><table className="view-data-table"><thead><tr><th className="view-data-row-header">Row</th>{data.fields.map((field, index) => <th key={`${field.json_path ?? field.field}-${index}`}><div className="view-data-field-heading"><span>{field.field}</span><span className="view-data-field-icons">{field.is_masked&&<ShieldCheck size={14} className="view-data-privacy-icon is-masked" aria-label="Privacy masking applied" role="img"/>}{field.is_entity_key&&<KeyRound size={14} className="view-data-entity-key-icon" aria-label="Entity key used for record locking" role="img"/>}</span></div></th>)}</tr></thead><tbody>{data.rows.map((row, rowIndex) => { const rowNumber = (page - 1) * PAGE_SIZE + rowIndex + 1; const isSelected = selectedRows.includes(rowNumber); const isProtected = data.protected_row_indices.includes(rowIndex); return <tr key={`${page}-${rowIndex}`} className={`${isSelected ? 'view-data-row-selected ' : ''}${isProtected ? 'view-data-row-protected' : ''}`.trim()}><th className="view-data-row-copy"><button type="button" className="view-data-select-row" data-row-number={rowNumber} aria-pressed={isSelected} aria-label={`Select row ${rowNumber}${isProtected ? ', protected for other QA users' : ''}`} title={isProtected ? 'Protected for other QA users by an active lock' : 'Click to toggle selection; use Up and Down arrows to select adjacent rows'} onClick={() => toggleRowSelection(rowNumber)} onKeyDown={event => moveRowSelection(event, rowNumber)}>{rowNumber}</button><button type="button" className="view-data-copy-row-number" aria-label={`Copy row ${rowNumber}`} title="Copy row" onClick={() => copyRow(row)} onKeyDown={event => handleCopyShortcut(event, () => copyRow(row))}><Copy size={12}/></button></th>{data.fields.map((field, fieldIndex) => { const value = displayValue(cellValue(row, field)); return <td key={`${field.field}-${fieldIndex}`} title={value}><span>{value || '—'}</span><button type="button" className="view-data-copy-cell" aria-label={`Copy ${field.field} value`} title="Copy cell" onClick={() => void copyText(value, 'Cell value')} onKeyDown={event => handleCopyShortcut(event, () => void copyText(value, 'Cell value'))}><Copy size={12}/></button></td> })}</tr> })}</tbody></table></div>
          <div className="view-data-pagination"><span>Showing {(page - 1) * PAGE_SIZE + 1}–{(page - 1) * PAGE_SIZE + data.rows.length} of {(isTde ? data.record_count : data.available_record_count).toLocaleString()} records</span><div><button type="button" className="button button-secondary" disabled={page <= 1 || loading} onClick={() => setPage(value => Math.max(1, value - 1))}><ChevronLeft size={15}/>Previous</button><span>Page {page}</span><button type="button" className="button button-secondary" disabled={!data.has_more || loading} onClick={() => setPage(value => value + 1)}>Next<ChevronRight size={15}/></button></div></div>
        </> : <div className="view-data-empty"><Grid2X2 size={22}/><strong>{datasets.length ? 'Choose a data set' : 'No data sets available'}</strong><p>{datasets.length ? (isTde ? 'Choose a published data set in this workspace to view its records.' : 'Choose a private data set you own, or a shared data set with a current approved reservation and active lock.') : (isTde ? 'No published, privacy-compliant data sets are currently available in this workspace.' : 'You have no private owned data sets or shared data sets with a current approved reservation and active lock.')}</p></div>}
      </section>
    </div>
    {queryOpen && createPortal(<div className="dataset-overlay" role="presentation" onMouseDown={event => {if(event.target===event.currentTarget)setQueryOpen(false)}}><section className="dataset-modal view-data-sql-modal" role="dialog" aria-modal="true" aria-label="Saved SQL query"><header className="dataset-modal-header"><div><h2>{savedQuery?.kind==='source_mapping' ? 'Saved source mapping' : 'Saved SQL query'}</h2><p>{savedQuery ? `Dataset version ${savedQuery.version_number} - Query and fields used to generate these records` : 'Loading the saved query...'}</p></div><button type="button" className="icon-button" aria-label="Close SQL query" onClick={() => setQueryOpen(false)}><X size={18}/></button></header><div className="dataset-refresh-dialog-body">{queryLoading ? <p role="status">Loading query...</p> : queryError ? <p role="alert">{queryError}</p> : savedQuery?.query ? <><pre className="view-data-saved-query">{savedQuery.query}</pre>{savedQuery.parameters.length > 0 && <p>Named parameters: {savedQuery.parameters.map(name => `:${name}`).join(', ')}</p>}</> : savedQuery?.kind==='source_mapping' ? <p>This source-backed data set was generated from a saved field mapping. It has no standalone SQL query.</p> : null}</div><footer className="dataset-modal-actions"><button type="button" className="button button-secondary" onClick={() => setQueryOpen(false)}>Close</button></footer></section></div>, document.body)}
    {downloadReview && <DownloadReviewDialog review={downloadReview} role={role} busy={downloading} onCancel={() => setDownloadReview(null)} onDownload={() => void saveCsv(downloadReview)}/>}
  </div>
}
