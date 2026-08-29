"""项目组管理（登录态）：我的组列表、加入/创建组。

加入或创建成功后自动切换为当前激活组（演示动线：加入组 → 立刻看到该组数据），
响应直接带新的 session payload，前端一次 setSession 完成，无需再调 active-group。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ahcc.api.deps import get_current_user
from ahcc.auth import SESSION_COOKIE
from ahcc.storage import repository
from ahcc.storage.repository import GroupNotFoundError

router = APIRouter()


class JoinGroupRequest(BaseModel):
    mode: str = "join"  # "join"=加入已有组 | "create"=创建新组
    group_id: str | None = None
    group_name: str | None = Field(None, max_length=80)


@router.get("/my")
def my_groups(user: dict = Depends(get_current_user)) -> list[dict]:
    active_group_id = user.get("project_group_id")
    member_counts = {g["group_id"]: g["member_count"] for g in repository.list_project_groups()}
    return [
        {
            "group_id": m["group_id"],
            "group_name": m["group_name"],
            "is_active": m["group_id"] == active_group_id,
            "member_count": member_counts.get(m["group_id"], 0),
        }
        for m in user.get("memberships") or []
    ]


@router.post("/join")
def join_group_endpoint(
    payload: JoinGroupRequest,
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    mode = (payload.mode or "join").strip().lower()
    if mode not in {"join", "create"}:
        raise HTTPException(status_code=422, detail="mode must be join or create")
    group_name = (payload.group_name or "").strip() or None
    if mode == "create" and not group_name:
        raise HTTPException(status_code=422, detail="请输入新项目组名称")

    try:
        result = repository.join_group(
            user["user_id"],
            mode=mode,
            group_id=payload.group_id,
            group_name=group_name,
        )
    except GroupNotFoundError:
        raise HTTPException(status_code=404, detail="项目组不存在") from None

    # join 后自动切换激活组（用户刚加入，成员校验必然通过）
    raw_token = request.cookies.get(SESSION_COOKIE)
    profile = repository.switch_active_group(user["user_id"], raw_token, result["group"]["group_id"])
    return {
        "group": result["group"],
        "already_member": result["already_member"],
        "session": repository.build_session_payload(profile) if profile else None,
    }
