"""Independent STT and section quiz services; no import-time API calls."""

from .quiz import QuizService
from .schemas import QuizResult, SectionInput, SectionVideo, Segment, Transcript, VideoInput
from .stt import STTService

__all__ = [
    "Segment",
    "Transcript",
    "VideoInput",
    "SectionVideo",
    "SectionInput",
    "QuizResult",
    "STTService",
    "QuizService",
]
