import type { ReactNode } from 'react'
import { useEffect } from 'react'
import { X } from 'lucide-react'

export function StatusBadge({ status, children }: { status: 'neutral' | 'success' | 'warning' | 'danger' | 'info'; children: ReactNode }) {
  return <span className={`status-badge status-${status}`}><i />{children}</span>
}

export function Alert({ tone = 'info', title, children }: { tone?: 'info' | 'success' | 'warning' | 'danger'; title?: string; children: ReactNode }) {
  return <div className={`alert alert-${tone}`} role={tone === 'danger' ? 'alert' : 'status'}>{title && <strong>{title}</strong>}<div>{children}</div></div>
}

export function TableShell({ headers, children, emptyMessage }: { headers: string[]; children?: ReactNode; emptyMessage: string }) {
  return <div className="table-shell"><table><thead><tr>{headers.map((header) => <th key={header}>{header}</th>)}</tr></thead><tbody>{children || <tr><td colSpan={headers.length} className="table-empty">{emptyMessage}</td></tr>}</tbody></table></div>
}

export function StepNavigation({ steps, active }: { steps: string[]; active: number }) {
  return <ol className="step-navigation">{steps.map((step, index) => <li key={step} className={index === active ? 'step-active' : index < active ? 'step-complete' : ''}><span>{index < active ? '✓' : index + 1}</span>{step}</li>)}</ol>
}

export function FormField({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return <label className="form-field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>
}

export function Modal({ title, onClose, children, footer }: { title: string; onClose: () => void; children: ReactNode; footer?: ReactNode }) {
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => event.key === 'Escape' && onClose()
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [onClose])
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}><section className="modal-window" role="dialog" aria-modal="true" aria-labelledby="modal-title"><header><h2 id="modal-title">{title}</h2><button className="icon-button" onClick={onClose} aria-label="Close dialog"><X size={18}/></button></header><div className="modal-content">{children}</div>{footer && <footer>{footer}</footer>}</section></div>
}

export function ConfirmDialog({ title, children, confirmLabel = 'Confirm', destructive = false, onConfirm, onClose }: { title: string; children: ReactNode; confirmLabel?: string; destructive?: boolean; onConfirm: () => void; onClose: () => void }) {
  return <Modal title={title} onClose={onClose} footer={<><button className="button button-secondary" onClick={onClose}>Cancel</button><button className={`button ${destructive ? 'button-danger' : 'button-confirm'}`} onClick={onConfirm}>{confirmLabel}</button></>}>
    {children}
  </Modal>
}
