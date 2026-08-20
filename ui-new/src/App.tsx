import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'
import kpmgLogo from './assets/kpmg-logo.svg'
import './App.css'
import {
  ArrowLeftIcon,
  ArrowRightIcon,
  BuildingIcon,
  CheckIcon,
  CloseIcon,
  ClockIcon,
  DocumentIcon,
  DownloadIcon,
  EmptyIllustration,
  SpinnerIcon,
  UploadIcon,
  UserIcon,
  WarningIcon,
} from './icons'
import {
  DEFAULT_UPLOAD_MAX_MB,
  DIFF_TRIAGE_GROUPS,
  JOB_REFRESH_STATUSES,
  RUNNING_STAGES,
  auditConclusion,
  bboxText,
  diffRatio,
  diffSourceGroupsForJob,
  diffTypeLabel,
  evidenceCountBySide,
  evidencePages,
  evidencePagesForSides,
  firstUploadErrorField,
  formatDate,
  formatDuration,
  groupDiffsByTriageAndScope,
  historyProgressLabel,
  initials,
  labelSideForJob,
  latestProgress,
  localized,
  metric,
  metricDisplayName,
  modeLabel,
  modeShortLabel,
  narrativePageRange,
  normalizedDiffScope,
  profileCoverage,
  profileScanText,
  profileWarnings,
  progressPercentText,
  reviewEvidenceMetric,
  reviewValues,
  runningProgressLabel,
  runningStageIndex,
  severityLabel,
  shouldRefreshJob,
  sideLabelsForJob,
  stageLabel,
  statusClass,
  statusLabel,
  summaryNumber,
  triageClass,
  triageLabel,
  validateUpload,
  valueText,
  visualOcrStatusLabel,
} from './format'
import type { DiffSourceScope, DiffTriageScope } from './format'
import { useCountUpText } from './useCountUp'

type Route = {
  page: 'cockpit' | 'history' | 'job' | 'profile'
  jobId?: string
}

type ProjectGroup = {
  id: string
  name: string
}

export type CurrentUser = {
  user_id: string
  display_name: string
  office_line: string
  role_title: string
  avatar_url: string | null
  project_group: ProjectGroup
}

type SessionPayload = {
  user: CurrentUser
  project_group: ProjectGroup
}

type HealthPayload = {
  status?: string
  extraction_engine_version?: string | number | null
  result_version?: string | number | null
  // Not currently returned by /health — read defensively and fall back to
  // DEFAULT_UPLOAD_MAX_MB below if/when the backend doesn't send it.
  upload_max_mb?: number | null
}

const RECENT_HISTORY_LIMIT = 8

export type JobSummary = {
  job_id: string
  company_name?: string | null
  check_mode: 'ah' | 'h_bilingual'
  owner_user_id?: string | null
  owner_display_name?: string | null
  project_group_name?: string | null
  status: string
  started_at: string
  finished_at?: string | null
  duration_seconds?: number | null
  comparison_summary?: Record<string, unknown>
}

export type JobProgressPayload = {
  stage?: string | null
  percent?: number | null
  message?: string | null
  updated_at?: string | null
}

type EvidenceItem = {
  side?: string
  page?: number
  bbox?: [number, number, number, number] | null
  snippet?: string
  section?: string | null
}

type DiffExplanationItem = {
  label: string
  role?: string | null
  a_value?: unknown
  h_value?: unknown
  delta?: unknown
  a_page?: number | null
  h_page?: number | null
  a_snippet?: string | null
  h_snippet?: string | null
}

type DiffExplanation = {
  headline: string
  issue: string
  location?: string
  items?: DiffExplanationItem[]
  review_hint?: string | null
}

type StandardCitation = {
  standard_code?: string | null
  clause?: string | null
  title?: string | null
  snippet?: string | null
  source?: string | null
}

type StandardReasoning = {
  expected?: boolean
  rationale?: string | null
  citations?: StandardCitation[]
  confidence?: number | null
  llm_model?: string | null
}

type ChartCrossCheck = {
  chart_value?: number | null
  table_value?: number | null
  text_value?: number | null
  inconsistency_count?: number | null
}

type ExtractionWarningDetail = {
  blocking?: boolean | null
  category?: string | null
  severity?: string | null
}

type ExtractionAuditPayload = {
  total_pages?: number | null
  scanned_pages?: number[] | null
  coverage_ratio?: number | null
  warning_flags?: string[] | null
  warnings?: string[] | null
  engines?: {
    warning_details?: ExtractionWarningDetail[] | null
  } | null
}

export type ProfileMetricPreview = {
  canonical_key?: string | null
  name?: { zh?: string | null; en?: string | null } | null
  value?: unknown
  value_text?: string | null
  unit?: string | null
  currency?: string | null
  page?: number | null
  occurrence_count?: number | null
  is_internally_consistent?: boolean | null
}

export type ProfileNarrativePreview = {
  topic_key?: string | null
  topic_label?: string | null
  page_range?: [number, number] | null
  word_count?: number | null
  detail_level?: string | null
  summary?: string | null
}

export type ProfilePayload = {
  doc_id?: string | null
  side?: string | null
  total_pages?: number | null
  metric_keys?: number | null
  metric_occurrences?: number | null
  narrative_blocks?: number | null
  structure_nodes?: number | null
  extraction_audit?: ExtractionAuditPayload | null
  warning_flags?: string[] | null
  warnings?: string[] | null
  metrics?: ProfileMetricPreview[] | null
  narratives?: ProfileNarrativePreview[] | null
}

export type DiffItem = {
  diff_id: string
  diff_type: string
  diff_scope?: DiffSourceScope | string | null
  severity: string
  triage?: string
  canonical_key?: string | null
  topic?: { zh?: string | null; en?: string | null }
  summary?: { zh?: string | null; en?: string | null }
  diff_explanation?: DiffExplanation | null
  a_value?: number | null
  h_value?: number | null
  delta?: number | null
  tolerance?: number | null
  evidence?: EvidenceItem[]
  standard_reasoning?: StandardReasoning | null
  chart_cross?: ChartCrossCheck | null
  rule_id?: string | null
  review_status?: string | null
}

export type JobDetail = JobSummary & {
  a_file?: string
  h_file?: string
  error?: string | null
  progress?: JobProgressPayload[]
  profile_a?: ProfilePayload | null
  profile_h?: ProfilePayload | null
  coverage_items?: unknown[]
  diffs?: DiffItem[]
  queue_position?: number | null
}

type ProfileDraft = {
  display_name: string
  office_line: string
  role_title: string
}

export type UploadState = {
  companyName: string
  checkMode: 'ah' | 'h_bilingual'
  bilingualLevel: 'fast' | 'strict'
  visualReviewMode: 'off' | 'smart' | 'strict'
  aFile: File | null
  hFile: File | null
}

export type UploadErrors = {
  companyName?: string
  aFile?: string
  hFile?: string
}

export type UploadErrorField = keyof UploadErrors

const DEFAULT_USER_LABEL = 'Chu, Stanley (SH/FS3)'
const API_ORIGIN = (import.meta.env.VITE_API_ORIGIN || '').replace(/\/+$/, '')
const EMPTY_UPLOAD: UploadState = {
  companyName: '',
  checkMode: 'ah',
  bilingualLevel: 'fast',
  visualReviewMode: 'off',
  aFile: null,
  hFile: null,
}

function parseRoute(): Route {
  const hash = window.location.hash || '#/cockpit'
  try {
    if (hash.startsWith('#/jobs/')) {
      return { page: 'job', jobId: decodeURIComponent(hash.slice('#/jobs/'.length)) }
    }
    if (hash === '#/history') return { page: 'history' }
    if (hash === '#/profile') return { page: 'profile' }
    return { page: 'cockpit' }
  } catch {
    // Malformed percent-encoding in the hash (e.g. "#/jobs/%zz") throws a
    // URIError from decodeURIComponent. This runs both during useState
    // initialization and inside the hashchange listener, and this app has no
    // ErrorBoundary, so an uncaught throw here would white-screen the whole
    // page. Fall back to the default cockpit route instead of crashing.
    return { page: 'cockpit' }
  }
}

function apiUrl(url: string): string {
  if (!API_ORIGIN || /^https?:\/\//i.test(url)) return url
  return `${API_ORIGIN}${url.startsWith('/') ? url : `/${url}`}`
}

// Thrown by fetchJson when the browser's own `fetch` call rejects (backend unreachable,
// DNS failure, offline, CORS preflight failure, etc.) rather than when the backend
// responds with a non-2xx status. Callers use this to distinguish "network is down" from
// a normal HTTP error so they can show a calmer, non-spammy connection indicator instead
// of an error banner (see the job-detail polling loop and the api-status pill).
class NetworkUnavailableError extends Error {}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(apiUrl(url), init)
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
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const payload = (await response.json()) as { detail?: string }
      detail = payload.detail || detail
    } catch {
      detail = response.statusText || detail
    }
    throw new Error(detail)
  }
  return response.json() as Promise<T>
}

function reportUrl(jobId: string, extension: 'pdf' | 'html'): string {
  return apiUrl(`/api/jobs/${encodeURIComponent(jobId)}/report.${extension}`)
}

