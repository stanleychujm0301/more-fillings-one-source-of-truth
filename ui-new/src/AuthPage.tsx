// Login / register page, rendered as a standalone full-screen card (no toolbar/nav) when
// the route is #/login or #/register. The card is split in two: a light brand panel that
// carries the KPMG mark and the KROSS-CHECK HUB identity exactly once, and a white form
// panel that opens straight onto the fields — no page heading.
// Registration either joins an existing project group
// (card-style picker fed by the public /api/auth/groups list — the native select element
// is banned by the UI token tests) or creates a new one; both endpoints log the user in immediately
// and return a full session payload.

import { useEffect, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import kpmgLogo from './assets/kpmg-logo.svg'
import { SpinnerIcon } from './icons'
import { fetchJson } from './api'
import type { SessionPayload } from './App'

export type AuthMode = 'login' | 'register'

export type GroupOption = {
  id: string
  name: string
  member_count: number
}

export function AuthPage({
  mode,
  session,
  onAuthenticated,
}: {
  mode: AuthMode
  session: SessionPayload | null
  onAuthenticated: (session: SessionPayload) => void
}) {
  const isRegister = mode === 'register'
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [officeLine, setOfficeLine] = useState('')
  const [roleTitle, setRoleTitle] = useState('')
  const [groupMode, setGroupMode] = useState<'join' | 'create'>('join')
  const [groupId, setGroupId] = useState('')
  const [groupName, setGroupName] = useState('')
  const [groups, setGroups] = useState<GroupOption[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Already signed in and somehow landed on #/login or #/register (e.g. back button):
  // go straight to the cockpit instead of showing a pointless login form.
  useEffect(() => {
    if (session) window.location.hash = '#/cockpit'
  }, [session])

  useEffect(() => {
    if (!isRegister) return
    let cancelled = false
    fetchJson<GroupOption[]>('/api/auth/groups')
      .then((payload) => {
        if (cancelled) return
        setGroups(payload)
        setGroupId((current) => current || payload[0]?.id || '')
      })
      .catch(() => {
        if (!cancelled) setGroups([])
      })
    return () => {
      cancelled = true
    }
  }, [isRegister])

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    setError(null)
    if (isRegister) {
      if (password !== confirmPassword) {
        setError('两次输入的密码不一致。')
        return
      }
      if (groupMode === 'join' && !groupId) {
        setError(groups && groups.length === 0 ? '暂无已有项目组，请切换到「创建新项目组」。' : '请选择要加入的项目组。')
        return
      }
      if (groupMode === 'create' && !groupName.trim()) {
        setError('请输入新项目组名称。')
        return
      }
    }
    setBusy(true)
    try {
      const payload = await fetchJson<SessionPayload>(
        isRegister ? '/api/auth/register' : '/api/auth/login',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(
            isRegister
              ? {
                  username: username.trim(),
                  password,
                  display_name: displayName.trim(),
                  office_line: officeLine.trim() || undefined,
                  role_title: roleTitle.trim() || undefined,
                  group_mode: groupMode,
                  project_group_id: groupMode === 'join' ? groupId : undefined,
                  project_group_name: groupMode === 'create' ? groupName.trim() : undefined,
                }
              : { username: username.trim(), password },
          ),
        },
      )
      onAuthenticated(payload)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  // Register stacks 7 fields plus the project-group picker; grouping them under captioned
  // sections gives that column a rhythm. Login has only two fields, so it renders the same
  // controls bare — a lone "账号信息" caption over a username/password pair is noise.
  const section = (title: string, children: ReactNode) =>
    isRegister ? (
      <div className="auth-section">
        <div className="auth-section-head">
          <span>{title}</span>
        </div>
        {children}
      </div>
    ) : (
      children
    )

  return (
    <div className="auth-shell">
      <section className="auth-card" aria-label={isRegister ? '注册账号' : '登录'}>
        <aside className="auth-brand-panel">
          <div className="auth-brand-mark">
            <img className="kpmg-logo" src={kpmgLogo} alt="KPMG" />
          </div>
          <div className="auth-brand-identity">
            <p className="auth-wordmark">
              {/* The name is one flex item so the flex gap only separates it from the KcH
                  pill — otherwise "KROSS-CHECK" / "HUB" get the gap too and the line wraps. */}
              <span className="wm-name">
                KROSS-CHECK <span className="wm-accent">HUB</span>
              </span>
              <span className="wm-abbr">KcH</span>
            </p>
            <span className="auth-brand-rule" aria-hidden="true" />
            <p className="auth-brand-hero">多重披露，一次核对</p>
            <p className="auth-brand-en">MORE FILINGS, ONE SOURCE OF TRUTH</p>
          </div>
        </aside>

        <div className="auth-form-panel">
          <form className="auth-form" onSubmit={handleSubmit}>
            {section(
              '账号信息',
              <>
                <label className="field-block">
                  用户名
                  <input
                    value={username}
                    onChange={(event) => setUsername(event.target.value)}
                    placeholder={isRegister ? '小写字母/数字开头，可含 _ 或 -' : '请输入用户名'}
                    autoComplete="username"
                    maxLength={32}
                    required
                  />
                </label>
                <label className="field-block">
                  密码
                  <input
                    type="password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    placeholder={isRegister ? '至少 6 位' : '请输入密码'}
                    autoComplete={isRegister ? 'new-password' : 'current-password'}
                    maxLength={64}
                    required
                  />
                </label>
                {isRegister && (
                  <label className="field-block">
                    确认密码
                    <input
                      type="password"
                      value={confirmPassword}
                      onChange={(event) => setConfirmPassword(event.target.value)}
                      placeholder="再次输入密码"
                      autoComplete="new-password"
                      maxLength={64}
                      required
                    />
                  </label>
                )}
              </>,
            )}
            {isRegister && (
              <>
                {section(
                  '个人信息',
                  <>
                    <label className="field-block">
                      姓名
                      <input
                        value={displayName}
                        onChange={(event) => setDisplayName(event.target.value)}
                        placeholder="用于展示的姓名，如 Chu, Stanley"
                        maxLength={80}
                        required
                      />
                    </label>
                    <div className="auth-two-col">
                      <label className="field-block">
                        所属部门（选填）
                        <input
                          value={officeLine}
                          onChange={(event) => setOfficeLine(event.target.value)}
                          placeholder="如 SH/FS3"
                          maxLength={40}
                        />
                      </label>
                      <label className="field-block">
                        职位角色（选填）
                        <input
                          value={roleTitle}
                          onChange={(event) => setRoleTitle(event.target.value)}
                          placeholder="如 审计经理"
                          maxLength={80}
                        />
                      </label>
                    </div>
                  </>,
                )}
                {section(
                  '项目组',
                  <>
                    <div className="segmented" aria-label="项目组加入方式">
                      <button
                        type="button"
                        className={groupMode === 'join' ? 'selected' : ''}
                        onClick={() => setGroupMode('join')}
                      >
                        加入已有项目组
                      </button>
                      <button
                        type="button"
                        className={groupMode === 'create' ? 'selected' : ''}
                        onClick={() => setGroupMode('create')}
                      >
                        创建新项目组
                      </button>
                    </div>
                    {groupMode === 'join' ? (
                      groups === null ? (
                        <p className="panel-copy">正在加载项目组列表…</p>
                      ) : groups.length === 0 ? (
                        <p className="panel-copy">暂无已有项目组，请切换到「创建新项目组」。</p>
                      ) : (
                        <div
                          className="depth-control auth-group-options"
                          role="group"
                          aria-label="选择项目组"
                        >
                          {groups.map((group) => (
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
                      )
                    ) : (
                      <label className="field-block">
                        新项目组名称
                        <input
                          value={groupName}
                          onChange={(event) => setGroupName(event.target.value)}
                          placeholder="如 SH/FS3"
                          maxLength={80}
                        />
                      </label>
                    )}
                  </>,
                )}
              </>
            )}
            {error && (
              <p className="auth-error" role="alert">
                {error}
              </p>
            )}
            <button className="primary auth-submit" type="submit" disabled={busy} aria-busy={busy}>
              {busy && <SpinnerIcon size={15} className="spin" />}
              {busy ? (isRegister ? '正在注册' : '正在登录') : isRegister ? '注册并进入工作台' : '登录'}
            </button>
          </form>

          <p className="auth-switch">
            {isRegister ? '已有账号？' : '没有账号？'}
            <a href={isRegister ? '#/login' : '#/register'}>{isRegister ? '去登录' : '去注册'}</a>
          </p>
        </div>

        {/* Card-wide footer rather than a block inside the form column: the line needs
            ~550px to stay unwrapped and the form column only offers ~360px. */}
        <p className="auth-demo-hint">
          演示账号 stanleychu · 同组 demouser1 · 异组 demouser2 · 密码 demo1234
        </p>
      </section>
    </div>
  )
}
