import { useEffect, useMemo, useState } from 'react'
import { apiRequest, type HumanRole } from './api'

export type TestDataRequest = {
  id: string
  display_id: string
  title: string | null
  description: string | null
  scenarios?: string[]
  record_count?: number | null
  required_until?: string | null
  acceptance_criteria?: string | null
  status: string
}

export function testDataRequestRequirements(ticket: TestDataRequest | null) {
  if (!ticket) return ''
  const details = [
    ticket.description?.trim() ? `Objective: ${ticket.description.trim()}` : ticket.title ? `Objective: ${ticket.title}` : '',
    ticket.scenarios?.length ? `Scenario details:\n${ticket.scenarios.map(item => `• ${item}`).join('\n')}` : '',
  ].filter(Boolean)
  return details.join('\n\n').slice(0, 4000)
}

export function testDataRequestExpiry(ticket: TestDataRequest | null) {
  const date = ticket?.required_until?.slice(0, 10)
  return date && /^\d{4}-\d{2}-\d{2}$/.test(date) ? `${date}T23:59` : ''
}

export function testDataRequestObjective(ticket: TestDataRequest | null) {
  return (ticket?.description?.trim() || ticket?.title?.trim() || '').slice(0, 80)
}

export default function TestDataRequestPicker({
  role, workspaceId, value, onChange, disabled = false,
}: {
  role: HumanRole
  workspaceId: string
  value: string
  onChange: (ticket: TestDataRequest | null) => void
  disabled?: boolean
}) {
  const [tickets, setTickets] = useState<TestDataRequest[]>([])
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [open, setOpen] = useState(false)

  useEffect(() => {
    let active = true
    setLoading(true)
    setLoadError('')
    apiRequest<{ items: TestDataRequest[] }>(`/tickets?workspace_id=${encodeURIComponent(workspaceId)}&limit=500&offset=0`, role)
      .then(result => { if (active) setTickets(result.items) })
      .catch(() => { if (active) setLoadError('Test data requests could not be loaded.') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [role, workspaceId])

  const filtered = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase()
    return needle ? tickets.filter(ticket => `${ticket.display_id} ${ticket.title ?? ''}`.toLocaleLowerCase().includes(needle)) : tickets
  }, [search, tickets])
  const selectedTicket = tickets.find(ticket => ticket.id === value)

  return <div className="form-field test-data-request-picker">
    <label htmlFor="test-data-request-search">Ticket ID (optional)</label>
    <div className="test-data-request-combobox">
      <input id="test-data-request-search" type="text" autoComplete="off" role="combobox" aria-autocomplete="list"
        aria-expanded={open && filtered.length > 0} aria-controls="test-data-request-options"
        value={search || selectedTicket?.display_id || ''} disabled={disabled || loading}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onChange={event => { setSearch(event.target.value); onChange(null); setOpen(true) }}
        onKeyDown={event => { if (event.key === 'Escape') setOpen(false); if (event.key === 'Enter' && open && filtered[0]) { event.preventDefault(); onChange(filtered[0]); setSearch(filtered[0].display_id); setOpen(false) } }}
        placeholder="Search by ticket ID or request title"/>
      {open && !disabled && !loading && <div id="test-data-request-options" className="test-data-request-options" role="listbox">
        {filtered.length ? filtered.slice(0, 30).map(ticket => <button type="button" role="option" aria-selected={ticket.id === value} key={ticket.id}
          onMouseDown={event => event.preventDefault()}
          onClick={() => { onChange(ticket); setSearch(ticket.display_id); setOpen(false) }}>
          <strong>{ticket.display_id}</strong><span>· {ticket.title || 'Untitled request'}</span>
        </button>) : <div className="test-data-request-empty">{search ? 'No matching ticket IDs.' : 'No requests available.'}</div>}
      </div>}
    </div>
    {(loadError || loading) && <small>{loadError || 'Loading workspace requests…'}</small>}
  </div>
}
