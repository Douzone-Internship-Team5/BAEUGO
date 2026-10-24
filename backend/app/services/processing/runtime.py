from functools import lru_cache

from app.core.config import get_settings
from app.services.processing.engine.audio import AudioExtractor
from app.services.processing.engine.correction import CorrectionService
from app.services.processing.engine.local_whisper import LocalWhisperSmall
from app.services.processing.engine.providers import GeminiAPI
from app.services.processing.engine.quiz import QuizService
from app.services.processing.engine.quiz_review import QuizReviewService
from app.services.processing.engine.stt import STTService


@lru_cache
def get_stt_service() -> STTService:
    settings = get_settings()
    return STTService(
        AudioExtractor(settings.media_root),
        LocalWhisperSmall(
            settings.whisper_model_dir, settings.whisper_device, settings.whisper_cpu_threads
        ),
    )


def get_gemini_provider(*, review: bool = False) -> GeminiAPI:
    settings = get_settings()
    model = (
        settings.gemini_review_model
        if review and settings.gemini_review_model
        else settings.gemini_model
    )
    return GeminiAPI(settings.gemini_api_key, model)


def get_correction_service() -> CorrectionService:
    return CorrectionService(get_gemini_provider())


def get_quiz_service() -> QuizService:
    return QuizService(get_gemini_provider())


def get_review_service() -> QuizReviewService:
    return QuizReviewService(get_gemini_provider(review=True))
