"""注册 / 登录 / 登出 / 项目组候选列表 —— 公开端点（无会话要求）。

会话形态：HttpOnly + SameSite=Lax Cookie（ahcc_session），DB 只存 token 的 sha256。
注册与登录成功后即种 Cookie 并返回完整 session payload（与 /api/session/current 同构），
前端一次 setSession 完成登录态切换。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ahcc.auth import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    SESSION_COOKIE,
    SESSION_TTL_DAYS,
    is_valid_username,
    normalize_username,
)
from ahcc.storage import repository
from ahcc.storage.repository import DuplicateUserError

router = APIRouter()

_LOGIN_FAILED_DETAIL = "用户名或密码不正确"


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
    display_name: str = Field(..., min_length=1, max_length=80)
    office_line: str | None = Field(None, max_length=40)
    role_title: str | None = Field(None, max_length=80)
    group_mode: str = "join"  # "join"=加入已有项目组 | "create"=创建新项目组
    project_group_id: str | None = None
    project_group_name: str | None = Field(None, max_length=80)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=80)
    password: str = Field(..., min_length=1, max_length=PASSWORD_MAX_LENGTH)


def _set_session_cookie(request: Request, response: Response, raw_token: str) -> None:
    # secure 必须按 scheme 动态设置：本地 127.0.0.1 是 http，写死 secure=True 会导致
    # 浏览器拒存 Cookie，登录表现为"点了没反应"——最难排查的一类演示事故。
    response.set_cookie(
        SESSION_COOKIE,
        raw_token,
        max_age=SESSION_TTL_DAYS * 86400,
        httponly=True,
        samesite="lax",
        path="/",
        secure=request.url.scheme == "https",
    )


def _login_response(request: Request, response: Response, profile: dict) -> dict:
    raw_token = repository.create_session(profile["user_id"])
    _set_session_cookie(request, response, raw_token)
    # 新 session 的 active_group_id 为 NULL → 解析链落到 profile 的上次激活组
    return repository.build_session_payload(repository.resolve_session_profile(profile, None))


@router.post("/register")
def register(payload: RegisterRequest, request: Request, response: Response) -> dict:
    username = normalize_username(payload.username)
    if not is_valid_username(username):
        raise HTTPException(status_code=422, detail="用户名需为 3-32 位小写字母、数字、_ 或 -")
    display_name = (payload.display_name or "").strip()
    if not display_name:
        raise HTTPException(status_code=422, detail="姓名不能为空")

    mode = (payload.group_mode or "join").strip().lower()
    if mode == "create":
        group_name = (payload.project_group_name or "").strip()
        if not group_name:
            raise HTTPException(status_code=422, detail="请输入新项目组名称")
        group = repository.ensure_project_group(group_name, created_by=username)
    elif mode == "join":
        group = repository.get_project_group((payload.project_group_id or "").strip())
        if group is None:
            raise HTTPException(status_code=404, detail="项目组不存在")
    else:
        raise HTTPException(status_code=422, detail="group_mode must be join or create")

    try:
        profile = repository.create_user(
            user_id=username,
            display_name=display_name,
            password=payload.password,
            office_line=(payload.office_line or "").strip(),
            role_title=(payload.role_title or "").strip(),
            project_group_id=group["group_id"],
            project_group_name=group["group_name"],
        )
    except DuplicateUserError:
        raise HTTPException(status_code=409, detail="用户名已存在") from None

    return _login_response(request, response, profile)


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response) -> dict:
    profile = repository.verify_user_credentials(payload.username, payload.password)
    if not profile:
        # 统一文案：不区分"用户不存在"与"密码错误"，防止用户名枚举
        raise HTTPException(status_code=401, detail=_LOGIN_FAILED_DETAIL)
    return _login_response(request, response, profile)


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        repository.delete_session(token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "ok"}


@router.get("/groups")
def list_groups() -> list[dict]:
    """注册页"加入已有项目组"的候选列表（公开，最小字段）。"""
    return [
        {"id": g["group_id"], "name": g["group_name"], "member_count": g["member_count"]}
        for g in repository.list_project_groups()
    ]
