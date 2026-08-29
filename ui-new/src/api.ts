// API access layer: same-origin fetch wrapper shared by App.tsx, AuthPage.tsx and
// groups.tsx. Split out of App.tsx (following the format.ts / icons.tsx precedent)
// so the auth pages can issue requests without importing the whole app shell.

const API_ORIGIN = (import.meta.env.VITE_API_ORIGIN || '').replace(/\/+$/, '')

export function apiUrl(url: string): string {
  if (!API_ORIGIN || /^https?:\/\//i.test(url)) return url
  return `${API_ORIGIN}${url.startsWith('/') ? url : `/${url}`}`
}

// Thrown by fetchJson when the browser's own `fetch` call rejects (backend unreachable,
// DNS failure, offline, CORS preflight failure, etc.) rather than when the backend
// responds with a non-2xx status. Callers use this to distinguish "network is down" from
// a normal HTTP error so they can show a calmer, non-spammy connection indicator instead
// of an error banner (see the job-detail polling loop and the api-status pill).
export class NetworkUnavailableError extends Error {}

// HTTP error carrying the response status, so callers can special-case 401 (redirect to
// login without retrying) separately from generic failures.
export class HttpError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export function isAuthError(err: unknown): boolean {
  return err instanceof HttpError && err.status === 401
}

// Global 401 hook registered by App on startup: clears the session and routes to the
// login page. /api/auth/* responses are exempt — a 401 there just means "wrong password"
// and must surface as a form-level error, not a redirect.
let onUnauthorized: (() => void) | null = null

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler
}

export async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    // credentials: 'include' lets the session cookie ride along when the UI is served
    // from a different origin during local dev (VITE_API_ORIGIN); it is a no-op for the
    // same-origin production deployment.
    response = await fetch(apiUrl(url), { credentials: 'include', ...init })
  } catch (err) {
    // The browser throws a plain TypeError ("Failed to fetch" / "NetworkError when
    // attempting to fetch resource") when the request never reaches a server at all.
    // Surfacing that raw English message directly to users is not useful; wrap it in a
    // clear Chinese explanation instead.
    if (err instanceof TypeError) {
      throw new NetworkUnavailableError('后端连接中断，请检查网络或稍后重试。')
    }
    throw err
  }
  if (response.status === 401 && !url.startsWith('/api/auth/')) {
    onUnauthorized?.()
    throw new HttpError(401, '登录已过期，请重新登录。')
  }
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const payload = (await response.json()) as { detail?: string }
      detail = payload.detail || detail
    } catch {
      detail = response.statusText || detail
    }
    throw new HttpError(response.status, detail)
  }
  return response.json() as Promise<T>
}
