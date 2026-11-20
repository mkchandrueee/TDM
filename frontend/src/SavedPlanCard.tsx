import type { ReactNode } from 'react'
import { ArrowRight, Trash2 } from 'lucide-react'
import './savedPlanCards.css'

export type SavedPlanCardProps = {
  icon: ReactNode
  title: string
  titleAdornment?: ReactNode
  pathway: string
  step: string
  referenceCode: string
  status: string
  statusTone?: 'draft' | 'saved' | 'failed'
  updatedAt: string
  onResume: () => void
  onQuarantine?: () => void
  quarantineDisabled?: boolean
}

export default function SavedPlanCard({
  icon,
  title,
  titleAdornment,
  pathway,
  step,
  referenceCode,
  status,
  statusTone = 'draft',
  updatedAt,
  onResume,
  onQuarantine,
  quarantineDisabled = false,
}: SavedPlanCardProps) {
  return (
    <article className="saved-plan-card">
      <div className="saved-plan-card__header">
        <div className="saved-plan-card__identity">
          <span className="saved-plan-card__icon" aria-hidden="true">{icon}</span>
          <button type="button" className="saved-plan-card__title" title={title} aria-label={`Resume ${title}`} onClick={onResume}>
            <span>{title}</span>{titleAdornment}
          </button>
        </div>
        <span className={`saved-plan-card__status saved-plan-card__status--${statusTone}`}>{status}</span>
      </div>

      <button type="button" className="saved-plan-card__details" onClick={onResume}>
        <span className="saved-plan-card__pathway">{pathway}</span>
        <span className="saved-plan-card__step">{step}</span>
        <span className="saved-plan-card__reference">{referenceCode}</span>
      </button>

      <footer className="saved-plan-card__footer">
        <span className="saved-plan-card__updated">Last updated · {updatedAt}</span>
        <div className="saved-plan-card__actions">
          {onQuarantine && (
            <button
              type="button"
              className="saved-plan-card__quarantine"
              aria-label="Quarantine draft version"
              title="Quarantine draft version"
              disabled={quarantineDisabled}
              onClick={onQuarantine}
            >
              <Trash2 size={16} />
            </button>
          )}
          <button type="button" className="saved-plan-card__resume" onClick={onResume}>
            Resume <ArrowRight size={15} aria-hidden="true" />
          </button>
        </div>
      </footer>
    </article>
  )
}
