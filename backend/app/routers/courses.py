from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import case, desc, func, or_, select

from app.core.auth import get_optional_current_user
from app.core.database import DbSession
from app.core.errors import AppError
from app.models.category import Category, Topic
from app.models.course import Course
from app.models.learning import Enrollment
from app.models.user import LearnerProfile, LearnerTopicInterest, User
from app.schemas.common import DataResponse, PageData
from app.schemas.course import CategorySimple, CourseSearchItem, TopicSimple
from app.services.storage import get_thumbnail_url

router = APIRouter(prefix="/courses", tags=["courses"])


@router.get("", response_model=DataResponse[PageData[CourseSearchItem]])
def search_courses(
    db: DbSession,
    q: str | None = Query(None, description="강좌명/소개 검색 키워드"),
    categoryId: int | None = Query(None, description="직무 카테고리 ID"),
    topicId: int | None = Query(None, description="세부 주제 ID"),
    difficulty: str | None = Query(None, description="난이도"),
    price: str | None = Query(None, description="가격 필터: free 또는 paid"),
    sort: str = Query("popular", description="정렬 기준: popular, latest, difficulty, recommended"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    authorization: Annotated[str | None, Header()] = None,
):
    # 유효성 검사
    if difficulty is not None and difficulty not in ("BEGINNER", "INTERMEDIATE", "ADVANCED"):
        raise AppError(400, "VALIDATION_ERROR", "유효하지 않은 필터 또는 정렬 기준입니다.")
    if price is not None and price not in ("free", "paid"):
        raise AppError(400, "VALIDATION_ERROR", "유효하지 않은 필터 또는 정렬 기준입니다.")
    if sort not in ("popular", "latest", "difficulty", "recommended"):
        raise AppError(400, "VALIDATION_ERROR", "유효하지 않은 필터 또는 정렬 기준입니다.")

    current_user: User | None = None
    if sort == "recommended":
        if not authorization:
            raise AppError(401, "UNAUTHORIZED", "개인화 추천(sort=recommended) 정렬은 로그인이 필요합니다.")
        current_user = get_optional_current_user(db, authorization)
        if not current_user:
            raise AppError(401, "UNAUTHORIZED", "개인화 추천(sort=recommended) 정렬은 로그인이 필요합니다.")

    # 기본 쿼리: PUBLISHED 강좌만
    query = select(Course).where(Course.status == "PUBLISHED")

    if q:
        query = query.where(
            or_(
                Course.title.ilike(f"%{q}%"),
                Course.description.ilike(f"%{q}%"),
            )
        )

    if categoryId:
        query = query.where(Course.category_id == categoryId)

    if topicId:
        query = query.where(Course.topic_id == topicId)

    if difficulty:
        query = query.where(Course.difficulty == difficulty)

    if price == "free":
        query = query.where(Course.price_krw == 0)
    elif price == "paid":
        query = query.where(Course.price_krw > 0)

    # 전체 개수
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0

    # 정렬 처리
    # enrollmentCount 계산용 서브쿼리
    enrollment_subq = (
        select(Enrollment.course_id, func.count(Enrollment.id).label("enr_cnt"))
        .group_by(Enrollment.course_id)
        .subquery()
    )

    if sort == "popular":
        query = (
            query.outerjoin(enrollment_subq, Course.id == enrollment_subq.c.course_id)
            .order_by(desc(func.coalesce(enrollment_subq.c.enr_cnt, 0)), desc(Course.id))
        )
    elif sort == "latest":
        query = query.order_by(desc(Course.published_at), desc(Course.id))
    elif sort == "difficulty":
        # BEGINNER -> INTERMEDIATE -> ADVANCED
        diff_order = case(
            (Course.difficulty == "BEGINNER", 1),
            (Course.difficulty == "INTERMEDIATE", 2),
            (Course.difficulty == "ADVANCED", 3),
            else_=4,
        )
        query = query.order_by(diff_order, desc(Course.id))
    elif sort == "recommended":
        # 사용자 관심 직무 및 토픽 기준
        target_category_id = current_user.learner_profile.target_category_id if current_user and current_user.learner_profile else None
        interest_topics = db.scalars(
            select(LearnerTopicInterest.topic_id).where(
                LearnerTopicInterest.learner_id == current_user.id
            )
        ).all() if current_user else []

        topic_score = case(
            (Course.topic_id.in_(interest_topics), 2),
            else_=0,
        ) if interest_topics else 0
        cat_score = case(
            (Course.category_id == target_category_id, 1),
            else_=0,
        ) if target_category_id else 0

        query = query.order_by(desc(topic_score + cat_score), desc(Course.id))

    courses = db.scalars(query.offset((page - 1) * size).limit(size)).all()

    items: list[CourseSearchItem] = []
    for c in courses:
        enr_count = len(c.enrollments)
        items.append(
            CourseSearchItem(
                course_id=c.id,
                title=c.title,
                thumbnail_storage_key=c.thumbnail_storage_key,
                thumbnail_url=get_thumbnail_url(c.thumbnail_storage_key),
                instructor_id=c.instructor_id,
                instructor_name=c.instructor.name if c.instructor else "",
                category=CategorySimple(
                    id=c.category.id,
                    code=c.category.code,
                    name=c.category.name,
                ),
                topic=TopicSimple(
                    id=c.topic.id,
                    name=c.topic.name,
                ),
                difficulty=c.difficulty,
                price_krw=c.price_krw,
                is_free=(c.price_krw == 0),
                enrollment_count=enr_count,
            )
        )

    return DataResponse(
        data=PageData(
            items=items,
            page=page,
            size=size,
            total=total,
        )
    )
