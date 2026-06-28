from __future__ import annotations

from typing import Any


CURRENT_USER_ID = "chu-stanley"
CURRENT_DISPLAY_NAME = "Chu, Stanley"
CURRENT_OFFICE_LINE = "SH/FS3"
CURRENT_PROJECT_GROUP_ID = "sh-fs3"
CURRENT_PROJECT_GROUP_NAME = "SH/FS3"

DEFAULT_USER_PROFILE: dict[str, Any] = {
    "user_id": CURRENT_USER_ID,
    "display_name": CURRENT_DISPLAY_NAME,
    "office_line": CURRENT_OFFICE_LINE,
    "role_title": "",
    "project_group_id": CURRENT_PROJECT_GROUP_ID,
    "project_group_name": CURRENT_PROJECT_GROUP_NAME,
    "avatar_path": None,
}


def avatar_url_for(profile: dict[str, Any]) -> str | None:
    return "/api/users/current/avatar" if profile.get("avatar_path") else None


def public_user_payload(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": profile.get("user_id") or CURRENT_USER_ID,
        "display_name": profile.get("display_name") or CURRENT_DISPLAY_NAME,
        "office_line": profile.get("office_line") or CURRENT_OFFICE_LINE,
        "role_title": profile.get("role_title") or "",
        "avatar_url": avatar_url_for(profile),
        "project_group": {
            "id": profile.get("project_group_id") or CURRENT_PROJECT_GROUP_ID,
            "name": profile.get("project_group_name") or CURRENT_PROJECT_GROUP_NAME,
        },
    }
