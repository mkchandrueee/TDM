import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { RefreshCw } from 'lucide-react'
import { apiRequest, type HumanRole } from './api'
import './dataset-refresh.css'

export default function DatasetRefreshButton({datasetId, version, role, operation, onRefreshed, iconOnly = false, requiresReview = false}: {
  datasetId: string; version: number; role: HumanRole; operation?: string; onRefreshed?: () => void; iconOnly?: boolean; requiresReview?: boolean
}) {
  const [jobId, setJobId] = useState('')
  const [confirmOpen, setConfirmOpen] = useState(false)
  const confirmDialog = useRef<HTMLDialogElement>(null)
  const mutation = ['insert', 'update', 'delete'].includes(operation ?? '')
  useEffect(() => {
    const dialog = confirmDialog.current
    if (confirmOpen && dialog && !dialog.open) dialog.showModal()
    if (!confirmOpen && dialog?.open) dialog.close()
  }, [confirmOpen])
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  useEffect(() => {
    if (!jobId) return
    let active = true
    const timer = window.setInterval(() => {
      void apiRequest<{status: string; safe_summary: string}>(`/jobs/${jobId}`, role).then(job => {
        if (!active || !['succeeded', 'failed', 'cancelled'].includes(job.status)) return
        window.clearInterval(timer); setJobId(''); setBusy(false)
        setMessage(job.status === 'succeeded'
          ? requiresReview ? 'Refresh generated a new version. Review it in Data Sets.' : 'Dataset refreshed.'
          : job.safe_summary || 'Refresh failed.')
        if (job.status === 'succeeded') onRefreshed?.()
      }).catch(error => {
        if (active) setMessage(`Unable to check refresh status: ${(error as Error).message}. Check Jobs before retrying.`)
      })
    }, 1500)
    return () => { active = false; window.clearInterval(timer) }
  }, [jobId, role, onRefreshed])

  async function refresh() {
    setConfirmOpen(false)
    setBusy(true); setMessage('')
    try {
      const result = await apiRequest<{job_id: string; requires_review?: boolean}>(`/datasets/${datasetId}/refresh`, role, {
        method: 'POST', body: JSON.stringify({expected_version: version, idempotency_key: crypto.randomUUID(), confirm_mutation: mutation}),
      })
      setJobId(result.job_id)
    } catch (error) { setMessage((error as Error).message); setBusy(false) }
  }
  return <span className="dataset-refresh-action"><button type="button" className={iconOnly ? "icon-button dataset-row-icon-button dataset-refresh-icon-button" : "button button-secondary compact"} aria-label={busy ? "Refreshing dataset" : "Refresh dataset"} title={busy ? "Refreshing dataset" : "Refresh dataset"} disabled={busy} onClick={() => setConfirmOpen(true)}><RefreshCw size={14}/>{!iconOnly && (busy ? 'Refreshing…' : 'Refresh dataset')}</button>{message && <small role="status">{message}</small>}{createPortal(<dialog ref={confirmDialog} className="dataset-modal dataset-refresh-dialog" aria-label="Refresh dataset" onCancel={() => setConfirmOpen(false)} onClose={() => setConfirmOpen(false)}><header className="dataset-modal-header"><h2>Refresh dataset</h2></header><div className="dataset-refresh-dialog-body"><p>{requiresReview ? 'The saved source definition will run again and generate a new version for review.' : mutation ? 'The saved query will run again and may insert, update or delete source records.' : 'The saved SELECT will run again and publish a new version with refreshed records.'}</p><p>The query and dataset fields will stay unchanged.</p></div><footer className="dataset-modal-actions"><button type="button" className="button button-secondary" autoFocus onClick={() => setConfirmOpen(false)}>Cancel</button><button type="button" className="button button-confirm" disabled={busy} onClick={() => void refresh()}>Confirm refresh</button></footer></dialog>, document.body)}</span>
}
