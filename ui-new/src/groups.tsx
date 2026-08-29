// Project-group UI: the toolbar switcher (switch the session's active group — all
// group-scoped views follow) and the profile-page manager (list memberships, join an
// existing group, create a new one). Both talk to the session-scoped backend endpoints
// and return a full session payload so the caller can setSession in one step.

import { useEffect, useRef, useState } from 'react'
import { BuildingIcon, CheckIcon, SpinnerIcon } from './icons'
import { fetchJson } from './api'
import type { GroupOption } from './AuthPage'
import type { SessionPayload } from './App'

export function GroupSwitcher({
  session,
  busy,
  onSwitch,
}: {
  session: SessionPayload | null
  busy: boolean
  onSwitch: (groupId: string) => void
}) {
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const memberships = session?.memberships || []

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false)
      }
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  if (!session || memberships.length === 0) return null

  return (
    <div className="group-switcher" ref={containerRef}>
      <button
        type="button"
        className="group-switcher-toggle"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-label="切换项目组"
        title="切换当前项目组"
      >
        <BuildingIcon size={13} />
        <span>{session.project_group.name}</span>
      </button>
      {open && (
        <div className="group-switcher-menu" role="listbox" aria-label="我的项目组">
          {memberships.map((membership) => (
            <button
              key={membership.group_id}
              type="button"
              role="option"
              aria-selected={membership.is_active}
              className={`group-switcher-option ${membership.is_active ? 'selected' : ''}`}
              disabled={busy}
              onClick={() => {
                setOpen(false)
                onSwitch(membership.group_id)
              }}
            >
              <span>{membership.group_name}</span>
              {membership.is_active && <CheckIcon size={13} />}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export function GroupManagerPanel({
  session,
  onSessionUpdate,
}: {
  session: SessionPayload | null
  onSessionUpdate: (session: SessionPayload) => void
}) {
  const memberships = session?.memberships || []
  const [expanded, setExpanded] = useState<'join' | 'create' | null>(null)
  const [groups, setGroups] = useState<GroupOption[] | null>(null)
  const [groupId, setGroupId] = useState('')
  const [groupName, setGroupName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    if (expanded !== 'join') return
    let cancelled = false
    fetchJson<GroupOption[]>('/api/auth/groups')
      .then((payload) => {
        if (cancelled) return
        setGroups(payload)
        const joined = new Set(memberships.map((membership) => membership.group_id))
        setGroupId((current) => current || payload.find((group) => !joined.has(group.id))?.id || '')
      })
      .catch(() => {
        if (!cancelled) setGroups([])
      })
    return () => {
      cancelled = true
    }
    // memberships changes re-run the fetch only when the join panel is open; the joined
    // filter below always reads the latest memberships at render time.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded])

  if (!session) return null

  const joinedIds = new Set(memberships.map((membership) => membership.group_id))
  const joinable = (groups || []).filter((group) => !joinedIds.has(group.id))

  async function submit(mode: 'join' | 'create') {
    if (busy) return
    setError(null)
    setNotice(null)
    if (mode === 'join' && !groupId) {
      setError('请选择要加入的项目组。')
      return
    }
    if (mode === 'create' && !groupName.trim()) {
      setError('请输入新项目组名称。')
      return
    }
    setBusy(true)
    try {
      const payload = await fetchJson<{
        group: { group_id: string; group_name: string }
        already_member: boolean
        session: SessionPayload
      }>('/api/groups/join', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(
          mode === 'join' ? { mode, group_id: groupId } : { mode, group_name: groupName.trim() },
        ),
      })
      if (payload.session) onSessionUpdate(payload.session)
      setNotice(
        payload.already_member
          ? `你已在 ${payload.group.group_name} 项目组，已切换为当前项目组。`
          : `已加入 ${payload.group.group_name} 项目组并切换为当前项目组。`,
      )
      setExpanded(null)
      setGroupName('')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="panel group-manager">
      <div className="panel-head">
        <div>
          <p className="eyebrow">
            <BuildingIcon size={12} />
            我的项目组
          </p>
          <h2>项目组与共享范围</h2>
          <p className="panel-copy">同一项目组内核查结果全员共享；切换当前项目组后，工作台与项目历史同步切换。</p>
        </div>
      </div>
      <ul className="group-membership-list">
        {memberships.map((membership) => (
          <li key={membership.group_id} className={membership.is_active ? 'active' : ''}>
            <span>{membership.group_name}</span>
            {membership.is_active && <em className="group-current-badge">当前</em>}
          </li>
        ))}
      </ul>
      <div className="segmented small group-manager-actions">
        <button
          type="button"
          className={expanded === 'join' ? 'selected' : ''}
          onClick={() => {
            setError(null)
            setNotice(null)
            setExpanded(expanded === 'join' ? null : 'join')
          }}
        >
          加入已有项目组
        </button>
        <button
          type="button"
          className={expanded === 'create' ? 'selected' : ''}
          onClick={() => {
            setError(null)
            setNotice(null)
            setExpanded(expanded === 'create' ? null : 'create')
          }}
        >
          创建新项目组
        </button>
      </div>
      {expanded === 'join' &&
        (groups === null ? (
          <p className="panel-copy">正在加载项目组列表…</p>
        ) : joinable.length === 0 ? (
          <p className="panel-copy">暂无可加入的其他项目组。</p>
        ) : (
          <>
            <div className="depth-control auth-group-options" role="group" aria-label="选择要加入的项目组">
              {joinable.map((group) => (
                <button
                  key={group.id}
                  type="button"
                  className={`depth-option ${groupId === group.id ? 'selected' : ''}`}
                  aria-pressed={groupId === group.id}
                  onClick={() => setGroupId(group.id)}
                >
                  <strong>{group.name}</strong>
                  <small>{group.member_count} 位成员 · 加入即共享核查结果</small>
                </button>
              ))}
            </div>
            <button type="button" className="primary" onClick={() => submit('join')} disabled={!groupId || busy}>
              {busy ? <SpinnerIcon size={15} className="spin" /> : <CheckIcon size={15} />}
              {busy ? '正在加入' : '加入并切换'}
            </button>
          </>
        ))}
      {expanded === 'create' && (
        <>
          <label className="field-block">
            新项目组名称
            <input
              value={groupName}
              onChange={(event) => setGroupName(event.target.value)}
              placeholder="如 SH/FS3"
              maxLength={80}
            />
          </label>
          <button type="button" className="primary" onClick={() => submit('create')} disabled={!groupName.trim() || busy}>
            {busy ? <SpinnerIcon size={15} className="spin" /> : <CheckIcon size={15} />}
            {busy ? '正在创建' : '创建并切换'}
          </button>
        </>
      )}
      {error && (
        <p className="auth-error" role="alert">
          {error}
        </p>
      )}
      {notice && <p className="panel-copy">{notice}</p>}
    </div>
  )
}
