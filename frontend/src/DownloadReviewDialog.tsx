import { Check, Download, LockKeyhole, X } from 'lucide-react'
import type { HumanRole } from './api'

export type DownloadAccess = {
  can_download: boolean
  record_count: number
  available_record_count: number
  blocked_record_count: number
  reservation_active: boolean
  lock_active: boolean
}

export type DownloadReview = {
  datasetId: string
  datasetName: string
  referenceCode: string
  versionNumber: number
  format: 'csv' | 'json'
  access: DownloadAccess
}

export default function DownloadReviewDialog({review,role='QA Engineer',busy,onCancel,onDownload}:{review:DownloadReview;role?:HumanRole;busy:boolean;onCancel:()=>void;onDownload:()=>void}) {
  const {access}=review
  const tde = role === 'Test Data Engineer'
  const message=access.record_count===0
    ?'This data set contains no records available for download.'
    :access.available_record_count===0
    ?tde ? 'All records are protected by active locks. They remain included in this TDE download.' : 'No records are currently available to download. Records locked by other user(s) are not included in this download.'
    :access.blocked_record_count>0
      ?tde ? `${access.blocked_record_count.toLocaleString()} records are protected by active locks and remain included in this TDE download.` : 'Records locked by other user(s) are not included in this download.'
      :`All ${access.available_record_count.toLocaleString()} records are available to use and download.`
  return <div className="dataset-overlay download-review-overlay" role="presentation"><section className="dataset-modal download-review-modal" role="dialog" aria-modal="true" aria-labelledby="download-review-title"><header className="dataset-modal-header"><div><div className="eyebrow">DOWNLOAD REVIEW · VERSION {review.versionNumber}</div><h2 id="download-review-title">{review.datasetName}</h2><p>{review.referenceCode}</p></div><button type="button" className="icon-button" aria-label="Close download review" onClick={onCancel}><X size={18}/></button></header><div className="dataset-detail-content"><div className={`alert ${access.blocked_record_count>0?'alert-warning':'alert-success'}`} role="status">{message}</div><div className="dataset-summary-row download-review-counts"><div className="dataset-summary-card"><span><Check size={17}/></span><div><strong>{access.available_record_count.toLocaleString()}</strong><small>{tde ? 'Available to QA' : 'Available to download'}</small></div></div><div className="dataset-summary-card"><span><LockKeyhole size={17}/></span><div><strong>{access.blocked_record_count.toLocaleString()}</strong><small>{tde ? 'Protected for QA · included for TDE' : 'Unavailable to download'}</small></div></div></div></div><footer className="dataset-modal-footer"><span>File format: {review.format.toUpperCase()}</span><div className="inline-actions"><button type="button" className="button button-secondary" onClick={onCancel}>Cancel</button><button type="button" className="button button-confirm" disabled={access.record_count===0||!access.can_download||busy} onClick={onDownload}><Download size={15}/>{busy?'Preparing…':`Download ${review.format.toUpperCase()}`}</button></div></footer></section></div>
}
