"""登录用户会话、个人资料、头像与激活项目组切换路由。

所有端点经 router 级 dependencies=[Depends(get_current_user)] 鉴权（见 main.py 接线）；
端点参数里的 user 是按 session 激活组解析后的 profile。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ahcc.api.deps import get_current_user
from ahcc.auth import SESSION_COOKIE
from ahcc.config import settings
from ahcc.storage.repository import (
    build_session_payload,
    get_project_group,
    get_user_profile,
    set_current_user_avatar,
    switch_active_group,
    update_current_user_profile,
)

router = APIRouter()

_MAX_AVATAR_BYTES = 2 * 1024 * 1024
_ALLOWED_AVATARS = {
    "image/png": (".png", b"\x89PNG\r\n\x1a\n"),
    "image/jpeg": (".jpg", b"\xff\xd8\xff"),
    "image/webp": (".webp", b"RIFF"),
}


class CurrentUserUpdate(BaseModel):
    display_name: str | None = Field(None, max_length=80)
    office_line: str | None = Field(None, max_length=40)
    role_title: str | None = Field(None, max_length=80)


class ActiveGroupUpdate(BaseModel):
    group_id: str = Field(..., min_length=1, max_length=80)


@router.get("/session/current")
def current_session(user: dict = Depends(get_current_user)) -> dict:
    return build_session_payload(user)


@router.post("/session/active-group")
def switch_active_group_endpoint(
    payload: ActiveGroupUpdate,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    """切换当前 session 的激活项目组；成功返回完整 session payload（前端直接 setSession）。"""
    if get_project_group(payload.group_id) is None:
        raise HTTPException(status_code=404, detail="项目组不存在")
    raw_token = request.cookies.get(SESSION_COOKIE)
    profile = switch_active_group(user["user_id"], raw_token, payload.group_id)
    if profile is None:
        raise HTTPException(status_code=403, detail="不是该项目组成员")
    return build_session_payload(profile)


@router.patch("/users/current")
def update_current_user(
    payload: CurrentUserUpdate,
    user: dict = Depends(get_current_user),
) -> dict:
    return update_current_user_profile(
        user["user_id"],
        display_name=payload.display_name,
        office_line=payload.office_line,
        role_title=payload.role_title,
    )


@router.post("/users/current/avatar")
async def upload_current_user_avatar(
    avatar: UploadFile = File(...),
    user: dict = Depends(get_current_user),
) -> dict:
    content_type = (avatar.content_type or "").split(";", 1)[0].strip().lower()
    if content_type not in _ALLOWED_AVATARS:
        raise HTTPException(status_code=415, detail="avatar must be png, jpg, or webp")

    content = await avatar.read()
    if len(content) > _MAX_AVATAR_BYTES:
        raise HTTPException(status_code=413, detail="avatar must be 2MB or smaller")

    ext, signature = _ALLOWED_AVATARS[content_type]
    if not content.startswith(signature):
        raise HTTPException(status_code=415, detail="avatar content does not match declared image type")

    avatar_dir = settings.storage_dir / "user-assets" / "avatars"
    avatar_dir.mkdir(parents=True, exist_ok=True)
    avatar_path = avatar_dir / f"{user['user_id']}{ext}"
    avatar_path.write_bytes(content)

    updated_user = set_current_user_avatar(user["user_id"], str(avatar_path))
    return {"avatar_url": updated_user["avatar_url"], "user": updated_user}


@router.get("/users/current/avatar")
def get_current_user_avatar(user: dict = Depends(get_current_user)) -> FileResponse:
    profile = get_user_profile(user["user_id"]) or user
    avatar_path = Path(profile.get("avatar_path") or "")
    if not avatar_path.is_file():
        raise HTTPException(status_code=404, detail="avatar not found")

    media_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(avatar_path.suffix.lower(), "application/octet-stream")
    return FileResponse(avatar_path, media_type=media_type)