function App() {
  const [route, setRoute] = useState<Route>(() => parseRoute())
  const [session, setSession] = useState<SessionPayload | null>(null)
  const [health, setHealth] = useState<HealthPayload | null>(null)
  const [history, setHistory] = useState<JobSummary[]>([])
  const [historyScope, setHistoryScope] = useState<'project' | 'mine'>('project')
  const [job, setJob] = useState<JobDetail | null>(null)
  const [jobLoadError, setJobLoadError] = useState<string | null>(null)
  const [latestRecoverableJob, setLatestRecoverableJob] = useState<JobSummary | null>(null)
  const [activeDiff, setActiveDiff] = useState<DiffItem | null>(null)
  const [upload, setUpload] = useState<UploadState>(EMPTY_UPLOAD)
  const [uploadErrors, setUploadErrors] = useState<UploadErrors>({})
  const [validationPulse, setValidationPulse] = useState(0)
  const validationTimeoutRef = useRef<number | null>(null)
  const [profileDraft, setProfileDraft] = useState<ProfileDraft>({
    display_name: 'Chu, Stanley',
    office_line: 'SH/FS3',
    role_title: '',
  })
  const [avatarFile, setAvatarFile] = useState<File | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [avatarVersion, setAvatarVersion] = useState(0)
  // Drives the toolbar api-status pill. 'connecting' is the initial/never-succeeded
  // state; 'disconnected' means a previously-working connection just failed (session
  // bootstrap retry or job-detail polling hit a network error); 'connected' means the
  // most recent relevant request succeeded.
  const [connectionStatus, setConnectionStatus] = useState<'connecting' | 'connected' | 'disconnected'>('connecting')
  const loadJobRequestRef = useRef(0)

  const userLabel = useMemo(() => {
    if (!session?.user) return DEFAULT_USER_LABEL
    return `${session.user.display_name} (${session.user.office_line})`
  }, [session])

  const loadSession = useCallback(async () => {
    const payload = await fetchJson<SessionPayload>('/api/session/current')
    setSession(payload)
    setProfileDraft({
      display_name: payload.user.display_name,
      office_line: payload.user.office_line,
      role_title: payload.user.role_title || '',
    })
  }, [])

  const loadHealth = useCallback(async () => {
    const payload = await fetchJson<HealthPayload>('/health')
    setHealth(payload)
  }, [])

  const loadHistory = useCallback(async (scope: 'project' | 'mine') => {
    const payload = await fetchJson<JobSummary[]>(`/api/jobs/history?scope=${scope}&limit=30`)
    setHistory(payload)
  }, [])

  const loadJob = useCallback(async (jobId: string) => {
    // Tag every call with an incrementing request id so a slow/late response from an
    // abandoned request (user already navigated to a different job, or away from the job
    // page entirely) can never clobber state with stale data — and so its eventual error
    // (if any) is silently dropped instead of surfacing for a request nobody is waiting on
    // anymore.
    const requestId = ++loadJobRequestRef.current
    try {
      const payload = await fetchJson<JobDetail>(`/api/jobs/${encodeURIComponent(jobId)}`)
      if (loadJobRequestRef.current !== requestId) return
      setJob(payload)
      setConnectionStatus('connected')
    } catch (err) {
      if (loadJobRequestRef.current !== requestId) return
      throw err
    }
  }, [])

  const fetchLatestCompletedJob = useCallback(async () => {
    const payload = await fetchJson<JobSummary[]>('/api/jobs/history?scope=project&limit=30')
    return (
      payload.find((item) => item.status === 'done' && item.check_mode === 'ah') ||
      payload.find((item) => item.status === 'done') ||
      null
    )
  }, [])

  const redirectToLatestJob = useCallback((missingJobId: string, latest: JobSummary | null) => {
    if (!latest?.job_id || latest.job_id === missingJobId) return false
    setMessage(`原任务不在当前环境，已打开最新完成任务 ${latest.job_id}`)
    window.location.hash = `#/jobs/${latest.job_id}`
    return true
  }, [])

  const handleMissingJob = useCallback(async (jobId: string, detail: string) => {
    setJobLoadError(`任务不在当前环境存储中：${detail}`)
    try {
      const latest = await fetchLatestCompletedJob()
      setLatestRecoverableJob(latest)
      redirectToLatestJob(jobId, latest)
    } catch {
      setLatestRecoverableJob(null)
    }
  }, [fetchLatestCompletedJob, redirectToLatestJob])

  useEffect(() => {
    if (!window.location.hash) {
      window.location.hash = '#/cockpit'
    }
    const onHashChange = () => {
      setRoute(parseRoute())
      // An evidence dialog opened on the previous route must not stay pinned full-screen
      // over whatever page the user navigated to (back/forward or a nav click while the
      // dialog was open).
      setActiveDiff(null)
      // A stale error/message banner from the page you're leaving (e.g. a failed history
      // fetch) shouldn't keep floating at the top of whatever page you navigate to next.
      setError(null)
      setMessage(null)
    }
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  useEffect(() => {
    let cancelled = false
    let retryTimeoutId: number | null = null

    const attempt = () => {
      loadSession()
        .then(() => {
          if (!cancelled) setConnectionStatus('connected')
        })
        .catch((err: unknown) => {
          if (cancelled) return
          setConnectionStatus('disconnected')
          setError(err instanceof Error ? err.message : String(err))
          // The session request is how the app first learns whether the backend is
          // reachable at all. Previously a failure here left the api-status pill stuck on
          // "正在连接" forever with no way to recover short of a manual page reload; retry
          // on a simple fixed interval until it succeeds.
          retryTimeoutId = window.setTimeout(attempt, 5000)
        })
    }
    attempt()

    return () => {
      cancelled = true
      if (retryTimeoutId !== null) window.clearTimeout(retryTimeoutId)
    }
  }, [loadSession])

  useEffect(() => {
    if (route.page === 'cockpit') {
      loadHealth().catch(() => setHealth(null))
    }
  }, [loadHealth, route.page])

  useEffect(() => {
    if (route.page === 'history' || route.page === 'cockpit') {
      loadHistory(historyScope).catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
    }
  }, [historyScope, loadHistory, route.page])

  const historyHasRunning = history.some((item) => JOB_REFRESH_STATUSES.has(item.status))

  useEffect(() => {
    if (route.page !== 'history' && route.page !== 'cockpit') return
    if (!historyHasRunning) return
    const intervalId = window.setInterval(() => {
      loadHistory(historyScope).catch(() => {})
    }, 2500)
    return () => window.clearInterval(intervalId)
  }, [historyHasRunning, historyScope, loadHistory, route.page])

  useEffect(() => {
    if (route.page === 'job' && route.jobId) {
      setJob(null)
      setJobLoadError(null)
      setLatestRecoverableJob(null)
      loadJob(route.jobId).catch((err: unknown) => {
        const detail = err instanceof Error ? err.message : String(err)
        if (/job not found|404/i.test(detail)) {
          handleMissingJob(route.jobId || '', detail)
          return
        }
        setJobLoadError(detail)
        setError(detail)
      })
    }
  }, [handleMissingJob, loadJob, route])

  useEffect(() => {
    if (route.page !== 'job' || !route.jobId || !shouldRefreshJob(job)) return
    // `route.jobId` is narrowed to `string` by the guard above, but that narrowing of a
    // property access doesn't survive into the nested setInterval closure below (a fresh
    // render could in principle produce a different `route` by the time the closure runs).
    // Capture it in a local const once so the closure gets a plain `string`.
    const jobId = route.jobId
    const intervalId = window.setInterval(() => {
      loadJob(jobId).catch((err: unknown) => {
        const detail = err instanceof Error ? err.message : String(err)
        if (/job not found|404/i.test(detail)) {
          // The task was deleted / isn't in this environment's storage anymore — polling
          // forever would just retry a request that can never succeed. Stop this interval
          // and fall back to the same "missing job" recovery flow used for the initial load.
          window.clearInterval(intervalId)
          handleMissingJob(jobId, detail)
          return
        }
        // Anything else here (backend unreachable, transient 5xx, etc.) used to call
        // setError on every single poll — a fresh error popup every 2.5s for as long as
        // the backend stayed down. Surface it via the shared connection-status pill
        // instead and keep polling; the backend may recover on its own.
        setConnectionStatus('disconnected')
      })
    }, 2500)
    return () => window.clearInterval(intervalId)
  }, [handleMissingJob, job?.status, loadJob, route.jobId, route.page])

  const clearValidationTimeout = useCallback(() => {
    if (validationTimeoutRef.current !== null) {
      window.clearTimeout(validationTimeoutRef.current)
      validationTimeoutRef.current = null
    }
  }, [])

  useEffect(() => clearValidationTimeout, [clearValidationTimeout])

  const showUploadErrors = useCallback((errors: UploadErrors) => {
    clearValidationTimeout()
    setUploadErrors(errors)
    setValidationPulse((current) => current + 1)
    validationTimeoutRef.current = window.setTimeout(() => {
      setUploadErrors({})
      validationTimeoutRef.current = null
    }, 1500)
  }, [clearValidationTimeout])

  const clearUploadError = useCallback((field: UploadErrorField) => {
    setUploadErrors((current) => {
      if (!current[field]) return current
      const next = { ...current }
      delete next[field]
      return next
    })
  }, [])

  async function submitJob(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    // A submit can also be triggered programmatically (e.g. form.requestSubmit()),
    // bypassing the submit button's `disabled` attribute. Guard here too so a job can
    // never be double-submitted while one is already in flight.
    if (busy === 'job') return
    const errors = validateUpload(upload, health?.upload_max_mb ?? DEFAULT_UPLOAD_MAX_MB)
    if (Object.keys(errors).length) {
      showUploadErrors(errors)
      setError(null)
      setMessage(null)
      return
    }
    // validateUpload only returns no aFile/hFile error when both are already selected, but
    // that guarantee isn't visible to TypeScript through the `errors` object alone — narrow
    // explicitly so the FormData.append calls below get `File`, not `File | null`.
    const { aFile, hFile } = upload
    if (!aFile || !hFile) return
    clearValidationTimeout()
    setBusy('job')
    setError(null)
    setMessage(null)
    setUploadErrors({})
    const form = new FormData()
    form.append('company_name', upload.companyName.trim())
    form.append('check_mode', upload.checkMode)
    form.append('bilingual_level', upload.bilingualLevel)
    form.append('visual_review_mode', upload.visualReviewMode)
    form.append('a_file', aFile)
    form.append('h_file', hFile)
    try {
      const created = await fetchJson<JobDetail>('/api/jobs/', { method: 'POST', body: form })
      setUpload(EMPTY_UPLOAD)
      setMessage('核查任务已生成。')
      window.location.hash = `#/jobs/${created.job_id}`
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  async function submitProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy('profile')
    setError(null)
    setMessage(null)
    try {
      const user = await fetchJson<CurrentUser>('/api/users/current', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(profileDraft),
      })
      setSession((current) => current && { ...current, user, project_group: user.project_group })
      setMessage('个人资料已更新。')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  async function submitAvatar(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!avatarFile) {
      setError('请选择头像文件。')
      return
    }
    setBusy('avatar')
    setError(null)
    setMessage(null)
    const form = new FormData()
    form.append('avatar', avatarFile)
    try {
      const payload = await fetchJson<{ avatar_url: string | null; user: CurrentUser }>(
        '/api/users/current/avatar',
        { method: 'POST', body: form },
      )
      setSession((current) => current && { ...current, user: payload.user, project_group: payload.user.project_group })
      setAvatarFile(null)
      setAvatarVersion((value) => value + 1)
      setMessage('头像已更新。')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  function avatarNode(className: string) {
    const avatarUrl = session?.user.avatar_url
    if (avatarUrl) {
      return <img className={className} src={`${avatarUrl}?v=${avatarVersion}`} alt={userLabel} />
    }
    return <span className={`${className} avatar-fallback`}>{initials(session?.user)}</span>
  }

  return (
    <div className="shell">
      <header className="app-toolbar">
        <a className="brand" href="#/cockpit" aria-label="KPMG 核查工作台">
          <img className="kpmg-logo" src={kpmgLogo} alt="KPMG" />
          <span className="brand-tagline">
            <span>多重披露，一次核对</span>
            <strong>MORE FILINGS, ONE SOURCE OF TRUTH</strong>
          </span>
        </a>
        <nav className="nav" aria-label="主要导航">
          <a className={route.page === 'cockpit' ? 'active' : ''} href="#/cockpit">
            <span>核查工作台</span>
            <small>Audit cockpit</small>
          </a>
          <a className={route.page === 'history' ? 'active' : ''} href="#/history">
            <span>项目历史</span>
            <small>Engagement history</small>
          </a>
          <a className={route.page === 'profile' ? 'active' : ''} href="#/profile">
            <span>个人资料</span>
            <small>User profile</small>
          </a>
        </nav>
        <div className="toolbar-actions">
          <a className="user-strip" href="#/profile">
            {avatarNode('nav-avatar')}
            <span>
              <strong>{userLabel}</strong>
              <small>{session?.user.role_title || 'Senior manager'}</small>
            </span>
          </a>
          <span id="statusBadge" className={`api-status ${connectionStatus}`}>
            {connectionStatus === 'connected' ? (
              <CheckIcon size={13} />
            ) : connectionStatus === 'disconnected' ? (
              <WarningIcon size={13} />
            ) : (
              <SpinnerIcon size={13} className="spin" />
            )}
            {connectionStatus === 'connected' ? 'API 已连接' : connectionStatus === 'disconnected' ? '连接中断' : '正在连接'}
          </span>
        </div>
      </header>

      <main className="workspace">
        <header className={`topbar ${route.page === 'job' ? 'job-topbar' : ''}`}>
          {route.page === 'job' ? (
            <JobReportActions job={job} />
          ) : (
            <div className={`page-context-bar ${route.page === 'cockpit' ? 'has-ticker' : ''}`} aria-label="页面位置">
              <span className="page-context-label">{pageContextLabel(route)}</span>
              {route.page === 'cockpit' && (
                <CockpitTickerBar
                  health={health}
                  history={history}
                />
              )}
            </div>
          )}
        </header>

        {(error || message) && (
          <div className={error ? 'notice error' : 'notice'}>
            <span>{error || message}</span>
            <button type="button" aria-label="关闭提示" onClick={() => { setError(null); setMessage(null) }}>
              <CloseIcon size={14} />
            </button>
          </div>
        )}

        {route.page === 'cockpit' && (
          <CockpitPage
            upload={upload}
            busy={busy}
            history={history}
            uploadErrors={uploadErrors}
            validationPulse={validationPulse}
            setUpload={setUpload}
            clearUploadError={clearUploadError}
            submitJob={submitJob}
          />
        )}

        {route.page === 'history' && (
          <HistoryPage
            scope={historyScope}
            setScope={setHistoryScope}
            history={history}
          />
        )}

        {route.page === 'job' && (
          jobLoadError && !job ? (
            <MissingJobFallback
              jobId={route.jobId || ''}
              latestJob={latestRecoverableJob}
              detail={jobLoadError}
            />
          ) : (
            <JobDetailPage job={job} setActiveDiff={setActiveDiff} />
          )
        )}

        {route.page === 'profile' && (
          <ProfilePage
            session={session}
            draft={profileDraft}
            avatarFile={avatarFile}
            busy={busy}
            avatarNode={avatarNode}
            setDraft={setProfileDraft}
            setAvatarFile={setAvatarFile}
            submitProfile={submitProfile}
            submitAvatar={submitAvatar}
          />
        )}
      </main>

      {activeDiff && <EvidenceDialog diff={activeDiff} job={job} onClose={() => setActiveDiff(null)} />}
    </div>
  )
}

function pageContextLabel(route: Route): string {
  if (route.page === 'history') return '项目历史'
  if (route.page === 'profile') return '个人资料'
  return '核查工作台'
}

function JobReportActions({ job }: { job: JobDetail | null }) {
  if (!job) {
    return (
      <div className="job-report-actions" aria-label="报告操作">
        <span className="job-report-action-link disabled" aria-disabled="true"><ClockIcon size={14} />下载 HTML</span>
        <span className="job-report-action-link disabled" aria-disabled="true"><ClockIcon size={14} />下载 PDF</span>
        <a className="job-report-action-link" href="#/history"><ArrowLeftIcon size={14} />返回项目历史</a>
      </div>
    )
  }
  if (job.status === 'failed') {
    // A failed job will never produce a report — showing "等待 HTML/PDF" here promises a
    // result that is never coming. Say so plainly instead.
    return (
      <div className="job-report-actions" aria-label="报告操作">
        <span className="job-report-action-link disabled" aria-disabled="true"><WarningIcon size={14} />任务失败，无报告</span>
        <a className="job-report-action-link" href="#/history"><ArrowLeftIcon size={14} />返回项目历史</a>
      </div>
    )
  }
  if (job.status !== 'done') {
    return (
      <div className="job-report-actions" aria-label="报告操作">
        <span className="job-report-action-link disabled" aria-disabled="true"><ClockIcon size={14} />等待 HTML</span>
        <span className="job-report-action-link disabled" aria-disabled="true"><ClockIcon size={14} />等待 PDF</span>
        <a className="job-report-action-link" href="#/history"><ArrowLeftIcon size={14} />返回项目历史</a>
      </div>
    )
  }
  return (
    <div className="job-report-actions" aria-label="报告操作">
      <a className="job-report-action-link" href={reportUrl(job.job_id, 'html')} download={`AHCC-${job.job_id}.html`}><DownloadIcon size={14} />下载 HTML</a>
      <a className="job-report-action-link" href={reportUrl(job.job_id, 'pdf')}><DownloadIcon size={14} />下载 PDF</a>
      <a className="job-report-action-link" href="#/history"><ArrowLeftIcon size={14} />返回项目历史</a>
    </div>
  )
}

function CockpitTickerBar({
  health,
  history,
}: {
  health: HealthPayload | null
  history: JobSummary[]
}) {
  const pendingCount = history.filter((item) => item.status !== 'done' && item.status !== 'failed').length
  const latestDone = history.find((item) => item.status === 'done')
  const latestDoneLabel = latestDone
    ? `${formatDate(latestDone.started_at)} · ${formatDuration(latestDone.duration_seconds)}`
    : '暂无完成记录'
  const resultVersion = health?.result_version ? `v${health.result_version}` : '待连接'
  const extractionVersion = health?.extraction_engine_version || '等待 API 连接'
  const latestProject = history[0]
  const latestProjectLabel = latestProject
    ? `${latestProject.company_name || latestProject.job_id} · ${modeShortLabel(latestProject.check_mode)}`
    : '等待首个项目核查'
  const tickerItems = [
    ['结果规则', resultVersion],
    ['抽取引擎', extractionVersion],
    ['待处理', `${pendingCount} 项`],
    ['最近完成', latestDoneLabel],
    ['最近项目', latestProjectLabel],
    ['提交前检查', '项目名称 + 双 PDF'],
  ]
  const tickerLoop = [...tickerItems, ...tickerItems]

  return (
    <div className="ticker-viewport" aria-label="工作台动态" tabIndex={0}>
      <div className="ticker-track">
        {tickerLoop.map(([label, value], index) => (
          <span className="ticker-item" key={`${label}-${index}`}>
            <span>{label}</span>
            <strong>{value}</strong>
            <i className="ticker-separator" aria-hidden="true">·</i>
          </span>
        ))}
      </div>
    </div>
  )
}

function CockpitPage({
  upload,
  busy,
  history,
  uploadErrors,
  validationPulse,
  setUpload,
  clearUploadError,
  submitJob,
}: {
  upload: UploadState
  busy: string | null
  history: JobSummary[]
  uploadErrors: UploadErrors
  validationPulse: number
  setUpload: (value: UploadState | ((current: UploadState) => UploadState)) => void
  clearUploadError: (field: UploadErrorField) => void
  submitJob: (event: FormEvent<HTMLFormElement>) => void
}) {
  const latest = history.slice(0, RECENT_HISTORY_LIMIT)
  const companyInputRef = useRef<HTMLInputElement>(null)
  const aFileInputRef = useRef<HTMLInputElement>(null)
  const hFileInputRef = useRef<HTMLInputElement>(null)
  const invalidPulseClass = `shake-${validationPulse % 2}` as 'shake-0' | 'shake-1'
  const invalidClass = (field: UploadErrorField) => uploadErrors[field] ? `field-invalid ${invalidPulseClass}` : ''

  useEffect(() => {
    const firstInvalid = firstUploadErrorField(uploadErrors)
    if (!firstInvalid) return
    const target = firstInvalid === 'companyName'
      ? companyInputRef.current
      : firstInvalid === 'aFile'
        ? aFileInputRef.current
        : hFileInputRef.current
    target?.focus({ preventScroll: true })
    target?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [uploadErrors, validationPulse])

  return (
    <section className="command-surface" aria-label="一体化工作台">
      <div className="command-hero">
        <div className="command-hero-copy">
          <p className="eyebrow">证据工作流</p>
          <h2>披露一致性核查</h2>
          <p className="panel-copy">在同一工作台完成上传、模式选择和项目组历史追踪，核查完成后可直接进入证据审阅。</p>
        </div>
        <div className="command-stats" aria-label="工作台概览">
          <span><strong>{modeShortLabel(upload.checkMode)}</strong> 当前模式</span>
        </div>
      </div>

      <div className="command-layout">
        <form className="command-main" onSubmit={submitJob}>
          <div className="command-section-head">
            <span>01</span>
            <div>
              <h3>新建核查</h3>
              <p>上传两份 PDF，系统自动生成项目组共享记录与证据链。</p>
            </div>
          </div>
          <label className={`field-block ${invalidClass('companyName')}`}>
            项目名称
            <input
              ref={companyInputRef}
              value={upload.companyName}
              onChange={(event) => {
                clearUploadError('companyName')
                setUpload((current) => ({ ...current, companyName: event.target.value }))
              }}
              placeholder="请输入项目名称"
              maxLength={80}
              aria-invalid={Boolean(uploadErrors.companyName)}
              aria-describedby={uploadErrors.companyName ? 'project-name-error' : undefined}
            />
            {uploadErrors.companyName && (
              <span className="field-error" id="project-name-error">{uploadErrors.companyName}</span>
            )}
          </label>
          <div className="field-label">核查模式</div>
          <div className="segmented" aria-label="核查模式">
            <button
              type="button"
              className={upload.checkMode === 'ah' ? 'selected' : ''}
              onClick={() => setUpload((current) => ({ ...current, checkMode: 'ah' }))}
            >
              A/H 股报告核查
            </button>
            <button
              type="button"
              className={upload.checkMode === 'h_bilingual' ? 'selected' : ''}
              onClick={() => setUpload((current) => ({ ...current, checkMode: 'h_bilingual' }))}
            >
              H 股中英文核查
            </button>
          </div>
          <div className="field-label">核查深度</div>
          <div className="depth-control" role="group" aria-label="核查深度">
            <button
              type="button"
              className={`depth-option ${upload.bilingualLevel === 'fast' ? 'selected' : ''}`}
              aria-pressed={upload.bilingualLevel === 'fast'}
              onClick={() => setUpload((current) => ({ ...current, bilingualLevel: 'fast' }))}
            >
              <strong>快速核查</strong>
              <small>标准证据链，适合日常复核</small>
            </button>
            <button
              type="button"
              className={`depth-option ${upload.bilingualLevel === 'strict' ? 'selected' : ''}`}
              aria-pressed={upload.bilingualLevel === 'strict'}
              onClick={() => setUpload((current) => ({ ...current, bilingualLevel: 'strict' }))}
            >
              <strong>严格核查</strong>
              <small>扩展规则覆盖，适合出具前复核</small>
            </button>
          </div>
          <div className="field-label">视觉复核</div>
          <div className="visual-review-control" role="group" aria-label="视觉复核模式">
            <button
              type="button"
              className={`visual-review-option ${upload.visualReviewMode === 'off' ? 'selected' : ''}`}
              aria-pressed={upload.visualReviewMode === 'off'}
              onClick={() => setUpload((current) => ({ ...current, visualReviewMode: 'off' }))}
            >
              <strong>标准核查</strong>
              <small>默认保证完成 · 不运行高成本 OCR</small>
            </button>
            <button
              type="button"
              className={`visual-review-option ${upload.visualReviewMode === 'smart' ? 'selected' : ''}`}
              aria-pressed={upload.visualReviewMode === 'smart'}
              onClick={() => setUpload((current) => ({ ...current, visualReviewMode: 'smart' }))}
            >
              <strong>智能视觉抽样</strong>
              <small>Smart visual review · 小预算覆盖高风险页</small>
            </button>
            <button
              type="button"
              className={`visual-review-option ${upload.visualReviewMode === 'strict' ? 'selected' : ''}`}
              aria-pressed={upload.visualReviewMode === 'strict'}
              onClick={() => setUpload((current) => ({ ...current, visualReviewMode: 'strict' }))}
            >
              <strong>严格视觉核查</strong>
              <small>Strict visual review · 扩大抽样范围，耗时会增加</small>
            </button>
          </div>
          <p className="visual-review-note">默认保证完成：先抽取全量文本层/表格层数值；智能/严格模式再按预算执行视觉 OCR。</p>
          <div className="file-row">
            <label className={`file-card ${invalidClass('aFile')}`}>
              <span className="file-kicker"><DocumentIcon size={14} />PDF</span>
              <strong>{upload.checkMode === 'h_bilingual' ? 'H 股中文报告' : 'A 股报告'}</strong>
              <small>{upload.aFile ? upload.aFile.name : '点击选择或拖入文件'}</small>
              <input
                ref={aFileInputRef}
                type="file"
                accept="application/pdf,.pdf"
                aria-invalid={Boolean(uploadErrors.aFile)}
                aria-describedby={uploadErrors.aFile ? 'a-file-error' : undefined}
                onChange={(event) => {
                  const file = event.target.files?.[0] || null
                  if (file) clearUploadError('aFile')
                  setUpload((current) => ({ ...current, aFile: file }))
                }}
              />
              {uploadErrors.aFile && (
                <span className="field-error" id="a-file-error">{uploadErrors.aFile}</span>
              )}
            </label>
            <label className={`file-card ${invalidClass('hFile')}`}>
              <span className="file-kicker"><DocumentIcon size={14} />PDF</span>
              <strong>{upload.checkMode === 'h_bilingual' ? 'H 股英文报告' : 'H 股报告'}</strong>
              <small>{upload.hFile ? upload.hFile.name : '点击选择或拖入文件'}</small>
              <input
                ref={hFileInputRef}
                type="file"
                accept="application/pdf,.pdf"
                aria-invalid={Boolean(uploadErrors.hFile)}
                aria-describedby={uploadErrors.hFile ? 'h-file-error' : undefined}
                onChange={(event) => {
                  const file = event.target.files?.[0] || null
                  if (file) clearUploadError('hFile')
                  setUpload((current) => ({ ...current, hFile: file }))
                }}
              />
              {uploadErrors.hFile && (
                <span className="field-error" id="h-file-error">{uploadErrors.hFile}</span>
              )}
            </label>
          </div>
          <button
            className={`primary job-submit-button ${busy === 'job' ? 'is-breathing' : ''}`}
            type="submit"
            disabled={busy === 'job'}
            aria-busy={busy === 'job'}
          >
            {busy === 'job' ? (
              <>
                <SpinnerIcon size={15} className="spin" />
                正在生成核查任务
              </>
            ) : (
              <>
                开始核查
                <ArrowRightIcon size={15} />
              </>
            )}
          </button>
        </form>

        <aside className="command-history">
          <div className="command-section-head">
            <span>02</span>
            <div>
              <h3><BuildingIcon size={16} />项目组最近核查</h3>
              <p>同组历史在这里连续展示，方便回到上一次证据复核。</p>
            </div>
          </div>
          <div className="job-list compact">
            {latest.length ? latest.map((item) => <JobRow key={item.job_id} item={item} />) : <EmptyState label="暂无项目历史" />}
          </div>
          <div className="command-history-actions">
            <a className="command-history-link primary" href="#/history">查看全部项目历史</a>
          </div>
        </aside>
      </div>
    </section>
  )
}

function HistoryPage({
  scope,
  setScope,
  history,
}: {
  scope: 'project' | 'mine'
  setScope: (scope: 'project' | 'mine') => void
  history: JobSummary[]
}) {
  const summary = useMemo(() => {
    const done = history.filter((j) => j.status === 'done')
    const failed = history.filter((j) => j.status === 'failed')
    const realDiffs = history.reduce(
      (sum, j) => sum + (Number(j.comparison_summary?.real_diff_count) || 0),
      0
    )
    const avgDuration = done.length
      ? done.reduce((sum, j) => sum + (j.duration_seconds || 0), 0) / done.length
      : 0
    return {
      total: history.length,
      done: done.length,
      failed: failed.length,
      realDiffs,
      avgDuration,
    }
  }, [history])

  return (
    <section className="panel wide">
      <div className="panel-head">
        <div>
          <p className="eyebrow">共享历史</p>
          <h2>{scope === 'project' ? 'SH/FS3 项目组历史' : '我的核查历史'}</h2>
          <p className="panel-copy">默认展示项目组共享历史，可切换查看当前用户提交的任务。</p>
        </div>
        <div className="segmented small">
          <button type="button" className={scope === 'mine' ? 'selected' : ''} onClick={() => setScope('mine')}>我的</button>
          <button type="button" className={scope === 'project' ? 'selected' : ''} onClick={() => setScope('project')}>项目组</button>
        </div>
      </div>

      <div className="detail-kpi-grid history-summary">
        <DashboardMetric
          label="总核查数"
          value={String(summary.total)}
          note="当前列表范围内"
          tone="accent"
        />
        <DashboardMetric
          label="已完成"
          value={String(summary.done)}
          note={`${summary.failed} 个失败`}
          tone={summary.failed > 0 ? 'warning' : 'accent'}
        />
        <DashboardMetric
          label="真实差异"
          value={String(summary.realDiffs)}
          note="累计发现"
          tone={summary.realDiffs > 0 ? 'critical' : 'accent'}
        />
        <DashboardMetric
          label="平均耗时"
          value={formatDuration(summary.avgDuration)}
          note="已完成任务"
        />
      </div>

      <div className="history-head">
        <span>项目</span>
        <span>状态</span>
        <span>真实差异</span>
        <span>检查时间</span>
        <span>核查耗时</span>
      </div>
      <div className="history-table">
        {history.length ? history.map((item) => <JobRow key={item.job_id} item={item} table />) : (
          <EmptyState label="暂无项目历史" ctaHref="#/cockpit" ctaLabel="去核查工作台新建任务" />
        )}
      </div>
    </section>
  )
}

function MissingJobFallback({
  jobId,
  latestJob,
  detail,
}: {
  jobId: string
  latestJob: JobSummary | null
  detail: string
}) {
  return (
    <section className="missing-job-panel">
      <div>
        <p className="eyebrow"><WarningIcon size={12} />Job Recovery</p>
        <h2>任务不在当前环境存储中</h2>
        <p>
          任务 {jobId || '—'} 未在当前 Zeabur 存储中找到。通常是部署重建前没有挂载持久化卷，
          旧 SQLite 或上传 PDF 已丢失；新任务会使用当前线上引擎重新生成结果。
        </p>
        <small>{detail}</small>
      </div>
      {latestJob ? (
        <div className="missing-job-latest">
          <span>最新完成任务</span>
          <strong>{latestJob.company_name || latestJob.job_id}</strong>
          <small>{modeLabel(latestJob.check_mode)} · {formatDate(latestJob.finished_at || latestJob.started_at)}</small>
        </div>
      ) : (
        <div className="missing-job-latest">
          <span>最新完成任务</span>
          <strong>暂无可恢复任务</strong>
          <small>请重新上传同一组 PDF 生成新任务。</small>
        </div>
      )}
      <div className="missing-job-actions">
        {latestJob && (
          <a className="primary" href={`#/jobs/${latestJob.job_id}`}>
            打开最新完成任务
            <ArrowRightIcon size={15} />
          </a>
        )}
        <a className="ghost" href="#/cockpit"><UploadIcon size={15} />重新上传 PDF</a>
        <a className="ghost" href="#/history"><ClockIcon size={15} />查看项目历史</a>
      </div>
    </section>
  )
}

function JobRunningProgress({ job }: { job: JobDetail }) {
  const progress = latestProgress(job)
  const rawPercent = progress?.percent
  const fillPercent = typeof rawPercent === 'number' && Number.isFinite(rawPercent)
    ? Math.max(0, Math.min(100, rawPercent))
    : 0
  const percentText = progressPercentText(rawPercent)
  const stageIdx = runningStageIndex(progress?.stage || job.status)
  // While a job is still queued (not yet picked up by the worker), the backend reports
  // how many other pending jobs are ahead of it. Surface that instead of the generic
  // "排队等待" fallback so the user knows roughly how long the wait will be.
  const queuePositionLabel = job.status === 'pending' && typeof job.queue_position === 'number' && job.queue_position > 0
    ? `排队中，前方 ${job.queue_position} 个任务`
    : null
  return (
    <div className="running-progress">
      <div className="running-progress-head">
        <span>{queuePositionLabel || progress?.message || stageLabel(progress?.stage || job.status)}</span>
        {percentText && <strong>{percentText}</strong>}
      </div>
      <div className="progress-track">
        <div className="progress-fill" style={{ width: `${fillPercent}%` }} />
      </div>
      <ol className="stage-stepper">
        {RUNNING_STAGES.map((stage, index) => {
          const state = index < stageIdx ? 'done' : index === stageIdx ? 'active' : 'todo'
          return (
            <li key={stage} className={`stage-step ${state}`}>
              <span className="stage-dot">{state === 'done' ? <CheckIcon /> : null}</span>
              <span>{stageLabel(stage)}</span>
            </li>
          )
        })}
      </ol>
    </div>
  )
}

function JobDetailPage({
  job,
  setActiveDiff,
}: {
  job: JobDetail | null
  setActiveDiff: (diff: DiffItem) => void
}) {
  const [selectedTriage, setSelectedTriage] = useState<DiffTriageScope>('real')
  const [selectedSource, setSelectedSource] = useState<DiffSourceScope>('cross_report')
  if (!job) return <JobDetailLoading />
  const summary = job.comparison_summary || {}
  const diffs = job.diffs || []
  const labels = sideLabelsForJob(job)
  const conclusion = auditConclusion(job, diffs)
  const sourceGroups = diffSourceGroupsForJob(job)
  const activeSource = sourceGroups.some((item) => item.key === selectedSource) ? selectedSource : sourceGroups[0].key
  const diffGroups = groupDiffsByTriageAndScope(diffs, job.check_mode)
  const reviewMetric = reviewEvidenceMetric(summary, diffs)
  const selectedTriageGroup = DIFF_TRIAGE_GROUPS.find((item) => item.key === selectedTriage) || DIFF_TRIAGE_GROUPS[0]
  const selectedSourceGroup = sourceGroups.find((item) => item.key === activeSource) || sourceGroups[0]
  const activeDiffs = diffGroups[selectedTriage][activeSource]
  return (
    <section className="stack detail-dashboard">
      <div className={`audit-conclusion-strip ${conclusion.tone}`}>
        <div className="audit-conclusion-main">
          <p className="eyebrow">Audit Conclusion / 差异与证据复核</p>
          <div className="audit-title-row">
            <h2 className="audit-project-title">{job.company_name || '项目名称待确认'}</h2>
            <div className="audit-meta-row">
              <span>Job {job.job_id}</span>
              <span>{modeLabel(job.check_mode)}</span>
              <span>{job.owner_display_name || 'Chu, Stanley'}</span>
            </div>
          </div>
          <h3 className="audit-result-title">{conclusion.title}</h3>
          <p>{conclusion.copy}</p>
          {shouldRefreshJob(job) ? <JobRunningProgress job={job} /> : null}
          <div className="audit-conclusion-chips">
            <span>{labels.a}事实 <strong>{metric(summary, 'a_fact_count')}</strong></span>
            <span>{labels.h}事实 <strong>{metric(summary, 'h_fact_count')}</strong></span>
            <span>跨页事件 <strong>{metric(summary, 'matched_event_count')}</strong></span>
            <span>核心预警 <strong>{valueText(conclusion.blocking)}</strong></span>
            <span>辅助提示 <strong>{valueText(conclusion.auxiliary)}</strong></span>
            <span>证据定位 <strong>{valueText(conclusion.evidenceItems)}</strong></span>
          </div>
        </div>
        <div className="audit-conclusion-side">
          <span className={`audit-pill ${conclusion.tone}`}>{conclusion.pill}</span>
          <span className={statusClass(job.status)}>{statusLabel(job.status)}</span>
          {shouldRefreshJob(job) ? <small>{runningProgressLabel(job)}</small> : null}
          {visualOcrStatusLabel(summary) ? <small>{visualOcrStatusLabel(summary)}</small> : null}
        </div>
      </div>

      <div className="detail-kpi-grid">
        <DashboardMetric
          tone="accent"
          label={labels.factLabel}
          value={`${metric(summary, 'a_fact_count')} / ${metric(summary, 'h_fact_count')}`}
          note={`${labels.a}/${labels.h} · 全量指标 ${metric(summary, 'a_metric_keys')} / ${metric(summary, 'h_metric_keys')}`}
        />
        <DashboardMetric
          tone={summaryNumber(summary, 'real_diff_count') ? 'critical' : undefined}
          label="差异"
          value={`${metric(summary, 'real_diff_count')} / ${metric(summary, 'expected_diff_count')}`}
          note={`真实 / 预期 · 内部指标 ${metric(summary, 'internal_inconsistency_count')} · 事件自身 ${metric(summary, 'internal_event_diff_count')}`}
        />
        <DashboardMetric
          tone={summaryNumber(summary, 'unresolved_diff_count') ? 'warning' : undefined}
          label="待人工复核"
          value={metric(summary, 'unresolved_diff_count')}
          note="未决差异 · 需人工判定"
        />
        <DashboardMetric
          tone={summaryNumber(summary, 'blocking_warning_count') ? 'warning' : undefined}
          label="提取预警"
          value={`${metric(summary, 'blocking_warning_count')} / ${metric(summary, 'aux_warning_count')}`}
          note={`核心 / 辅助 · 总计 ${metric(summary, 'warning_count')}`}
        />
        <DashboardMetric
          tone="teal"
          label="耗时"
          value={formatDuration(job.duration_seconds)}
          note={`证据定位 ${conclusion.evidenceItems} · 总差异 ${metric(summary, 'total_diff_count')}`}
        />
        <DashboardMetric
          tone={reviewMetric.tone}
          label="证据审阅"
          value={`${reviewMetric.reviewQueueCount} 项`}
          note={`真实 ${reviewMetric.real} · 待复核 ${reviewMetric.unresolved} · 已定位 ${reviewMetric.evidenceLocated}/${reviewMetric.totalDiff}`}
        />
      </div>

      {job.check_mode !== 'h_bilingual' ? (
        <div className="profile-showcase">
          <ProfileCard title="A 股画像" sideLabel={labels.a} profile={job.profile_a} />
          <ProfileCard title="H 股画像" sideLabel={labels.h} profile={job.profile_h} />
        </div>
      ) : (
        <BilingualPageReview
          job={job}
          diffs={diffs}
          labels={labels}
          setActiveDiff={setActiveDiff}
        />
      )}

      <div className="panel diff-review-panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">复核队列</p>
            <h2>差异与证据</h2>
            <p className="panel-copy">先选择分流和来源，只展开当前需要复核的一组差异；点击单项进入全屏证据审阅。</p>
          </div>
          <span className="mode-chip">{diffs.length} 项</span>
        </div>
        {diffs.length ? (
        <div className="diff-drilldown-board">
          <div className="diff-drilldown-grid" aria-label="差异分类选择">
            {DIFF_TRIAGE_GROUPS.map((triageGroup) => (
              sourceGroups.map((sourceGroup) => {
                const count = diffGroups[triageGroup.key][sourceGroup.key].length
                const selected = selectedTriage === triageGroup.key && activeSource === sourceGroup.key
                return (
                  <button
                    key={`${triageGroup.key}-${sourceGroup.key}`}
                    type="button"
                    className={`diff-drilldown-card ${triageGroup.tone} ${selected ? 'selected' : ''}`}
                    aria-pressed={selected}
                    onClick={() => {
                      setSelectedTriage(triageGroup.key)
                      setSelectedSource(sourceGroup.key)
                    }}
                  >
                    <span className="diff-source-label">{sourceGroup.label}</span>
                    <strong>{count}</strong>
                    <small className="diff-triage-label">{triageGroup.label}</small>
                  </button>
                )
              })
            ))}
          </div>

          <div className="diff-active-queue">
            <div className="diff-active-head">
              <div>
                <p className="eyebrow">Selected Review Queue</p>
                <h3>{selectedTriageGroup.label} · {selectedSourceGroup.label}</h3>
                <p>{selectedSourceGroup.description}</p>
              </div>
              <span className={`triage ${triageClass(selectedTriage)}`}>{activeDiffs.length} 项</span>
            </div>
            <div className="diff-active-list">
              {activeDiffs.length ? activeDiffs.map((diff) => {
                const values = reviewValues(diff)
                return (
                  <article className="diff-source-row" id={`diff-row-${diff.diff_id}`} key={diff.diff_id}>
                    <div className="diff-source-row-main">
                      <span className={`severity ${diff.severity}`}>{severityLabel(diff.severity)}</span>
                      <span className="type-chip">{diffTypeLabel(diff.diff_type)}</span>
                      <strong>{diff.diff_explanation?.headline || localized(diff.topic)}</strong>
                      <p>{diff.diff_explanation?.issue || localized(diff.summary)}</p>
                      <small>规则 ID {diff.rule_id || '—'} · {evidencePages(diff, labels)}</small>
                    </div>
                    <div className="diff-source-row-meta">
                      <span>{valueText(values.aValue)}</span>
                      <span>{valueText(values.hValue)}</span>
                      <button type="button" className="ghost" onClick={() => setActiveDiff(diff)}>查看证据</button>
                    </div>
                  </article>
                )
              }) : <div className="diff-source-empty">暂无此类差异</div>}
            </div>
          </div>
        </div>
        ) : <EmptyState label="暂无差异" />}
      </div>
    </section>
  )
}

function BilingualPageReview({
  job,
  diffs,
  labels,
  setActiveDiff,
}: {
  job: JobDetail
  diffs: DiffItem[]
  labels: { a: string; h: string; factLabel: string }
  setActiveDiff: (diff: DiffItem) => void
}) {
  const summary = job.comparison_summary || {}
  const evidenceLocated = diffs.filter((diff) => (diff.evidence || []).length > 0).length
  const reviewRows = diffs.slice(0, 8)
  return (
    <div className="bilingual-page-review">
      <div className="bilingual-review-head">
        <div>
          <p className="eyebrow">Page-by-page Review</p>
          <h2>H 股中英文逐页核对</h2>
          <p>按 H 中文报告与 H 英文报告逐页定位差异和证据，突出文本、事实、披露项逐项复核链路。</p>
        </div>
        <span className="mode-chip">{reviewRows.length} 项</span>
      </div>

      <div className="bilingual-review-stats">
        <span><small>{labels.a}事实数</small><strong>{metric(summary, 'a_fact_count')}</strong></span>
        <span><small>{labels.h}事实数</small><strong>{metric(summary, 'h_fact_count')}</strong></span>
        <span><small>真实差异</small><strong>{metric(summary, 'real_diff_count')}</strong></span>
        <span><small>待复核</small><strong>{metric(summary, 'unresolved_diff_count')}</strong></span>
        <span><small>证据定位数</small><strong>{valueText(evidenceLocated)}</strong></span>
      </div>

      <div className="bilingual-page-list">
        <div className="bilingual-page-head">
          <span>#</span>
          <span>主题</span>
          <span>{labels.a}页码</span>
          <span>{labels.h}页码</span>
          <span>差异摘要</span>
          <span>规则 ID</span>
          <span>证据定位</span>
        </div>
        {reviewRows.length ? reviewRows.map((diff, index) => {
          const values = reviewValues(diff)
          const zhPages = evidencePagesForSides(diff, ['A', 'H_ZH', 'ZH', 'H_CN'], values.aPage)
          const enPages = evidencePagesForSides(diff, ['H', 'H_EN', 'EN'], values.hPage)
          return (
            <article className="bilingual-page-row" id={`diff-row-${diff.diff_id}`} key={diff.diff_id}>
              <span className="row-index">{index + 1}</span>
              <strong>{localized(diff.topic)}</strong>
              <span>{zhPages}</span>
              <span>{enPages}</span>
              <p>{diff.diff_explanation?.issue || localized(diff.summary)}</p>
              <small>{diff.rule_id || '—'}</small>
              <button type="button" className="ghost" onClick={() => setActiveDiff(diff)}>查看证据</button>
            </article>
          )
        }) : <EmptyState label="暂无逐页差异项" />}
      </div>
    </div>
  )
}

function DashboardMetric({
  label,
  value,
  note,
  tone,
}: {
  label: string
  value: string
  note: string
  tone?: 'accent' | 'critical' | 'warning' | 'teal'
}) {
  const animatedValue = useCountUpText(value)
  return (
    <div className={`dashboard-metric ${tone || ''}`}>
      <span>{label}</span>
      <strong>{animatedValue}</strong>
      <small>{note}</small>
    </div>
  )
}

function ProfileCard({ title, sideLabel, profile }: { title: string; sideLabel: string; profile?: ProfilePayload | null }) {
  const warnings = profileWarnings(profile)
  const metrics = profile?.metrics?.slice(0, 5) || []
  const narratives = profile?.narratives?.slice(0, 4) || []
  return (
    <article className="profile-card">
      <div className="profile-card-head">
        <div>
          <p className="eyebrow">{sideLabel}</p>
          <h2>{title}</h2>
          <p>{profile?.doc_id || '画像数据待生成'}</p>
        </div>
        <span>{profileCoverage(profile)}</span>
      </div>
      <div className="profile-stat-grid">
        <span><small>总页数</small><strong>{valueText(profile?.total_pages)}</strong></span>
        <span><small>扫描页数</small><strong>{profileScanText(profile)}</strong></span>
        <span><small>全量指标</small><strong>{valueText(profile?.metric_keys)}</strong></span>
        <span><small>事实出现</small><strong>{valueText(profile?.metric_occurrences)}</strong></span>
        <span><small>叙述块</small><strong>{valueText(profile?.narrative_blocks)}</strong></span>
        <span><small>结构节点</small><strong>{valueText(profile?.structure_nodes)}</strong></span>
        <span><small>提取预警</small><strong>{warnings.blocking} / {warnings.auxiliary}</strong></span>
      </div>
      <div className="profile-preview-table">
        <div className="profile-preview-head">
          <span>数值画像预览</span>
          <small>{metrics.length} 条</small>
        </div>
        {metrics.length ? metrics.map((item) => (
          <div className="profile-preview-row" key={`${sideLabel}-${item.canonical_key}-${item.page}`}>
            <strong>{metricDisplayName(item)}</strong>
            <span>{valueText(item.value_text || item.value)}</span>
            <small>p{valueText(item.page)} · {valueText(item.occurrence_count)} 次</small>
          </div>
        )) : <EmptyState label="暂无数值画像" />}
      </div>
      <div className="profile-preview-table narrative">
        <div className="profile-preview-head">
          <span>文字画像预览</span>
          <small>{narratives.length} 条</small>
        </div>
        {narratives.length ? narratives.map((item) => (
          <div className="profile-preview-row" key={`${sideLabel}-${item.topic_key}-${narrativePageRange(item)}`}>
            <strong>{item.topic_label || item.topic_key || '未分类叙述'}</strong>
            <span>{valueText(item.summary)}</span>
            <small>p{narrativePageRange(item)} · {valueText(item.word_count)} 字</small>
          </div>
        )) : <EmptyState label="暂无文字画像" />}
      </div>
    </article>
  )
}

function ProfilePage({
  session,
  draft,
  avatarFile,
  busy,
  avatarNode,
  setDraft,
  setAvatarFile,
  submitProfile,
  submitAvatar,
}: {
  session: SessionPayload | null
  draft: ProfileDraft
  avatarFile: File | null
  busy: string | null
  avatarNode: (className: string) => JSX.Element
  setDraft: (value: ProfileDraft | ((current: ProfileDraft) => ProfileDraft)) => void
  setAvatarFile: (file: File | null) => void
  submitProfile: (event: FormEvent<HTMLFormElement>) => void
  submitAvatar: (event: FormEvent<HTMLFormElement>) => void
}) {
  return (
    <section className="grid two-col profile-grid">
      <form className="panel" onSubmit={submitProfile}>
        <div className="panel-head">
          <div>
            <p className="eyebrow"><UserIcon size={12} />当前用户</p>
            <h2>{session?.user.display_name || 'Chu, Stanley'}</h2>
            <p className="panel-copy">更新当前演示用户的展示信息，导航栏会同步显示。</p>
          </div>
          {avatarNode('profile-avatar')}
        </div>
        <label>
          姓名
          <input
            value={draft.display_name}
            onChange={(event) => setDraft((current) => ({ ...current, display_name: event.target.value }))}
            maxLength={80}
          />
        </label>
        <label>
          所属部门
          <input
            value={draft.office_line}
            onChange={(event) => setDraft((current) => ({ ...current, office_line: event.target.value }))}
            maxLength={40}
          />
        </label>
        <label>
          职位角色
          <input
            value={draft.role_title}
            onChange={(event) => setDraft((current) => ({ ...current, role_title: event.target.value }))}
            placeholder="审计经理"
            maxLength={80}
          />
        </label>

        <button className="primary" type="submit" disabled={busy === 'profile'}>
          {busy === 'profile' ? <SpinnerIcon size={15} className="spin" /> : <CheckIcon size={15} />}
          {busy === 'profile' ? '正在保存' : '保存资料'}
        </button>
      </form>

      <form className="panel avatar-panel" onSubmit={submitAvatar}>
        <div className="panel-head">
          <div>
            <p className="eyebrow">头像</p>
            <h2>个人头像</h2>
            <p className="panel-copy">支持 PNG、JPG 或 WEBP，文件大小不超过 2MB。</p>
          </div>
        </div>
        <div className="avatar-stage">{avatarNode('stage-avatar')}</div>
        <label>
          头像文件
          <input
            type="file"
            accept="image/png,image/jpeg,image/webp"
            onChange={(event: ChangeEvent<HTMLInputElement>) => setAvatarFile(event.target.files?.[0] || null)}
          />
        </label>
        <p className="file-name">{avatarFile ? avatarFile.name : '请选择 PNG、JPG 或 WEBP 文件'}</p>
        <button className="primary" type="submit" disabled={busy === 'avatar'}>
          {busy === 'avatar' ? <SpinnerIcon size={15} className="spin" /> : <UploadIcon size={15} />}
          {busy === 'avatar' ? '正在上传' : '上传头像'}
        </button>
      </form>
    </section>
  )
}

function JobRow({ item, table = false }: { item: JobSummary; table?: boolean }) {
  const summary = item.comparison_summary || {}
  if (table) {
    const detailLabel = historyProgressLabel(item) || visualOcrStatusLabel(summary)
    return (
      <a className="history-row" href={`#/jobs/${item.job_id}`}>
        <div className="history-row-main">
          <strong>{item.company_name || item.job_id}</strong>
          {detailLabel ? <small>{detailLabel}</small> : null}
          <div className="history-row-meta-line">
            <span className="mode-chip">{modeLabel(item.check_mode)}</span>
            <span>{item.owner_display_name || 'Chu, Stanley'}</span>
          </div>
        </div>
        <span className={statusClass(item.status)}>{statusLabel(item.status)}</span>
        <span className="stat stat-diff">
          <small>真实差异</small>
          <strong>{metric(summary, 'real_diff_count')}</strong>
        </span>
        <span className="stat stat-time">
          <small>检查时间</small>
          <strong>{formatDate(item.started_at)}</strong>
        </span>
        <span className="stat stat-duration">
          <small>核查耗时</small>
          <strong>{formatDuration(item.duration_seconds)}</strong>
        </span>
      </a>
    )
  }
  return (
    <a className="job-row" href={`#/jobs/${item.job_id}`}>
      <span>
        <strong>{item.company_name || item.job_id}</strong>
        <small>{historyProgressLabel(item) || visualOcrStatusLabel(summary) || `${item.owner_display_name || 'Chu, Stanley'} · ${formatDate(item.started_at)}`}</small>
      </span>
      <span className="job-row-mode">
        <small>核查模式</small>
        <strong>{modeShortLabel(item.check_mode)}</strong>
      </span>
      <span className={statusClass(item.status)}>{statusLabel(item.status)}</span>
    </a>
  )
}

function EmptyState({
  label,
  ctaHref,
  ctaLabel,
}: {
  label: string
  ctaHref?: string
  ctaLabel?: string
}) {
  return (
    <div className="empty">
      <EmptyIllustration className="empty-illustration" />
      <p className="empty-label">{label}</p>
      {ctaHref && ctaLabel && (
        <a className="ghost empty-cta" href={ctaHref}>
          {ctaLabel}
          <ArrowRightIcon size={14} />
        </a>
      )}
    </div>
  )
}

function SkeletonBlock({ className }: { className?: string }) {
  return <div className={`skeleton-block ${className || ''}`} aria-hidden="true" />
}

function JobDetailLoading() {
  return (
    <section className="stack detail-dashboard job-detail-loading" aria-busy="true" aria-live="polite">
      <SkeletonBlock className="skeleton-conclusion" />
      <div className="detail-kpi-grid">
        {Array.from({ length: 6 }).map((_, index) => (
          <SkeletonBlock className="skeleton-kpi" key={index} />
        ))}
      </div>
      <p className="loading-caption">正在加载核查详情</p>
    </section>
  )
}

function EvidenceDialog({ diff, job, onClose }: { diff: DiffItem; job: JobDetail | null; onClose: () => void }) {
  const evidences = diff.evidence || []
  const explanation = diff.diff_explanation
  const items = explanation?.items || []
  const values = reviewValues(diff)
  const citations = diff.standard_reasoning?.citations || []
  const chart = diff.chart_cross
  const labels = job ? sideLabelsForJob(job) : { a: 'A 股', h: 'H 股', factLabel: '画像事实' }
  const scope = normalizedDiffScope(diff)
  const dialogRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    // Remember whatever triggered the dialog (the "查看证据" button) so focus can be
    // returned to it once the dialog closes, instead of leaving focus lost on <body>.
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null
    dialogRef.current?.focus()

    // Prevent the page underneath from scrolling while the full-screen dialog is open.
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)

    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previousOverflow
      previouslyFocused?.focus()
    }
  }, [onClose])
  // internal 差异（a_internal/h_internal）比较的是同一份报告内部的"可见值"与"底层原值"
  // （见 text_overlay_tamper.py / key_metric_tamper.py 对 a_value/h_value 的复用），
  // 不是两份不同报告的对比，表头文案与配色都要按此区分，避免误导为跨报告差异。
  const compareLeft = scope === 'h_internal'
    ? { label: `${labels.h} · 可见值`, side: 'h-side' as const }
    : scope === 'a_internal'
      ? { label: `${labels.a} · 可见值`, side: 'a-side' as const }
      : { label: labels.a, side: 'a-side' as const }
  const compareRight = scope === 'h_internal'
    ? { label: `${labels.h} · 底层原值`, side: 'h-side' as const }
    : scope === 'a_internal'
      ? { label: `${labels.a} · 底层原值`, side: 'a-side' as const }
      : { label: labels.h, side: 'h-side' as const }
  const valueChipLabel = scope === 'a_internal' || scope === 'h_internal' ? '可见值/底层原值' : `${labels.a}/${labels.h} 取值`
  return (
    <div className="review-overlay" role="presentation" onClick={onClose}>
      <section
        className="review-shell"
        role="dialog"
        aria-modal="true"
        aria-label="证据复核"
        tabIndex={-1}
        ref={dialogRef}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="review-header">
          <div>
            <p className="review-eyebrow">证据复核 · {triageLabel(diff.triage)}</p>
            <h2>{explanation?.headline || localized(diff.topic)}</h2>
            <p>{diff.diff_id} · {evidencePages(diff, labels)} · {evidences.length} 条证据</p>
          </div>
          <div className="review-actions">
            <button
              type="button"
              className="ghost"
              onClick={() => {
                const rowId = `diff-row-${diff.diff_id}`
                onClose()
                // Wait a tick for the dialog to unmount and body scroll to be restored
                // before scrolling, so this doesn't fight the overflow:hidden lock above.
                window.setTimeout(() => {
                  document.getElementById(rowId)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
                }, 0)
              }}
            >
              定位列表
            </button>
            <button type="button" className="ghost" onClick={onClose}><CloseIcon size={14} />关闭</button>
          </div>
        </header>

        <div className="review-summary-strip">
          <span className={`triage ${triageClass(diff.triage)}`}>{triageLabel(diff.triage)}</span>
          <span className={`severity ${diff.severity}`}>{severityLabel(diff.severity)}</span>
          <span className="type-chip">{diffTypeLabel(diff.diff_type)}</span>
          <span className="mode-chip">{valueChipLabel} {valueText(values.aValue)} / {valueText(values.hValue)}</span>
          <span className="mode-chip">差异率 {diffRatio(values.aValue, values.hValue)}</span>
        </div>

        <div className="review-grid">
          <aside className="review-panel">
            <div className="review-panel-head">
              <span>证据链</span>
              <span>{evidenceCountBySide(diff, labels)}</span>
            </div>
            <div className="review-chain">
              {evidences.length ? evidences.map((item, index) => (
                // Reuse labelSideForJob's H/H_EN/EN -> h-side, A/H_ZH/ZH/H_CN -> a-side
                // classification instead of a standalone `side === 'H'` check, which only
                // matched the exact literal "H" and mis-colored bilingual evidence (side
                // "H_EN"/"EN") as the A-side.
                <article
                  key={`${diff.diff_id}-${index}`}
                  className={`review-evidence-card ${labelSideForJob(item.side, labels) === labels.h ? 'h-side' : 'a-side'}`}
                >
                  <div className="review-evidence-top">
                    <span>{labelSideForJob(item.side, labels)}</span>
                    <strong>第 {item.page || '-'} 页</strong>
                  </div>
                  <small>{item.section || '章节待确认'} · bbox {bboxText(item.bbox)}</small>
                  <p>{item.snippet || '—'}</p>
                </article>
              )) : <EmptyState label="暂无证据片段" />}
            </div>
          </aside>

          <section className="review-panel review-focus">
            <div className="review-panel-head">对照视图</div>
            <div className="review-summary-card">
              <p>{explanation?.issue || localized(diff.summary)}</p>
              {explanation?.review_hint && <small>审阅提示：{explanation.review_hint}</small>}
            </div>

            <div className="review-compare">
              <div className={`review-value-card ${compareLeft.side}`}>
                <span>{compareLeft.label}</span>
                <strong>{valueText(values.aValue)}</strong>
                <small>{values.aPage ? `第 ${values.aPage} 页` : '页码待确认'}</small>
              </div>
              <div className={`review-value-card ${compareRight.side}`}>
                <span>{compareRight.label}</span>
                <strong>{valueText(values.hValue)}</strong>
                <small>{values.hPage ? `第 ${values.hPage} 页` : `差额 ${valueText(values.delta)}`}</small>
              </div>
            </div>

            {items.length > 0 && (
              <div className="review-explanation-grid">
                {items.map((item, index) => (
                  <article className="review-explanation-card" key={`${diff.diff_id}-item-${index}`}>
                    <span>{item.label || item.role || `差异项 ${index + 1}`}</span>
                    <strong>{valueText(item.a_value)} / {valueText(item.h_value)}</strong>
                    <small>
                      差额 {valueText(item.delta)} ·{' '}
                      {scope === 'a_internal' || scope === 'h_internal'
                        ? `p${item.a_page || item.h_page || '—'}`
                        : `A p${item.a_page || '—'} / H p${item.h_page || '—'}`}
                    </small>
                    {(item.a_snippet || item.h_snippet) && (
                      <p>{item.a_snippet || '—'}<br />{item.h_snippet || '—'}</p>
                    )}
                  </article>
                ))}
              </div>
            )}

            <div className="review-summary-card">
              <span>差异率</span>
              <strong>{diffRatio(values.aValue, values.hValue)}</strong>
              <small>容差 {valueText(diff.tolerance)} · 差额 {valueText(values.delta)}</small>
            </div>
          </section>

          <aside className="review-panel meta-panel">
            <div className="review-panel-head">元数据</div>
            <div className="review-meta">
              <div className="review-meta-item"><span>Diff ID</span><strong>{diff.diff_id}</strong></div>
              <div className="review-meta-item"><span>规则 ID</span><strong>{diff.rule_id || '—'}</strong></div>
              <div className="review-meta-item"><span>审阅状态</span><strong>{diff.review_status || 'pending'}</strong></div>
              <div className="review-meta-item"><span>审阅提示</span><strong>{explanation?.review_hint || '—'}</strong></div>
              <div className="review-meta-item"><span>位置</span><strong>{explanation?.location || evidencePages(diff, labels)}</strong></div>
              <div className="review-meta-item"><span>类型</span><strong>{diffTypeLabel(diff.diff_type)}</strong></div>
            </div>

            {diff.standard_reasoning && (
              <div className="review-insight">
                <span>准则推理</span>
                <strong>{diff.standard_reasoning.expected ? '符合预期差异' : '不符合预期差异'}</strong>
                <p>{diff.standard_reasoning.rationale || '—'}</p>
                <small>置信度 {valueText(typeof diff.standard_reasoning.confidence === 'number' ? `${Math.round(diff.standard_reasoning.confidence * 100)}%` : null)}</small>
                {citations.length > 0 && (
                  <div className="review-citations">
                    <span>引用条款</span>
                    {citations.map((citation, index) => (
                      <p key={`${diff.diff_id}-citation-${index}`}>
                        <strong>{citation.standard_code || '标准'}</strong> · {[citation.clause, citation.title].filter(Boolean).join(' · ') || '—'}
                      </p>
                    ))}
                  </div>
                )}
              </div>
            )}

            {chart && (
              <div className="review-insight">
                <span>图表校核</span>
                <div className="review-meta">
                  <div className="review-meta-item"><span>图表值</span><strong>{valueText(chart.chart_value)}</strong></div>
                  <div className="review-meta-item"><span>表格值</span><strong>{valueText(chart.table_value)}</strong></div>
                  <div className="review-meta-item"><span>文本值</span><strong>{valueText(chart.text_value)}</strong></div>
                  <div className="review-meta-item"><span>不一致数</span><strong>{valueText(chart.inconsistency_count)}</strong></div>
                </div>
              </div>
            )}
          </aside>
        </div>
      </section>
    </div>
  )
}

export default App
