import type {
  CurrentUser,
  DiffItem,
  JobDetail,
  JobProgressPayload,
  JobSummary,
  ProfileMetricPreview,
  ProfileNarrativePreview,
  ProfilePayload,
  UploadErrorField,
  UploadErrors,
  UploadState,
} from './App'

export const JOB_REFRESH_STATUSES = new Set(['pending', 'parsing', 'profiling', 'checking', 'reporting'])

// Backend timestamps are naive UTC ISO strings with no timezone suffix
// (e.g. "2026-07-05T03:53:00"). Matches a trailing "Z" or a numeric UTC
// offset such as "+08:00" / "-0800".
const TIMEZONE_SUFFIX_RE = /(?:Z|[+-]\d{2}:?\d{2})$/

export function textValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '-'
  return String(value)
}

export function localized(value?: { zh?: string | null; en?: string | null }): string {
  return value?.zh || value?.en || '-'
}

export function formatDate(value?: string | null): string {
  if (!value) return '-'
  // `new Date(...)` parses a timezone-less string using the *browser's local*
  // timezone rather than UTC, which silently shifts every displayed
  // timestamp by the browser's UTC offset (e.g. 8 hours early for China
  // Standard Time). Force UTC parsing by appending "Z" when the backend
  // string doesn't already carry a timezone marker.
  const normalized = TIMEZONE_SUFFIX_RE.test(value) ? value : `${value}Z`
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

export function metric(summary: Record<string, unknown> | undefined, key: string): string {
  return textValue(summary?.[key])
}

export function initials(user?: CurrentUser | null): string {
  const name = user?.display_name || 'Chu Stanley'
  const parts = name.replace(',', ' ').split(/\s+/).filter(Boolean)
  return parts.slice(0, 2).map((part) => part[0]?.toUpperCase()).join('') || 'CS'
}

export function statusClass(status: string): string {
  if (status === 'done') return 'status done'
  if (status === 'failed') return 'status failed'
  return 'status running'
}

export function statusLabel(status: string): string {
  if (status === 'done') return '已完成'
  if (status === 'failed') return '失败'
  return '进行中'
}

export function modeLabel(mode: JobSummary['check_mode'] | UploadState['checkMode']): string {
  return mode === 'h_bilingual' ? 'H 股中英文核查' : 'A/H 股报告核查'
}

export function modeShortLabel(mode: JobSummary['check_mode'] | UploadState['checkMode']): string {
  return mode === 'h_bilingual' ? 'H 中英' : 'A/H'
}

export function uploadRequiredFileMessages(mode: UploadState['checkMode']): { aFile: string; hFile: string } {
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

export function validateUpload(upload: UploadState): UploadErrors {
  const fileMessages = uploadRequiredFileMessages(upload.checkMode)
  const errors: UploadErrors = {}
  if (!upload.companyName.trim()) errors.companyName = '请输入项目名称'
  if (!upload.aFile) errors.aFile = fileMessages.aFile
  if (!upload.hFile) errors.hFile = fileMessages.hFile
  return errors
}

export function firstUploadErrorField(errors: UploadErrors): UploadErrorField | null {
  if (errors.companyName) return 'companyName'
  if (errors.aFile) return 'aFile'
  if (errors.hFile) return 'hFile'
  return null
}

export function severityLabel(severity: string): string {
  if (severity === 'critical') return '重大'
  if (severity === 'high') return '高'
  if (severity === 'medium') return '中'
  if (severity === 'low') return '低'
  if (severity === 'info') return '提示'
  return severity || '待定'
}

export function triageLabel(triage?: string | null): string {
  if (triage === 'expected') return '预期差异'
  if (triage === 'unresolved') return '待复核'
  return '真实差异'
}

export function triageClass(triage?: string | null): string {
  if (triage === 'expected') return 'expected'
  if (triage === 'unresolved') return 'unresolved'
  return 'real'
}

export function diffTypeLabel(type?: string | null): string {
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

export function sideLabel(side?: string | null): string {
  if (side === 'A') return 'A 股'
  if (side === 'H') return 'H 股'
  if (side === 'H_ZH') return 'H 股中文'
  if (side === 'H_EN') return 'H 股英文'
  return side || '证据'
}

export function labelSideForJob(side: string | null | undefined, labels: SideDisplayLabels): string {
  if (side === 'A' || side === 'H_ZH' || side === 'ZH' || side === 'H_CN') return labels.a
  if (side === 'H' || side === 'H_EN' || side === 'EN') return labels.h
  return sideLabel(side)
}

export function valueText(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return '—'
    return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 4 }).format(value)
  }
  return String(value)
}

