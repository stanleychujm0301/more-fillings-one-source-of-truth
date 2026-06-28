import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'
import kpmgLogo from './assets/kpmg-logo.svg'
import './App.css'

type Route = {
  page: 'cockpit' | 'history' | 'job' | 'profile'
  jobId?: string
}

type ProjectGroup = {
  id: string
  name: string
}

type CurrentUser = {
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
}

type JobSummary = {
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

type ProfileMetricPreview = {
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

type ProfileNarrativePreview = {
  topic_key?: string | null
  topic_label?: string | null
  page_range?: [number, number] | null
  word_count?: number | null
  detail_level?: string | null
  summary?: string | null
}

type ProfilePayload = {
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

type DiffItem = {
  diff_id: string
  diff_type: string
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

type JobDetail = JobSummary & {
  a_file?: string
  h_file?: string
  error?: string | null
  profile_a?: ProfilePayload | null
  profile_h?: ProfilePayload | null
  coverage_items?: unknown[]
  diffs?: DiffItem[]
}

type ProfileDraft = {
  display_name: string
  office_line: string
  role_title: string
}

type UploadState = {
  companyName: string
  checkMode: 'ah' | 'h_bilingual'
  bilingualLevel: 'fast' | 'strict'
  aFile: File | null
  hFile: File | null
}

type UploadErrors = {
  companyName?: string
  aFile?: string
  hFile?: string
}

type UploadErrorField = keyof UploadErrors

const DEFAULT_USER_LABEL = 'Chu, Stanley (SH/FS3)'
const EMPTY_UPLOAD: UploadState = {
  companyName: '',
  checkMode: 'ah',
  bilingualLevel: 'fast',
  aFile: null,
  hFile: null,
}

function parseRoute(): Route {
  const hash = window.location.hash || '#/cockpit'
  if (hash.startsWith('#/jobs/')) {
    return { page: 'job', jobId: decodeURIComponent(hash.slice('#/jobs/'.length)) }
  }
  if (hash === '#/history') return { page: 'history' }
  if (hash === '#/profile') return { page: 'profile' }
  return { page: 'cockpit' }
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
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

function textValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '-'
  return String(value)
}

function localized(value?: { zh?: string | null; en?: string | null }): string {
  return value?.zh || value?.en || '-'
}

function formatDate(value?: string | null): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function metric(summary: Record<string, unknown> | undefined, key: string): string {
  return textValue(summary?.[key])
}

function initials(user?: CurrentUser | null): string {
  const name = user?.display_name || 'Chu Stanley'
  const parts = name.replace(',', ' ').split(/\s+/).filter(Boolean)
  return parts.slice(0, 2).map((part) => part[0]?.toUpperCase()).join('') || 'CS'
}

function statusClass(status: string): string {
  if (status === 'done') return 'status done'
  if (status === 'failed') return 'status failed'
  return 'status running'
}

function statusLabel(status: string): string {
  if (status === 'done') return '已完成'
  if (status === 'failed') return '失败'
  return '进行中'
}

function modeLabel(mode: JobSummary['check_mode'] | UploadState['checkMode']): string {
  return mode === 'h_bilingual' ? 'H 股中英文核查' : 'A/H 股报告核查'
}

function modeShortLabel(mode: JobSummary['check_mode'] | UploadState['checkMode']): string {
  return mode === 'h_bilingual' ? 'H 中英' : 'A/H'
}

function uploadRequiredFileMessages(mode: UploadState['checkMode']): { aFile: string; hFile: string } {
  if (mode === 'h_bilingual') {
    return {
      aFile: '请上传 H 股中文报告 PDF',
      hFile: '请上传 H 股英文报告 PDF',
    }
  }
  return {
    aFile: '请上传 A 股报告 PDF',
    hFile: '请上传 H 股报告 PDF',
  }
}

function validateUpload(upload: UploadState): UploadErrors {
  const fileMessages = uploadRequiredFileMessages(upload.checkMode)
  const errors: UploadErrors = {}
  if (!upload.companyName.trim()) errors.companyName = '请输入项目名称'
  if (!upload.aFile) errors.aFile = fileMessages.aFile
  if (!upload.hFile) errors.hFile = fileMessages.hFile
  return errors
}

function firstUploadErrorField(errors: UploadErrors): UploadErrorField | null {
  if (errors.companyName) return 'companyName'
  if (errors.aFile) return 'aFile'
  if (errors.hFile) return 'hFile'
  return null
}

function severityLabel(severity: string): string {
  if (severity === 'critical') return '重大'
  if (severity === 'high') return '高'
  if (severity === 'medium') return '中'
  if (severity === 'low') return '低'
  if (severity === 'info') return '提示'
  return severity || '待定'
}

function triageLabel(triage?: string | null): string {
  if (triage === 'expected') return '预期差异'
  if (triage === 'unresolved') return '待复核'
  return '真实差异'
}

function triageClass(triage?: string | null): string {
  if (triage === 'expected') return 'expected'
  if (triage === 'unresolved') return 'unresolved'
  return 'real'
}

function diffTypeLabel(type?: string | null): string {
  const labels: Record<string, string> = {
    numeric: '数值差异',
    cross_check: '勾稽差异',
    standard: '准则差异',
    disclosure: '披露差异',
    chart: '图表校核',
    internal: '内部一致性',
  }
  return labels[type || ''] || type || '差异'
}

function sideLabel(side?: string | null): string {
  if (side === 'A') return 'A 股'
  if (side === 'H') return 'H 股'
  if (side === 'H_ZH') return 'H 股中文'
  if (side === 'H_EN') return 'H 股英文'
  return side || '证据'
}

function valueText(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return '—'
    return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 4 }).format(value)
  }
  return String(value)
}

function numericValue(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string') {
    const parsed = Number(value.replace(/,/g, ''))
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function diffRatio(aValue: unknown, hValue: unknown): string {
  const a = numericValue(aValue)
  const h = numericValue(hValue)
  if (a === null || h === null || a === 0) return '—'
  return `${(Math.abs((h - a) / Math.abs(a)) * 100).toFixed(2)}%`
}

function bboxText(bbox?: [number, number, number, number] | null): string {
  if (!bbox?.length) return '无坐标'
  return bbox.map((value) => Number(value).toFixed(1)).join(', ')
}

function evidencePages(diff: DiffItem): string {
  const pages = (diff.evidence || [])
    .filter((item) => item.page)
    .map((item) => `${sideLabel(item.side)} p${item.page}`)
  return pages.length ? pages.join(' · ') : '—'
}

function evidencePagesForSides(diff: DiffItem, sides: string[], fallbackPage?: number | null): string {
  const sideSet = new Set(sides)
  const pages = new Set<number>()
  ;(diff.evidence || []).forEach((item) => {
    if (item.page && sideSet.has(item.side || '')) pages.add(item.page)
  })
  if (!pages.size && fallbackPage) pages.add(fallbackPage)
  return pages.size ? Array.from(pages).sort((a, b) => a - b).map((page) => `p${page}`).join(' / ') : '—'
}

function evidenceCountBySide(diff: DiffItem): string {
  const counts = (diff.evidence || []).reduce<Record<string, number>>((acc, item) => {
    const key = sideLabel(item.side)
    acc[key] = (acc[key] || 0) + 1
    return acc
  }, {})
  const parts = Object.entries(counts).map(([side, count]) => `${side} ${count}`)
  return parts.length ? parts.join(' / ') : 'A 0 / H 0'
}

function reviewValues(diff: DiffItem) {
  const primary = diff.diff_explanation?.items?.[0]
  return {
    aValue: primary?.a_value ?? diff.a_value,
    hValue: primary?.h_value ?? diff.h_value,
    delta: primary?.delta ?? diff.delta,
    aPage: primary?.a_page ?? null,
    hPage: primary?.h_page ?? null,
    aSnippet: primary?.a_snippet ?? '',
    hSnippet: primary?.h_snippet ?? '',
  }
}

type DiffScope = 'all' | 'real' | 'unresolved' | 'expected' | 'coverage'

function summaryNumber(summary: Record<string, unknown>, key: string): number {
  return numericValue(summary[key]) ?? 0
}

function formatDuration(seconds?: number | null): string {
  if (!seconds || !Number.isFinite(seconds)) return '—'
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  return minutes ? `${minutes}分${rest}秒` : `${rest}秒`
}

function sideLabelsForJob(job: JobDetail): { a: string; h: string; factLabel: string } {
  const rawLabels = job.comparison_summary?.side_labels
  const labels = rawLabels && typeof rawLabels === 'object' ? rawLabels as Record<string, unknown> : {}
  const a = String(labels.A || (job.check_mode === 'h_bilingual' ? 'H中文' : 'A 股'))
  const h = String(labels.H || (job.check_mode === 'h_bilingual' ? 'H英文' : 'H 股'))
  return {
    a,
    h,
    factLabel: job.check_mode === 'h_bilingual' ? '文本事实' : '画像事实',
  }
}

function profileWarnings(profile?: ProfilePayload | null) {
  const audit = profile?.extraction_audit
  const details = audit?.engines?.warning_details || []
  const flagCount = (audit?.warning_flags?.length || 0) + (profile?.warning_flags?.length || 0)
  const messageCount = (audit?.warnings?.length || 0) + (profile?.warnings?.length || 0)
  const blocking = details.filter((item) => item?.blocking).length
  const auxiliary = details.filter((item) => item?.category === 'auxiliary_chart').length
  const total = Math.max(details.length, flagCount, messageCount)
  return { blocking, auxiliary, total }
}

function profileScanText(profile?: ProfilePayload | null): string {
  const audit = profile?.extraction_audit
  const scanned = audit?.scanned_pages?.length || 0
  const total = audit?.total_pages || profile?.total_pages || 0
  if (!scanned && !total) return '—'
  return `${scanned || total}/${total || '—'}`
}

function profileCoverage(profile?: ProfilePayload | null): string {
  const audit = profile?.extraction_audit
  const ratio = numericValue(audit?.coverage_ratio)
  if (ratio !== null) return `${(ratio * 100).toFixed(1)}%`
  const scanned = audit?.scanned_pages?.length || 0
  const total = audit?.total_pages || profile?.total_pages || 0
  return scanned && total ? `${((scanned / total) * 100).toFixed(1)}%` : '—'
}

function auditConclusion(job: JobDetail, diffs: DiffItem[]) {
  const summary = job.comparison_summary || {}
  const real = summaryNumber(summary, 'real_diff_count')
  const unresolved = summaryNumber(summary, 'unresolved_diff_count')
  const blocking = summaryNumber(summary, 'blocking_warning_count') || summaryNumber(summary, 'core_warning_count')
  const auxiliary = summaryNumber(summary, 'aux_warning_count')
  const evidenceItems = diffs.filter((diff) => (diff.evidence || []).length > 0).length
  if (job.status === 'failed') {
    return {
      tone: 'failed',
      title: '核查任务失败，需重新生成结果',
      copy: job.error || '任务未能完成，请检查上传文件或后端日志。',
      pill: '红色 · 失败',
      evidenceItems,
      blocking,
      auxiliary,
    }
  }
  if (real || blocking || unresolved) {
    return {
      tone: 'risk',
      title: '发现真实差异或核心提取预警',
      copy: `真实差异 ${real} 条，待人工复核 ${unresolved} 条，核心提取预警 ${blocking} 条，辅助提示 ${auxiliary} 条，建议优先进入证据审阅。`,
      pill: real ? '红色 · 需复核' : '琥珀 · 需关注',
      evidenceItems,
      blocking,
      auxiliary,
    }
  }
  return {
    tone: 'clean',
    title: '未发现真实差异或核心提取预警',
    copy: 'A/H 画像、披露覆盖和证据定位已生成，可继续查看画像或抽样复核证据。',
    pill: '绿色 · 已完成',
    evidenceItems,
    blocking,
    auxiliary,
  }
}

function filteredDiffs(diffs: DiffItem[], scope: DiffScope): DiffItem[] {
  if (scope === 'all' || scope === 'coverage') return diffs
  return diffs.filter((diff) => (diff.triage || 'real') === scope)
}

function diffScopeCount(diffs: DiffItem[], scope: DiffScope, coverageCount: number): number {
  if (scope === 'all') return diffs.length
  if (scope === 'coverage') return coverageCount
  return filteredDiffs(diffs, scope).length
}

function metricDisplayName(metric: ProfileMetricPreview): string {
  return metric.name?.zh || metric.name?.en || metric.canonical_key || '—'
}

function narrativePageRange(item: ProfileNarrativePreview): string {
  return item.page_range?.length ? `${item.page_range[0]}-${item.page_range[1]}` : '—'
}

function App() {
  const [route, setRoute] = useState<Route>(() => parseRoute())
  const [session, setSession] = useState<SessionPayload | null>(null)
  const [health, setHealth] = useState<HealthPayload | null>(null)
  const [history, setHistory] = useState<JobSummary[]>([])
  const [historyScope, setHistoryScope] = useState<'project' | 'mine'>('project')
  const [job, setJob] = useState<JobDetail | null>(null)
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
    const payload = await fetchJson<JobDetail>(`/api/jobs/${encodeURIComponent(jobId)}`)
    setJob(payload)
  }, [])

  useEffect(() => {
    if (!window.location.hash) {
      window.location.hash = '#/cockpit'
    }
    const onHashChange = () => setRoute(parseRoute())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  useEffect(() => {
    loadSession().catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
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

  useEffect(() => {
    if (route.page === 'job' && route.jobId) {
      setJob(null)
      loadJob(route.jobId).catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
    }
  }, [loadJob, route])

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
    const errors = validateUpload(upload)
    if (Object.keys(errors).length) {
      showUploadErrors(errors)
      setError(null)
      setMessage(null)
      return
    }
    clearValidationTimeout()
    setBusy('job')
    setError(null)
    setMessage(null)
    setUploadErrors({})
    const form = new FormData()
    form.append('company_name', upload.companyName.trim())
    form.append('check_mode', upload.checkMode)
    form.append('bilingual_level', upload.bilingualLevel)
    form.append('a_file', upload.aFile)
    form.append('h_file', upload.hFile)
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
            <strong>MORE FILLINGS, ONE SOURCE OF TRUTH</strong>
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
          <span id="statusBadge" className="api-status">{session ? 'API 已连接' : '正在连接'}</span>
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
            <button type="button" onClick={() => { setError(null); setMessage(null) }}>x</button>
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
          <JobDetailPage job={job} setActiveDiff={setActiveDiff} />
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

      {activeDiff && <EvidenceDialog diff={activeDiff} onClose={() => setActiveDiff(null)} />}
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
        <span className="job-report-action-link disabled" aria-disabled="true">下载 Excel</span>
        <span className="job-report-action-link disabled" aria-disabled="true">下载 PDF</span>
        <a className="job-report-action-link" href="#/history">返回项目历史</a>
      </div>
    )
  }
  return (
    <div className="job-report-actions" aria-label="报告操作">
      <a className="job-report-action-link" href={`/api/jobs/${job.job_id}/report.xlsx?template=latest`}>下载 Excel</a>
      <a className="job-report-action-link" href={`/api/jobs/${job.job_id}/report.pdf?template=latest`}>下载 PDF</a>
      <a className="job-report-action-link" href="#/history">返回项目历史</a>
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
  const latest = history.slice(0, 5)
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
          <div className="file-row">
            <label className={`file-card ${invalidClass('aFile')}`}>
              <span className="file-kicker">PDF</span>
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
              <span className="file-kicker">PDF</span>
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
            {busy === 'job' ? '正在生成核查任务' : '开始核查'}
          </button>
        </form>

        <aside className="command-history">
          <div className="command-section-head">
            <span>02</span>
            <div>
              <h3>项目组最近核查</h3>
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
      <div className="history-table">
        <div className="history-head">
          <span>核查任务</span>
          <span>核查模式</span>
          <span>提交人</span>
          <span>状态</span>
          <span>真实差异</span>
          <span>检查时间</span>
          <span>核查耗时</span>
        </div>
        {history.length ? history.map((item) => <JobRow key={item.job_id} item={item} table />) : <EmptyState label="暂无项目历史" />}
      </div>
    </section>
  )
}

function JobDetailPage({
  job,
  setActiveDiff,
}: {
  job: JobDetail | null
  setActiveDiff: (diff: DiffItem) => void
}) {
  const [diffScope, setDiffScope] = useState<DiffScope>('all')
  if (!job) return <EmptyState label="正在加载核查详情" />
  const summary = job.comparison_summary || {}
  const diffs = job.diffs || []
  const labels = sideLabelsForJob(job)
  const conclusion = auditConclusion(job, diffs)
  const coverageCount = summaryNumber(summary, 'coverage_count') || job.coverage_items?.length || 0
  const visibleDiffs = filteredDiffs(diffs, diffScope)
  const coverageItems = (job.coverage_items || []) as Array<Record<string, unknown>>
  const diffScopes: Array<{ key: DiffScope; label: string }> = [
    { key: 'all', label: '全部' },
    { key: 'real', label: '真实差异' },
    { key: 'unresolved', label: '待人工复核' },
    { key: 'expected', label: '预期差异' },
    { key: 'coverage', label: '披露覆盖' },
  ]
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
          <div className="audit-conclusion-chips">
            <span>{labels.a}事实 <strong>{metric(summary, 'a_fact_count')}</strong></span>
            <span>{labels.h}事实 <strong>{metric(summary, 'h_fact_count')}</strong></span>
            <span>披露覆盖 <strong>{valueText(coverageCount)}</strong></span>
            <span>跨页事件 <strong>{metric(summary, 'matched_event_count')}</strong></span>
            <span>核心预警 <strong>{valueText(conclusion.blocking)}</strong></span>
            <span>辅助提示 <strong>{valueText(conclusion.auxiliary)}</strong></span>
            <span>证据定位 <strong>{valueText(conclusion.evidenceItems)}</strong></span>
          </div>
        </div>
        <div className="audit-conclusion-side">
          <span className={`audit-pill ${conclusion.tone}`}>{conclusion.pill}</span>
          <span className={statusClass(job.status)}>{statusLabel(job.status)}</span>
        </div>
      </div>

      <div className="detail-kpi-grid">
        <DashboardMetric
          tone="accent"
          label={labels.factLabel}
          value={`${metric(summary, 'a_fact_count')} / ${metric(summary, 'h_fact_count')}`}
          note={`${labels.a}/${labels.h} · Key ${metric(summary, 'a_metric_keys')} / ${metric(summary, 'h_metric_keys')}`}
        />
        <DashboardMetric
          tone={summaryNumber(summary, 'real_diff_count') ? 'critical' : undefined}
          label="差异"
          value={`${metric(summary, 'real_diff_count')} / ${metric(summary, 'expected_diff_count')}`}
          note={`真实 / 预期 · 内部 ${metric(summary, 'internal_inconsistency_count')}`}
        />
        <DashboardMetric
          tone={summaryNumber(summary, 'unresolved_diff_count') ? 'warning' : undefined}
          label="待人工复核"
          value={metric(summary, 'unresolved_diff_count')}
          note="未决差异 · 需人工判定"
        />
        <DashboardMetric
          tone="accent"
          label="披露覆盖"
          value={valueText(coverageCount)}
          note={`跨页 ${metric(summary, 'matched_event_count')} · 单边 ${labels.a} ${metric(summary, 'a_only_count')} / ${labels.h} ${metric(summary, 'h_only_count')}`}
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
          coverageCount={coverageCount}
          labels={labels}
          setActiveDiff={setActiveDiff}
        />
      )}

      <div className="panel diff-review-panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">复核队列</p>
            <h2>差异与证据</h2>
            <p className="panel-copy">按分流、严重性、主题、页码和规则 ID 展示，点击单项进入全屏证据审阅。</p>
          </div>
          <span className="mode-chip">{diffScope === 'coverage' ? coverageCount : visibleDiffs.length} 项</span>
        </div>
        <div className="diff-scope-rail" aria-label="差异分流筛选">
          {diffScopes.map((item) => (
            <button
              key={item.key}
              type="button"
              className={diffScope === item.key ? 'selected' : ''}
              onClick={() => setDiffScope(item.key)}
            >
              {item.label} <span>{diffScopeCount(diffs, item.key, coverageCount)}</span>
            </button>
          ))}
        </div>
        {diffScope === 'coverage' ? (
          <div className="coverage-review-list">
            {coverageItems.length ? coverageItems.slice(0, 10).map((item, index) => (
              <article className="coverage-review-row" key={String(item.coverage_id || index)}>
                <span>{index + 1}</span>
                <strong>{valueText(item.category)} · {valueText(item.status)}</strong>
                <p>{valueText(item.topic || item.title || item.note || item.coverage_id)}</p>
                <small>A {valueText(item.a_pages)} / H {valueText(item.h_pages)}</small>
              </article>
            )) : <EmptyState label="暂无披露覆盖项" />}
          </div>
        ) : (
          <div className="diff-command-table">
            <div className="diff-command-head">
              <span>#</span>
              <span>分流</span>
              <span>严重性</span>
              <span>类型</span>
              <span>差异说明</span>
              <span>主题</span>
              <span>{labels.a}取值</span>
              <span>{labels.h}取值</span>
              <span>页码</span>
              <span>审阅</span>
            </div>
            {visibleDiffs.length ? visibleDiffs.map((diff, index) => {
              const values = reviewValues(diff)
              return (
                <article className="diff-command-row" key={diff.diff_id}>
                  <span className="row-index">{index + 1}</span>
                  <span className={`triage ${triageClass(diff.triage)}`}>{triageLabel(diff.triage)}</span>
                  <span className={`severity ${diff.severity}`}>{severityLabel(diff.severity)}</span>
                  <span className="type-chip">{diffTypeLabel(diff.diff_type)}</span>
                  <div className="diff-command-copy">
                    <strong>{diff.diff_explanation?.headline || localized(diff.topic)}</strong>
                    <p>{diff.diff_explanation?.issue || localized(diff.summary)}</p>
                    <small>规则 ID {diff.rule_id || '—'} · 证据 {diff.evidence?.length || 0} 条</small>
                  </div>
                  <strong className="diff-command-topic">{localized(diff.topic)}</strong>
                  <span className="diff-command-value">{valueText(values.aValue)}</span>
                  <span className="diff-command-value">{valueText(values.hValue)}</span>
                  <span className="diff-command-pages">{evidencePages(diff)}</span>
                  <button type="button" className="ghost" onClick={() => setActiveDiff(diff)}>查看证据</button>
                </article>
              )
            }) : <EmptyState label="暂无差异" />}
          </div>
        )}
      </div>
    </section>
  )
}

