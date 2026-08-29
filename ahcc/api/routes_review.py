"""审计师覆盖路由 — 给差异打"已审/可接受/需追问"标签。

组间隔离：diff 所属任务的项目组必须等于当前激活项目组，否则一律 404
（不区分"diff 不存在"与"无权访问"，避免跨组探测 diff_id）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ahcc.api.deps import get_current_user
from ahcc.schemas import ReviewStatus
from ahcc.storage.repository import get_job_group_for_diff, save_review
from ahcc.user_context import CURRENT_PROJECT_GROUP_ID

router = APIRouter()


class ReviewRequest(BaseModel):
    diff_id: str
    status: ReviewStatus
    note: str | None = None
    reviewed_by: str | None = None


@router.post("/")
def submit_review(req: ReviewRequest, user: dict = Depends(get_current_user)) -> dict[str, str]:
    target = get_job_group_for_diff(req.diff_id)
    active_group = user.get("project_group_id") or CURRENT_PROJECT_GROUP_ID
    if target is None or (target.get("project_group_id") or CURRENT_PROJECT_GROUP_ID) != active_group:
        raise HTTPException(status_code=404, detail="diff not found")
    save_review(req.diff_id, req.status, req.note, req.reviewed_by or user.get("display_name"))
    return {"status": "ok"}
