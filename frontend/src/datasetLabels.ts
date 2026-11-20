export const SCENARIO_TO_DATA_LABEL = 'Scenario to Data'

export type DatasetProvenance = Record<string, string | number | boolean | null | undefined>

export function generationModeLabel(provenance?: DatasetProvenance | null, pathway?: string | null): string {
  if (pathway === 'self_servicing_data') return 'Self-service Query'
  const origin = String(provenance?.origin ?? '').trim().toLowerCase().replaceAll('-', '_')
  if (origin === 'synthetic') return 'Synthetic'
  if (origin === 'source_backed' || origin === 'source') return 'Source-backed'
  if ((origin === '' || origin === 'not_set') && pathway === 'source_to_data') return 'Source-backed'
  return origin === 'not_set' ? 'Not set' : 'Not specified'
}

export function generationModeClass(provenance?: DatasetProvenance | null, pathway?: string | null): string {
  const label = generationModeLabel(provenance, pathway)
  if (label === 'Synthetic') return 'is-synthetic'
  if (label === 'Source-backed' || label === 'Self-service Query') return 'is-source-backed'
  return 'is-mode-unset'
}

export function creationPathwayLabel(pathway?: string | null): string {
  if (pathway === 'self_servicing_data') return 'Self Servicing Data'
  if (pathway === 'source_to_data') return 'Source to Data'
  if (pathway === 'scenario_to_data') return SCENARIO_TO_DATA_LABEL
  return 'Not specified'
}
