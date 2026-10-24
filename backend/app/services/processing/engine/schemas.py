import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Literal

from .errors import ProcessingError


def require(condition, code="INVALID_INPUT"):
    if not condition:
        raise ProcessingError(code)


def integer(value, minimum=1):
    return type(value) is int and value >= minimum


def text(value):
    return isinstance(value, str) and bool(value.strip())


@dataclass(frozen=True)
class Segment:
    seq: int
    start_ms: int
    end_ms: int
    content: str

    def __post_init__(self):
        require(integer(self.seq) and integer(self.start_ms, 0) and integer(self.end_ms))
        require(self.end_ms > self.start_ms and text(self.content), "INVALID_SEGMENT")


@dataclass(frozen=True)
class Transcript:
    video_id: int
    segments: tuple[Segment, ...]

    def __post_init__(self):
        require(integer(self.video_id) and bool(self.segments), "EMPTY_TRANSCRIPT")
        require(
            [s.seq for s in self.segments] == list(range(1, len(self.segments) + 1)),
            "INVALID_SEQUENCE",
        )
        require(
            all(
                a.start_ms <= b.start_ms
                for a, b in zip(self.segments, self.segments[1:], strict=False)
            ),
            "INVALID_TIMESTAMPS",
        )

    def rows(self):
        return [dict(video_id=self.video_id, **asdict(s)) for s in self.segments]


@dataclass(frozen=True)
class VideoInput:
    video_id: int
    video_storage_key: str
    duration_seconds: int
    video_type: Literal["OT", "LESSON"]
    language: str = "ko"

    def __post_init__(self):
        require(integer(self.video_id) and integer(self.duration_seconds))
        require(text(self.video_storage_key) and self.video_type in ("OT", "LESSON"))
        require(
            isinstance(self.language, str) and re.fullmatch(r"[a-z]{2,3}", self.language),
            "INVALID_LANGUAGE",
        )


@dataclass(frozen=True)
class SectionVideo:
    video_id: int
    section_id: int
    title: str
    sort_order: int
    transcript: Transcript
    video_type: str = "LESSON"
    is_active: bool = True

    def __post_init__(self):
        require(integer(self.video_id) and integer(self.section_id) and integer(self.sort_order, 0))
        require(text(self.title) and self.transcript.video_id == self.video_id)
        require(self.video_type == "LESSON" and self.is_active is True, "NOT_ACTIVE_LESSON")


@dataclass(frozen=True)
class SectionInput:
    course_id: int
    course_title: str
    section_id: int
    section_title: str
    videos: tuple[SectionVideo, ...]
    expected_video_ids: tuple[int, ...]
    question_count: int = 3
    course_description: str = ""
    difficulty: str = ""
    topic: str = ""
    category_code: str = ""
    topic_id: int | None = None
    category_name: str = ""

    def __post_init__(self):
        require(integer(self.course_id) and integer(self.section_id))
        require(text(self.course_title) and text(self.section_title) and bool(self.videos))
        require(type(self.question_count) is int and 3 <= self.question_count <= 5)
        ids = [v.video_id for v in self.videos]
        require(len(ids) == len(set(ids)), "DUPLICATE_VIDEO")
        require(all(integer(v) for v in self.expected_video_ids))
        require(len(self.expected_video_ids) == len(set(self.expected_video_ids)))
        require(set(ids) == set(self.expected_video_ids), "SECTION_NOT_READY")
        require(all(v.section_id == self.section_id for v in self.videos), "SECTION_MISMATCH")
        require(
            len({v.sort_order for v in self.videos}) == len(self.videos), "AMBIGUOUS_VIDEO_ORDER"
        )
        require(
            all(isinstance(x, str) for x in (self.course_description, self.difficulty, self.topic))
        )
        require(
            isinstance(self.category_code, str) and isinstance(self.category_name, str),
            "INVALID_CATEGORY",
        )
        require(self.topic_id is None or integer(self.topic_id), "INVALID_TOPIC")

    def payload(self):
        data = asdict(self)
        data["videos"] = [asdict(v) for v in sorted(self.videos, key=lambda v: v.sort_order)]
        data["expected_video_ids"] = sorted(self.expected_video_ids)
        return data

    def fingerprint(self, payload=None):
        # 생성·평가에 전달한 강좌 정보와 대본 전체가 같은지 확인한다.
        raw = json.dumps(
            self.payload() if payload is None else payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class Choice:
    choice_no: int
    content: str


@dataclass(frozen=True)
class Question:
    order_no: int
    content: str
    choices: tuple[Choice, ...]
    correct_choice_no: int
    explanation: str


@dataclass(frozen=True)
class QuizResult:
    course_id: int
    section_id: int
    input_fingerprint: str
    questions: tuple[Question, ...]
    quiz_type: str = "UNIT"
    model: str = ""
    prompt_version: str = "unit-quiz-v1"

    def rows(self):
        """Nested insert plan; real IDs and atomic persistence belong to backend."""
        return {
            "quiz": {
                "course_id": self.course_id,
                "section_id": self.section_id,
                "quiz_type": "UNIT",
                "ai_status": "COMPLETED",
                "is_active": True,
                "reviewed_by": None,
                "reviewed_at": None,
            },
            "questions": [
                {
                    "question": {
                        "order_no": q.order_no,
                        "content": q.content,
                        "correct_choice_no": q.correct_choice_no,
                        "explanation": q.explanation,
                        "generated_by": "AI",
                        "is_active": True,
                    },
                    "choices": [asdict(c) for c in q.choices],
                }
                for q in self.questions
            ],
        }


def section_from_dict(data):
    data = dict(data)
    videos = []
    for raw in data.pop("videos"):
        item = dict(raw)
        tr = item.pop("transcript")
        item["transcript"] = Transcript(tr["video_id"], tuple(Segment(**s) for s in tr["segments"]))
        videos.append(SectionVideo(**item))
    data["videos"] = tuple(videos)
    data["expected_video_ids"] = tuple(data["expected_video_ids"])
    return SectionInput(**data)
