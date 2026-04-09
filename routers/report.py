from __future__ import annotations

from collections import Counter
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.database import get_db
from models.report import Report, ReportEvidence
from models.user import User
from schemas.report import ReportResponse, ReportSummaryResponse
from services.report_storage_service import upload_report_file
from services.report_target_service import resolve_target_snapshot_name
from core.security import get_current_user  # 실제 경로 확인 필요

router = APIRouter(prefix="/reports", tags=["reports"])

ALLOWED_TARGET_TYPES = {"USER", "PARTY", "CHAT"}
ALLOWED_CATEGORIES = {"PROFANITY", "SCAM", "SPAM"}
ALLOWED_STATUSES = {"PENDING", "IN_REVIEW", "APPROVED", "REJECTED"}


@router.post("", response_model=ReportResponse, status_code=status.HTTP_201_CREATED)
async def create_report(
    target_type: Annotated[str, Form(...)],
    target_id: Annotated[UUID, Form(...)],
    category: Annotated[str, Form(...)],
    description: Annotated[str, Form(...)],
    files: list[UploadFile] | None = File(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        target_type = target_type.upper().strip()
        category = category.upper().strip()
        description = description.strip()

        if target_type not in ALLOWED_TARGET_TYPES:
            raise HTTPException(status_code=400, detail="유효하지 않은 target_type 입니다.")

        if category not in ALLOWED_CATEGORIES:
            raise HTTPException(status_code=400, detail="유효하지 않은 category 입니다.")

        if not description:
            raise HTTPException(status_code=400, detail="신고 내용을 입력해주세요.")

        snapshot_name = await resolve_target_snapshot_name(db, target_type, target_id)
        if snapshot_name is None:
            raise HTTPException(status_code=404, detail="신고 대상을 찾을 수 없습니다.")

        report = Report(
            reporter_id=current_user.id,
            target_type=target_type,
            target_id=target_id,
            target_snapshot_name=snapshot_name,
            category=category,
            description=description,
            status="PENDING",
            action_result_code="NONE",
        )

        db.add(report)
        await db.flush()

        evidence_rows: list[ReportEvidence] = []

        if files:
            for file in files:
                if not file.filename:
                    continue

                uploaded = await upload_report_file(file=file, report_id=str(report.id))

                evidence = ReportEvidence(
                    report_id=report.id,
                    object_key=uploaded["object_key"],
                    original_filename=uploaded.get("original_filename"),
                    content_type=uploaded.get("content_type"),
                    file_size=uploaded.get("file_size"),
                )
                evidence_rows.append(evidence)

            if evidence_rows:
                db.add_all(evidence_rows)
                report.evidence_key = evidence_rows[0].object_key

        await db.commit()

        result = await db.execute(
            select(Report)
            .options(selectinload(Report.evidences))
            .where(Report.id == report.id)
        )
        created_report = result.scalar_one()
        return created_report

    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise
    

@router.get("", response_model=list[ReportResponse])
async def list_my_reports(
    status_filter: str | None = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = (
        select(Report)
        .options(selectinload(Report.evidences))
        .where(Report.reporter_id == current_user.id)
        .order_by(Report.created_at.desc())
    )

    if status_filter:
        normalized_status = status_filter.upper().strip()
        if normalized_status not in ALLOWED_STATUSES:
            raise HTTPException(status_code=400, detail="유효하지 않은 status 입니다.")
        query = query.where(Report.status == normalized_status)

    result = await db.execute(query)
    reports = result.scalars().unique().all()
    return list(reports)


@router.get("/summary", response_model=ReportSummaryResponse)
async def get_my_report_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Report.status, func.count(Report.id))
        .where(Report.reporter_id == current_user.id)
        .group_by(Report.status)
    )

    counts = Counter({report_status: count for report_status, count in result.all()})

    return ReportSummaryResponse(
        pending=counts.get("PENDING", 0),
        in_review=counts.get("IN_REVIEW", 0),
        approved=counts.get("APPROVED", 0),
        rejected=counts.get("REJECTED", 0),
    )