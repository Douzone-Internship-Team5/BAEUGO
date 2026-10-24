from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.course import Course, Section
from app.models.video import Video, VideoScript
from app.services.processing.engine.errors import ProcessingError
from app.services.processing.engine.schemas import (
    SectionInput,
    SectionVideo,
    Segment,
    Transcript,
    VideoInput,
    integer,
)


def validated_transcript(video: Video, segments) -> Transcript:
    transcript = Transcript(
        video.id,
        tuple(Segment(s.seq, s.start_ms, s.end_ms, s.content) for s in segments),
    )
    if not integer(video.duration_seconds):
        raise ProcessingError("INVALID_DURATION")
    if any(s.end_ms > (video.duration_seconds + 1) * 1000 for s in transcript.segments):
        raise ProcessingError("INVALID_TIMESTAMPS")
    return transcript


def editable_course(db: Session, course_id: int) -> Course:
    course = db.get(Course, course_id)
    if course is None or course.status not in ("DRAFT", "EDITING"):
        raise ProcessingError("COURSE_NOT_EDITABLE")
    return course


def video_snapshot(db: Session, video_id: int) -> dict:
    video = db.get(Video, video_id)
    if video is None or not video.is_active:
        raise ProcessingError("VIDEO_INACTIVE")
    course = editable_course(db, video.course_id)
    section = db.get(Section, video.section_id) if video.section_id is not None else None
    if video.video_type == "LESSON" and (section is None or not section.is_active):
        raise ProcessingError("SECTION_INACTIVE")
    return {
        "video": asdict(
            VideoInput(video.id, video.video_storage_key, video.duration_seconds, video.video_type)
        ),
        "metadata": {
            "course_title": course.title,
            "section_title": section.title if section else "OT",
            "video_title": video.title,
            "course_description": course.description,
            "difficulty": course.difficulty,
            "category_code": course.category.code,
            "category_name": course.category.name,
            "topic_id": course.topic_id,
            "topic": course.topic.name,
        },
    }


def section_input(db: Session, section_id: int) -> SectionInput | None:
    section = db.get(Section, section_id)
    if section is None or not section.is_active:
        raise ProcessingError("SECTION_INACTIVE")
    course = editable_course(db, section.course_id)
    videos = db.scalars(
        select(Video)
        .where(
            Video.section_id == section.id,
            Video.is_active.is_(True),
            Video.video_type == "LESSON",
        )
        .order_by(Video.sort_order, Video.id)
    ).all()
    if not videos:
        raise ProcessingError("SECTION_EMPTY")
    if any(video.stt_status == "FAILED" for video in videos):
        raise ProcessingError("SECTION_TRANSCRIPT_FAILED")
    if any(video.stt_status != "COMPLETED" for video in videos):
        return None
    inputs = []
    for video in videos:
        scripts = db.scalars(
            select(VideoScript).where(VideoScript.video_id == video.id).order_by(VideoScript.seq)
        ).all()
        transcript = validated_transcript(video, scripts)
        inputs.append(SectionVideo(video.id, section.id, video.title, video.sort_order, transcript))
    return SectionInput(
        course.id,
        course.title,
        section.id,
        section.title,
        tuple(inputs),
        tuple(video.id for video in videos),
        course_description=course.description,
        difficulty=course.difficulty,
        topic=course.topic.name,
        category_code=course.category.code,
        category_name=course.category.name,
        topic_id=course.topic_id,
    )
