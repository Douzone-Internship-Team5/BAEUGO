import asyncio
import math

from .errors import ProcessingError
from .schemas import Segment, Transcript


class STTService:
    def __init__(self, extractor, provider):
        self.extractor, self.provider = extractor, provider

    def transcribe(self, request):
        with self.extractor.extract(request.video_storage_key) as audio:
            raw = self.provider.transcribe(audio, request.language)
        try:
            segments = []
            for item in raw["segments"]:
                content = item["text"].strip()
                if not content:
                    continue
                start, end = item["start"], item["end"]
                if any(type(t) not in (int, float) or not math.isfinite(t) for t in (start, end)):
                    raise ValueError()
                start_ms, end_ms = round(start * 1000), round(end * 1000)
                if end_ms > (request.duration_seconds + 1) * 1000:
                    raise ValueError()
                segments.append(Segment(len(segments) + 1, start_ms, end_ms, content))
            return Transcript(request.video_id, tuple(segments))
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ProcessingError("INVALID_STT_RESPONSE") from None

    async def transcribe_async(self, request):
        return await asyncio.to_thread(self.transcribe, request)
