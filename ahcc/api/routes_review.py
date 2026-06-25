"""审计师覆盖路由 — 给差异打"已审/可接受/需追问"标签。"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ahcc.schemas import ReviewStatus
from ahcc.storage.repository import save_review

router = APIRouter()


class ReviewRequest(BaseModel):
    diff_id: str
    status: ReviewStatus
    note: str | None = None
    reviewed_by: str | None = None


@router.post("/")
def submit_review(req: ReviewRequest) -> dict[str, str]:
    save_review(req.diff_id, req.status, req.note, req.reviewed_by)
    return {"status": "ok"}
