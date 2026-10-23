from fastapi import APIRouter

from app.routers.instructor import courses, quizzes, sections, stats, videos

router = APIRouter(prefix="/instructor")

router.include_router(courses.router)
router.include_router(sections.router)
router.include_router(videos.router)
router.include_router(quizzes.router)
router.include_router(stats.router)

__all__ = ["router"]
