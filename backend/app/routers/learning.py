from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Query, Request

from app.core.auth import CurrentUser, LearnerUser, OptionalUser
from app.core.database import DbSession
from app.schemas.common import DataResponse
from app.schemas.learning import (
    CourseDetailResponse,
    EnrollmentNextResponse,
    EnrollRequest,
    EnrollResponse,
    LikedVideoListResponse,
    MyEnrollmentListResponse,
    VideoDetailResponse,
    VideoLikeResponse,
    VideoProgressRequest,
    VideoProgressResponse,
    VideoViewRequest,
    VideoViewResponse,
)
from app.services import learning as learning_service

router = APIRouter(tags=["learning"])


# ==========================================
# [C-1] 강좌 상세 · 학습 현황 조회
# ==========================================
@router.get("/courses/{id}", response_model=DataResponse[CourseDetailResponse])
def get_course_detail(
    id: int,
    db: DbSession,
    current_user: OptionalUser = None,
) -> DataResponse[CourseDetailResponse]:
    return DataResponse(data=learning_service.get_course_detail_for_learner(db, id, current_user))


# ==========================================
# [C-2] 수강 등록 (목업 결제 · 무료)
# ==========================================
@router.post(
    "/courses/{id}/enroll",
    response_model=DataResponse[EnrollResponse],
    status_code=201,
)
def enroll_course(
    id: int,
    current_user: CurrentUser,
    db: DbSession,
    body: Annotated[EnrollRequest | None, Body()] = None,
) -> DataResponse[EnrollResponse]:
    return DataResponse(data=learning_service.enroll_course(db, id, current_user, body))


# ==========================================
# [C-3] 내 강좌 목록 조회 (수강 중 · 수료)
# ==========================================
@router.get("/me/enrollments", response_model=DataResponse[MyEnrollmentListResponse])
def list_my_enrollments(
    current_user: LearnerUser,
    db: DbSession,
    status: Annotated[
        str | None, Query(description="수강 상태 필터: ALL, IN_PROGRESS, COMPLETED")
    ] = None,
) -> DataResponse[MyEnrollmentListResponse]:
    return DataResponse(data=learning_service.list_my_enrollments(db, current_user, status))


# ==========================================
# [C-4] 이어가기 (첫 미완료 항목 조회)
# ==========================================
@router.get("/enrollments/{id}/next", response_model=DataResponse[EnrollmentNextResponse])
def get_next_learning_item(
    id: int,
    current_user: CurrentUser,
    db: DbSession,
) -> DataResponse[EnrollmentNextResponse]:
    return DataResponse(data=learning_service.get_next_learning_item(db, id, current_user))


# ==========================================
# [C-5] 영상 정보 및 잠금 확인
# ==========================================
@router.get("/videos/{id}", response_model=DataResponse[VideoDetailResponse])
def get_video_detail(
    id: int,
    request: Request,
    db: DbSession,
    current_user: OptionalUser = None,
) -> DataResponse[VideoDetailResponse]:
    has_auth_header = bool(request.headers.get("authorization"))
    return DataResponse(
        data=learning_service.get_video_detail(
            db, id, current_user, has_auth_header=has_auth_header
        )
    )


# ==========================================
# [C-6] 영상 조회수 집계 (3초 · 하루 1회)
# ==========================================
@router.post("/videos/{id}/views", response_model=DataResponse[VideoViewResponse])
def record_video_view(
    id: int,
    body: VideoViewRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> DataResponse[VideoViewResponse]:
    return DataResponse(data=learning_service.record_video_view(db, id, current_user, body))


# ==========================================
# [C-7] 학습 진도 · 시청 시간 저장 (PUT · POST)
# ==========================================
@router.put("/videos/{id}/progress", response_model=DataResponse[VideoProgressResponse])
@router.post("/videos/{id}/progress", response_model=DataResponse[VideoProgressResponse])
def save_video_progress(
    id: int,
    body: VideoProgressRequest,
    current_user: LearnerUser,
    db: DbSession,
) -> DataResponse[VideoProgressResponse]:
    return DataResponse(data=learning_service.save_video_progress(db, id, current_user, body))


# ==========================================
# [C-8] 영상 좋아요 등록 · 취소
# ==========================================
@router.post("/videos/{id}/like", response_model=DataResponse[VideoLikeResponse])
def like_video(
    id: int,
    current_user: CurrentUser,
    db: DbSession,
) -> DataResponse[VideoLikeResponse]:
    return DataResponse(data=learning_service.like_video(db, id, current_user))


@router.delete("/videos/{id}/like", response_model=DataResponse[VideoLikeResponse])
def unlike_video(
    id: int,
    current_user: CurrentUser,
    db: DbSession,
) -> DataResponse[VideoLikeResponse]:
    return DataResponse(data=learning_service.unlike_video(db, id, current_user))


# ==========================================
# [C-9] 내가 좋아요한 영상 목록 조회
# ==========================================
@router.get("/me/likes", response_model=DataResponse[LikedVideoListResponse])
def list_my_likes(
    current_user: LearnerUser,
    db: DbSession,
    video_type: Annotated[
        str | None,
        Query(alias="videoType", description="영상 유형 필터: ALL, OT, LESSON"),
    ] = None,
) -> DataResponse[LikedVideoListResponse]:
    return DataResponse(data=learning_service.list_my_likes(db, current_user, video_type))