export function numericValue(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string') {
    const parsed = Number(value.replace(/,/g, ''))
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

export function diffRatio(aValue: unknown, hValue: unknown): string {
  const a = numericValue(aValue)
  const h = numericValue(hValue)
  if (a === null || h === null || a === 0) return '—'
  return `${(Math.abs((h - a) / Math.abs(a)) * 100).toFixed(2)}%`
}

export function bboxText(bbox?: [number, number, number, number] | null): string {
  if (!bbox?.length) return '无坐标'
  return bbox.map((value) => Number(value).toFixed(1)).join(', ')
}

export function evidencePages(diff: DiffItem, labels?: SideDisplayLabels): string {
  const pages = (diff.evidence || [])
    .filter((item) => item.page)
    .map((item) => `${labels ? labelSideForJob(item.side, labels) : sideLabel(item.side)} p${item.page}`)
  return pages.length ? pages.join(' · ') : '—'
}

export function evidencePagesForSides(diff: DiffItem, sides: string[], fallbackPage?: number | null): string {
  const sideSet = new Set(sides)
  const pages = new Set<number>()
  ;(diff.evidence || []).forEach((item) => {
    if (item.page && sideSet.has(item.side || '')) pages.add(item.page)
  })
  if (!pages.size && fallbackPage) pages.add(fallbackPage)
  return pages.size ? Array.from(pages).sort((a, b) => a - b).map((page) => `p${page}`).join(' / ') : '—'
}

export function evidenceCountBySide(diff: DiffItem, labels?: SideDisplayLabels): string {
  const counts = (diff.evidence || []).reduce<Record<string, number>>((acc, item) => {
    const key = labels ? labelSideForJob(item.side, labels) : sideLabel(item.side)
    acc[key] = (acc[key] || 0) + 1
    return acc
  }, {})
  const parts = Object.entries(counts).map(([side, count]) => `${side} ${count}`)
  return parts.length ? parts.join(' / ') : `${labels?.a || 'A'} 0 / ${labels?.h || 'H'} 0`
}

export function reviewValues(diff: DiffItem) {
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

export type DiffTriageScope = 'real' | 'unresolved' | 'expected'
export type DiffSourceScope = 'cross_report' | 'a_internal' | 'h_internal'
export type CheckMode = JobSummary['check_mode']
export type SideDisplayLabels = { a: string; h: string; factLabel: string }

export const DIFF_TRIAGE_GROUPS: Array<{ key: DiffTriageScope; label: string; tone: string }> = [
  { key: 'real', label: '真实差异', tone: 'critical' },
  { key: 'unresolved', label: '待人工复核差异', tone: 'warning' },
  { key: 'expected', label: '预期差异', tone: 'expected' },
]

export const DIFF_SOURCE_GROUPS: Array<{ key: DiffSourceScope; label: string; description: string }> = [
  { key: 'cross_report', label: 'A/H报告不一致', description: 'A/H报告之间的不一致差异' },
  { key: 'a_internal', label: 'A股自身问题', description: 'A股报告自身存在的差异问题' },
  { key: 'h_internal', label: 'H股自身问题', description: 'H股报告自身存在的差异问题' },
]

export function diffSourceGroupsForJob(job: JobDetail): Array<{ key: DiffSourceScope; label: string; description: string }> {
  if (job.check_mode === 'h_bilingual') {
    return [
      {
        key: 'cross_report',
        label: 'H中英文不一致',
        description: 'H中文报告与H英文报告之间的不一致差异',
      },
    ]
  }
  return DIFF_SOURCE_GROUPS
}

export function normalizedTriageScope(diff: DiffItem): DiffTriageScope {
  if (diff.triage === 'expected') return 'expected'
  if (diff.triage === 'unresolved') return 'unresolved'
  return 'real'
}

export function normalizedDiffScope(diff: DiffItem, checkMode?: CheckMode): DiffSourceScope {
  if (checkMode === 'h_bilingual') return 'cross_report'
  if (diff.diff_scope === 'a_internal' || diff.diff_scope === 'h_internal') {
    return diff.diff_scope
  }
  if (diff.diff_type === 'internal') {
    const sides = new Set((diff.evidence || []).map((item) => item.side))
    if (sides.size === 1 && sides.has('A')) return 'a_internal'
    if (sides.size === 1 && sides.has('H')) return 'h_internal'
  }
  if (diff.diff_scope === 'cross_report') return 'cross_report'
  return 'cross_report'
}

export function emptyDiffGroups(): Record<DiffTriageScope, Record<DiffSourceScope, DiffItem[]>> {
  return DIFF_TRIAGE_GROUPS.reduce((triageAcc, triage) => {
    triageAcc[triage.key] = DIFF_SOURCE_GROUPS.reduce((scopeAcc, scope) => {
      scopeAcc[scope.key] = []
      return scopeAcc
    }, {} as Record<DiffSourceScope, DiffItem[]>)
    return triageAcc
  }, {} as Record<DiffTriageScope, Record<DiffSourceScope, DiffItem[]>>)
}

export function groupDiffsByTriageAndScope(diffs: DiffItem[], checkMode?: CheckMode): Record<DiffTriageScope, Record<DiffSourceScope, DiffItem[]>> {
  const groups = emptyDiffGroups()
  diffs.forEach((diff) => {
    groups[normalizedTriageScope(diff)][normalizedDiffScope(diff, checkMode)].push(diff)
  })
  return groups
}

export function summaryNumber(summary: Record<string, unknown>, key: string): number {
  return numericValue(summary[key]) ?? 0
}

export function formatDuration(seconds?: number | null): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '—'
  // Round the *total* seconds first, then split into minutes/seconds — rounding the
  // remainder in isolation (old: `Math.round(seconds % 60)`) can produce a remainder of
  // 60 (e.g. 119.6s -> floor(119.6/60)=1 minute, round(119.6%60)=round(59.6)=60), which
  // rendered the nonsensical "1分60秒". Rounding first guarantees the remainder is 0-59.
  const totalSeconds = Math.max(0, Math.round(seconds))
  const minutes = Math.floor(totalSeconds / 60)
  const rest = totalSeconds % 60
  return minutes ? `${minutes}分${rest}秒` : `${rest}秒`
}

export function sideLabelsForJob(job: JobDetail): SideDisplayLabels {
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

export function profileWarnings(profile?: ProfilePayload | null) {
  const audit = profile?.extraction_audit
  const details = audit?.engines?.warning_details || []
  const flagCount = (audit?.warning_flags?.length || 0) + (profile?.warning_flags?.length || 0)
  const messageCount = (audit?.warnings?.length || 0) + (profile?.warnings?.length || 0)
  const blocking = details.filter((item) => item?.blocking).length
  const auxiliary = details.filter((item) => item?.category === 'auxiliary_chart').length
  const total = Math.max(details.length, flagCount, messageCount)
  return { blocking, auxiliary, total }
}

export function profileScanText(profile?: ProfilePayload | null): string {
  const audit = profile?.extraction_audit
  const scanned = audit?.scanned_pages?.length || 0
  const total = audit?.total_pages || profile?.total_pages || 0
  if (!scanned && !total) return '—'
  return `${scanned || total}/${total || '—'}`
}

export function profileCoverage(profile?: ProfilePayload | null): string {
  const audit = profile?.extraction_audit
  const ratio = numericValue(audit?.coverage_ratio)
  if (ratio !== null) return `${(ratio * 100).toFixed(1)}%`
  const scanned = audit?.scanned_pages?.length || 0
  const total = audit?.total_pages || profile?.total_pages || 0
  return scanned && total ? `${((scanned / total) * 100).toFixed(1)}%` : '—'
}

export function auditConclusion(job: JobDetail, diffs: DiffItem[]) {
  const summary = job.comparison_summary || {}
  const real = summaryNumber(summary, 'real_diff_count')
  const unresolved = summaryNumber(summary, 'unresolved_diff_count')
  const blocking = summaryNumber(summary, 'blocking_warning_count') || summaryNumber(summary, 'core_warning_count')
  const auxiliary = summaryNumber(summary, 'aux_warning_count')
  const evidenceItems = diffs.filter((diff) => (diff.evidence || []).length > 0).length
  if (shouldRefreshJob(job)) {
    return {
      tone: 'running',
      title: '核查任务正在执行',
      copy: runningProgressLabel(job),
      pill: '蓝色 · 进行中',
      evidenceItems,
      blocking,
      auxiliary,
    }
  }
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
    copy: 'A/H 画像和证据定位已生成，可继续查看画像或抽样复核证据。',
    pill: '绿色 · 已完成',
    evidenceItems,
    blocking,
    auxiliary,
  }
}

export function reviewEvidenceMetric(summary: Record<string, unknown>, diffs: DiffItem[]) {
  const real = summaryNumber(summary, 'real_diff_count')
  const unresolved = summaryNumber(summary, 'unresolved_diff_count')
  const reviewQueueCount = real + unresolved
  const evidenceLocated = diffs.filter((diff) => (diff.evidence || []).length > 0).length
  const totalDiff = summaryNumber(summary, 'total_diff_count') || diffs.length
  const missingEvidence = Math.max(totalDiff - evidenceLocated, 0)
  const tone: 'critical' | 'warning' | 'teal' = real ? 'critical' : unresolved ? 'warning' : 'teal'
  return { real, unresolved, reviewQueueCount, evidenceLocated, totalDiff, missingEvidence, tone }
}

export function shouldRefreshJob(job: JobDetail | null): boolean {
  return Boolean(job?.status && JOB_REFRESH_STATUSES.has(job.status))
}

export function runningProgressFromSummary(summary?: Record<string, unknown>): JobProgressPayload | null {
  if (!summary) return null
  const stage = typeof summary.current_stage === 'string' ? summary.current_stage : null
  const percent = numericValue(summary.current_percent)
  const message = typeof summary.current_message === 'string' ? summary.current_message : null
  const updatedAt = typeof summary.last_progress_at === 'string' ? summary.last_progress_at : null
  if (!stage && percent === null && !message && !updatedAt) return null
  return {
    stage,
    percent,
    message,
    updated_at: updatedAt,
  }
}

export function stageLabel(stage?: string | null): string {
  if (stage === 'parsing') return '解析报告'
  if (stage === 'profiling') return '提取画像'
  if (stage === 'checking') return '差异核查'
  if (stage === 'reporting') return '生成报告'
  if (stage === 'failed') return '任务中断'
  return '排队等待'
}

export const RUNNING_STAGES = ['parsing', 'profiling', 'checking', 'reporting'] as const

export function runningStageIndex(stage?: string | null): number {
  if (!stage) return -1
  return RUNNING_STAGES.indexOf(stage as (typeof RUNNING_STAGES)[number])
}

// Shared by JobRunningProgress (detail page progress bar) and runningProgressLabel /
// historyProgressLabel below, so "progress percent is null/undefined" is handled the same
// way everywhere: no percent suffix at all (just the stage text), instead of one place
// defaulting to a misleading "0%" and another silently dropping the percent sign.
export function progressPercentText(percent?: number | null): string {
  if (typeof percent !== 'number' || !Number.isFinite(percent)) return ''
  return `${Math.max(0, Math.min(100, percent))}%`
}

export function runningProgressLabel(job: JobDetail): string {
  const progress = latestProgress(job)
  if (!progress) return statusLabel(job.status)
  const label = progress.message || stageLabel(progress.stage)
  const percentText = progressPercentText(progress.percent)
  return percentText ? `${label} ${percentText}` : label
}

export function historyProgressLabel(item: JobSummary): string {
  if (!JOB_REFRESH_STATUSES.has(item.status)) return ''
  const progress = runningProgressFromSummary(item.comparison_summary)
  if (!progress) return statusLabel(item.status)
  const label = progress.message || stageLabel(progress.stage)
  const percentText = progressPercentText(progress.percent)
  return percentText ? `${label} ${percentText}` : label
}

export function visualOcrStatusLabel(summary?: Record<string, unknown>): string {
  const status = summary?.visual_ocr_status
  if (!status || typeof status !== 'object') return ''
  const payload = status as Record<string, unknown>
  const mode = typeof payload.mode === 'string' ? payload.mode : ''
  const skippedReason = typeof payload.skipped_reason === 'string' ? payload.skipped_reason : ''
  const sides = payload.sides && typeof payload.sides === 'object' ? payload.sides as Record<string, unknown> : {}
  const ocrPageCount = Object.values(sides).reduce<number>((total, side) => {
    if (!side || typeof side !== 'object') return total
    const value = numericValue((side as Record<string, unknown>).ocr_page_count)
    return total + (value || 0)
  }, 0)
  if (skippedReason === 'runtime_ocr_disabled') return 'OCR 标准核查：未运行高成本视觉 OCR'
  if (skippedReason === 'easyocr_large_pdf') return 'OCR 已跳过：大文件仅 EasyOCR，优先完成任务'
  if (payload.timed_out) return `OCR 预算已用尽：已处理 ${ocrPageCount} 页`
  if (ocrPageCount) return `OCR ${mode || 'smart'}：已处理 ${ocrPageCount} 页`
  if (mode === 'off') return 'OCR 标准核查：未运行高成本视觉 OCR'
  return ''
}

export function latestProgress(job: JobDetail): JobProgressPayload | null {
  const progress = job.progress || []
  return progress.length ? progress[progress.length - 1] : runningProgressFromSummary(job.comparison_summary)
}

export function metricDisplayName(metric: ProfileMetricPreview): string {
  return metric.name?.zh || metric.name?.en || metric.canonical_key || '—'
}

export function narrativePageRange(item: ProfileNarrativePreview): string {
  return item.page_range?.length ? `${item.page_range[0]}-${item.page_range[1]}` : '—'
}
