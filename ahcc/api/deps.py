"""请求级依赖：当前登录用户解析。

受保护 router 统一挂 dependencies=[Depends(get_current_user)]：
- /health、/api/auth/*、静态资源不在受保护 router 下，天然放行，无需路径白名单；
- 依赖同时把解析后的用户（含激活项目组、memberships）注入端点参数。
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from ahcc.auth import SESSION_COOKIE
from ahcc.config import settings
from ahcc.storage import repository


def get_current_user(request: Request) -> dict:
    """解析当前登录用户；返回的 profile 已按 session 激活组解析 project_group_id/name。

    AHCC_AUTH_DISABLED=1（测试/CLI/eval）时旁路为演示用户；旁路同样走
    resolve_session_profile，保证响应契约（memberships）与真实登录路径不分叉。
    """
    if settings.auth_disabled:
        return repository.resolve_session_profile(repository.get_current_user_profile(), None)
    token = request.cookies.get(SESSION_COOKIE)
    profile = repository.get_session_user(token) if token else None
    if not profile:
        raise HTTPException(status_code=401, detail="not authenticated")
    return profile
