from __future__ import annotations

import base64
import json
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import case, desc, select
from sqlalchemy.orm import joinedload

from app.core.auth import get_current_user
from app.core.database import DbSession
from app.core.errors import AppError
from app.models.category import Category, Topic
from app.models.course import Course, Video
from app.models.learning import Enrollment, VideoLike
from app.models.user import LearnerProfile, LearnerTopicInterest, User
from app.schemas.common import CursorPageData, DataResponse
from app.schemas.course import FeedCourseInfo, FeedInstructorInfo, FeedOtItem
from app.services.storage import (
    get_profile_image_url,
    get_thumbnail_url,
    get_video_stream_url,
)

router = APIRouter(prefix="/feed", tags=["feed"])


@router.get("/ot", response_model=DataResponse[CursorPageData[FeedOtItem]])
def get_ot_feed(
    db: DbSession,
    current_user: Annotated[User, Depends(get_current_user)],
    authorization: Annotated[str | None, Header()] = None,
    cursor: str | None = Query(None, description="다음 페이지 커서"),
    size: int = Query(10, ge=1, le=100),
):
    if not authorization:
        raise AppError(401, "UNAUTHORIZED", "로그인이 필요합니다.")

    # 유저의 관심 직무 및 토픽 조회
    learner_prof = current_user.learner_profile
    target_category_id = learner_prof.target_category_id if learner_prof else None

    user_interests = db.scalars(
        select(LearnerTopicInterest.topic_id).where(
            LearnerTopicInterest.learner_id == current_user.id
        )
    ).all()
    interest_topic_ids = set(user_interests)

    order_clauses = []
    if interest_topic_ids or target_category_id:
        topic_match_score = (
            case((Course.topic_id.in_(interest_topic_ids), 2), else_=0)
            if interest_topic_ids
            else 0
        )
        cat_match_score = (
            case((Course.category_id == target_category_id, 1), else_=0)
            if target_category_id
            else 0
        )
        order_clauses.append(desc(topic_match_score + cat_match_score))

    order_clauses.extend([
        desc(Video.view_count),
        desc(Video.like_count),
        desc(Course.published_at),
        desc(Video.id),
    ])

    # DB 레벨에서 정렬 및 Eager Loading(N+1 방지)
    query = (
        select(Video)
        .join(Course, Course.id == Video.course_id)
        .options(
            joinedload(Video.course).joinedload(Course.category),
            joinedload(Video.course).joinedload(Course.instructor).joinedload(User.instructor_profile),
        )
        .where(
            Course.status == "PUBLISHED",
            Video.video_type == "OT",
            Video.is_active.is_(True),
        )
        .order_by(*order_clauses)
    )
    all_ot_videos = list(db.scalars(query).unique().all())
    total_ot = len(all_ot_videos)

    if total_ot == 0:
        return DataResponse(
            data=CursorPageData(
                items=[],
                next_cursor=None,
                has_next=False,
            )
        )

    # 커서 디코딩: {"videoId": ...} 및 {"offset": ...} 모두 지원, 끝 도달 시 첫 OT로 순환
    start_idx = 0
    if cursor:
        try:
            decoded = json.loads(base64.b64decode(cursor.encode()).decode())
            if isinstance(decoded, dict):
                if "videoId" in decoded:
                    target_vid = int(decoded["videoId"])
                    for idx, v in enumerate(all_ot_videos):
                        if v.id == target_vid:
                            start_idx = (idx + 1) % total_ot
                            break
                    else:
                        if "offset" in decoded:
                            start_idx = int(decoded["offset"]) % total_ot
                elif "offset" in decoded:
                    start_idx = int(decoded["offset"]) % total_ot
        except Exception:
            start_idx = 0

    page_videos = all_ot_videos[start_idx : start_idx + size]
    if not page_videos:
        page_videos = all_ot_videos[:size]
        start_idx = 0

    has_next = total_ot > 0
    next_offset = (start_idx + len(page_videos)) % total_ot
    last_vid_id = page_videos[-1].id
    next_cursor = base64.b64encode(
        json.dumps({"videoId": last_vid_id, "offset": next_offset}).encode()
    ).decode()

    # 수강 여부 및 좋아요 여부 일괄 조회
    course_ids = [v.course_id for v in page_videos]
    enrolled_courses = set()
    if course_ids:
        enr_rows = db.scalars(
            select(Enrollment.course_id).where(
                Enrollment.learner_id == current_user.id,
                Enrollment.course_id.in_(course_ids),
            )
        ).all()
        enrolled_courses = set(enr_rows)

    video_ids = [v.id for v in page_videos]
    liked_videos = set()
    if video_ids:
        like_rows = db.scalars(
            select(VideoLike.video_id).where(
                VideoLike.user_id == current_user.id,
                VideoLike.video_id.in_(video_ids),
            )
        ).all()
        liked_videos = set(like_rows)

    items: list[FeedOtItem] = []
    for v in page_videos:
        c = v.course
        instructor = c.instructor
        inst_profile = instructor.instructor_profile if instructor else None

        items.append(
            FeedOtItem(
                video_id=v.id,
                video_url=get_video_stream_url(v.id) or "",
                video_storage_key=v.video_storage_key,
                thumbnail_url=get_thumbnail_url(v.thumbnail_storage_key or c.thumbnail_storage_key),
                thumbnail_storage_key=v.thumbnail_storage_key or c.thumbnail_storage_key,
                title=v.title,
                duration_seconds=v.duration_seconds,
                view_count=v.view_count,
                like_count=v.like_count,
                is_liked=(v.id in liked_videos),
                course=FeedCourseInfo(
                    course_id=c.id,
                    title=c.title,
                    description=c.description,
                    category_name=c.category.name if c.category else "",
                    difficulty=c.difficulty,
                    price_krw=c.price_krw,
                    is_enrolled=(c.id in enrolled_courses),
                ),
                instructor=FeedInstructorInfo(
                    id=instructor.id,
                    name=instructor.name,
                    bio=inst_profile.bio if inst_profile else None,
                    profile_image_url=get_profile_image_url(instructor.profile_image_key),
                ),
            )
        )

    return DataResponse(
        data=CursorPageData(
            items=items,
            next_cursor=next_cursor,
            has_next=has_next,
        )
    )
