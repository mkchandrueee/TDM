export type HumanRole = 'Test Data Engineer' | 'QA Engineer' | 'Admin'
export type DevelopmentAccess = {
  subject: string
  display_name: string
  workspace_id: string
  workspace_name: string
  role: HumanRole
  last_seen_at?: string | null
}

let oidcAccessToken: string | null = null
let developmentSubject = 'local-developer'

export function setOidcAccessToken(token: string | null) {
  oidcAccessToken = token
}

export function setDevelopmentSubject(subject: string) {
  developmentSubject = subject
}

const apiBase = import.meta.env.VITE_TIGR_API_BASE_URL || '/api/v1'

export async function getDevelopmentLoginOptions(): Promise<DevelopmentAccess[]> {
  const response = await fetch(`${apiBase}/dev-auth/options`)
  const body = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(typeof body.message === 'string' ? body.message : `Unable to load development users (${response.status})`)
  return body as DevelopmentAccess[]
}

export async function loginDevelopmentUser(subject: string, workspace_id: string, password: string): Promise<DevelopmentAccess> {
  const response = await fetch(`${apiBase}/dev-auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ subject, workspace_id, password }),
  })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(typeof body.message === 'string' ? body.message : `Sign in failed (${response.status})`)
  return body as DevelopmentAccess
}

export async function apiRequest<T>(path: string, _role: HumanRole, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  if (oidcAccessToken) {
    headers.set('Authorization', `Bearer ${oidcAccessToken}`)
  } else if (import.meta.env.DEV) {
    headers.set('X-Dev-Subject', developmentSubject)
  }
  const response = await fetch(`${apiBase}${path}`, { ...init, headers })
  if (response.status === 204) return undefined as T
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    const message = typeof body.message === 'string' ? body.message : typeof body.detail === 'string' ? body.detail : `Request failed (${response.status})`
    throw new Error(message)
  }
  return body as T
}


export async function apiDownload(path: string, _role: HumanRole): Promise<Blob> {
  const headers = new Headers()
  if (oidcAccessToken) headers.set('Authorization', `Bearer ${oidcAccessToken}`)
  else if (import.meta.env.DEV) {
    headers.set('X-Dev-Subject', developmentSubject)
  }
  const response = await fetch(`${apiBase}${path}`, { headers })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    const message = typeof body.message === 'string'
      ? body.message
      : typeof body.detail === 'string'
        ? body.detail
        : `Download failed (${response.status})`
    throw new Error(message)
  }
  return response.blob()
}
