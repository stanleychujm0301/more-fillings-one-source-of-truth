"""Demo user session, profile, and avatar routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ahcc.config import settings
from ahcc.storage.repository import (
    get_current_session,
    get_current_user_profile,
    set_current_user_avatar,
    update_current_user_profile,
)
from ahcc.user_context import CURRENT_USER_ID

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


@router.get("/session/current")
def current_session() -> dict:
    return get_current_session()


@router.patch("/users/current")
def update_current_user(payload: CurrentUserUpdate) -> dict:
    return update_current_user_profile(
        display_name=payload.display_name,
        office_line=payload.office_line,
        role_title=payload.role_title,
    )


@router.post("/users/current/avatar")
async def upload_current_user_avatar(avatar: UploadFile = File(...)) -> dict:
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
    avatar_path = avatar_dir / f"{CURRENT_USER_ID}{ext}"
    avatar_path.write_bytes(content)

    user = set_current_user_avatar(str(avatar_path))
    return {"avatar_url": user["avatar_url"], "user": user}


@router.get("/users/current/avatar")
def get_current_user_avatar() -> FileResponse:
    profile = get_current_user_profile()
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
