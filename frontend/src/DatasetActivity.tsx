import { createContext, useContext } from 'react'
import { CalendarCheck2, LockKeyhole } from 'lucide-react'

export type DatasetUsageStatus = {
  dataset_id: string
  reservation_active: boolean
  lock_active: boolean
}

export const DatasetUsageContext = createContext<Record<string, DatasetUsageStatus>>({})

export function DatasetActivityIcons({ datasetId, status: statusOverride }: { datasetId?: string | null; status?: DatasetUsageStatus | null }) {
  const usage = useContext(DatasetUsageContext)
  const status = statusOverride !== undefined ? statusOverride : (datasetId ? usage[datasetId] : undefined)
  if (!status || (!status.reservation_active && !status.lock_active)) return null

  return <span className="dataset-activity-icons" aria-label="Active data set access">
    {status.reservation_active && <span className="dataset-activity-icon-wrap reservation" title="Reserved by a workspace user"><CalendarCheck2 className="dataset-reservation-icon" size={15} strokeWidth={3} aria-label="Active reservation" role="img"/></span>}
    {status.lock_active && <span className="dataset-activity-icon-wrap lock" title="Locked by a workspace user"><LockKeyhole className="dataset-lock-icon" size={15} strokeWidth={3} aria-label="Active lock" role="img"/></span>}
  </span>
}