function BilingualPageReview({
  job,
  diffs,
  coverageCount,
  labels,
  setActiveDiff,
}: {
  job: JobDetail
  diffs: DiffItem[]
  coverageCount: number
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
        <span className="mode-chip">{reviewRows.length || coverageCount} 项</span>
      </div>

      <div className="bilingual-review-stats">
        <span><small>{labels.a}事实数</small><strong>{metric(summary, 'a_fact_count')}</strong></span>
        <span><small>{labels.h}事实数</small><strong>{metric(summary, 'h_fact_count')}</strong></span>
        <span><small>披露/文本覆盖项</small><strong>{valueText(coverageCount)}</strong></span>
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
            <article className="bilingual-page-row" key={diff.diff_id}>
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
  return (
    <div className={`dashboard-metric ${tone || ''}`}>
      <span>{label}</span>
      <strong>{value}</strong>
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
        <span><small>指标 Key</small><strong>{valueText(profile?.metric_keys)}</strong></span>
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
            <p className="eyebrow">当前用户</p>
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
          {busy === 'avatar' ? '正在上传' : '上传头像'}
        </button>
      </form>
    </section>
  )
}

function JobRow({ item, table = false }: { item: JobSummary; table?: boolean }) {
  const summary = item.comparison_summary || {}
  if (table) {
    return (
      <a className="history-row" href={`#/jobs/${item.job_id}`}>
        <span>
          <strong>{item.company_name || item.job_id}</strong>
        </span>
        <span>{modeLabel(item.check_mode)}</span>
        <span>{item.owner_display_name || 'Chu, Stanley'}</span>
        <span className={statusClass(item.status)}>{statusLabel(item.status)}</span>
        <span>{metric(summary, 'real_diff_count')}</span>
        <span>{formatDate(item.started_at)}</span>
        <span>{formatDuration(item.duration_seconds)}</span>
      </a>
    )
  }
  return (
    <a className="job-row" href={`#/jobs/${item.job_id}`}>
      <span>
        <strong>{item.company_name || item.job_id}</strong>
        <small>{item.owner_display_name || 'Chu, Stanley'} · {formatDate(item.started_at)}</small>
      </span>
      <span className="job-row-mode">
        <small>核查模式</small>
        <strong>{modeShortLabel(item.check_mode)}</strong>
      </span>
      <span className={statusClass(item.status)}>{statusLabel(item.status)}</span>
    </a>
  )
}

function EmptyState({ label }: { label: string }) {
  return <div className="empty">{label}</div>
}

function EvidenceDialog({ diff, onClose }: { diff: DiffItem; onClose: () => void }) {
  const evidences = diff.evidence || []
  const explanation = diff.diff_explanation
  const items = explanation?.items || []
  const values = reviewValues(diff)
  const citations = diff.standard_reasoning?.citations || []
  const chart = diff.chart_cross
  return (
    <div className="review-overlay" role="presentation" onClick={onClose}>
      <section className="review-shell" role="dialog" aria-modal="true" aria-label="证据复核" onClick={(event) => event.stopPropagation()}>
        <header className="review-header">
          <div>
            <p className="review-eyebrow">证据复核 · {triageLabel(diff.triage)}</p>
            <h2>{explanation?.headline || localized(diff.topic)}</h2>
            <p>{diff.diff_id} · {evidencePages(diff)} · {evidences.length} 条证据</p>
          </div>
          <div className="review-actions">
            <button type="button" className="ghost" onClick={onClose}>定位列表</button>
            <button type="button" className="ghost" onClick={onClose}>关闭</button>
          </div>
        </header>

        <div className="review-summary-strip">
          <span className={`triage ${triageClass(diff.triage)}`}>{triageLabel(diff.triage)}</span>
          <span className={`severity ${diff.severity}`}>{severityLabel(diff.severity)}</span>
          <span className="type-chip">{diffTypeLabel(diff.diff_type)}</span>
          <span className="mode-chip">A/H 取值 {valueText(values.aValue)} / {valueText(values.hValue)}</span>
          <span className="mode-chip">差异率 {diffRatio(values.aValue, values.hValue)}</span>
        </div>

        <div className="review-grid">
          <aside className="review-panel">
            <div className="review-panel-head">
              <span>证据链</span>
              <span>{evidenceCountBySide(diff)}</span>
            </div>
            <div className="review-chain">
              {evidences.length ? evidences.map((item, index) => (
                <article key={`${diff.diff_id}-${index}`} className={`review-evidence-card ${item.side === 'H' ? 'h-side' : 'a-side'}`}>
                  <div className="review-evidence-top">
                    <span>{sideLabel(item.side)}</span>
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
              <div className="review-value-card a-side">
                <span>A 股</span>
                <strong>{valueText(values.aValue)}</strong>
                <small>{values.aPage ? `第 ${values.aPage} 页` : '页码待确认'}</small>
              </div>
              <div className="review-value-card h-side">
                <span>H 股</span>
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
                    <small>差额 {valueText(item.delta)} · A p{item.a_page || '—'} / H p{item.h_page || '—'}</small>
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
              <div className="review-meta-item"><span>位置</span><strong>{explanation?.location || evidencePages(diff)}</strong></div>
              <div className="review-meta-item"><span>类型</span><strong>{diffTypeLabel(diff.diff_type)}</strong></div>
            </div>

            {diff.standard_reasoning && (
              <div className="review-insight">
                <span>准则推理</span>
                <strong>{diff.standard_reasoning.expected ? '符合预期差异' : '不符合预期差异'}</strong>
                <p>{diff.standard_reasoning.rationale || '—'}</p>
                <small>置信度 {valueText(diff.standard_reasoning.confidence ? `${Math.round(diff.standard_reasoning.confidence * 100)}%` : null)}</small>
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
