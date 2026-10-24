"""Local multilingual Whisper small. No API key or external inference requests."""

import wave
from pathlib import Path
from threading import Lock

from .errors import ProcessingError


class LocalWhisperSmall:
    def __init__(self, model_dir=".models/whisper", device="cpu", cpu_threads=4):
        if device not in ("cpu", "cuda") or not 1 <= cpu_threads <= 32:
            raise ProcessingError("INVALID_LOCAL_STT_CONFIG")
        self.model_dir = Path(model_dir).resolve()
        self.device, self.cpu_threads = device, cpu_threads
        self._model = None
        self._lock = Lock()

    def _load(self):
        if self._model is None:
            try:
                import torch
                import whisper
            except ImportError:
                raise ProcessingError("LOCAL_WHISPER_NOT_INSTALLED") from None
            if self.device == "cuda" and not torch.cuda.is_available():
                raise ProcessingError("CUDA_UNAVAILABLE")
            torch.set_num_threads(self.cpu_threads)
            self.model_dir.mkdir(parents=True, exist_ok=True)
            # Official loader downloads once, verifies SHA256, then reuses the checkpoint.
            self._model = whisper.load_model(
                "small", device=self.device, download_root=str(self.model_dir)
            )

    def prepare(self):
        with self._lock:
            self._load()

    def transcribe(self, audio, language):
        with self._lock:
            self._load()
            import numpy as np

            # AudioExtractor already produces 16 kHz mono PCM16; do not invoke another ffmpeg.
            with wave.open(str(audio), "rb") as wav:
                if (wav.getnchannels(), wav.getframerate(), wav.getsampwidth()) != (1, 16000, 2):
                    raise ProcessingError("INVALID_LOCAL_AUDIO_FORMAT")
                samples = (
                    np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32)
                    / 32768.0
                )
            return self._model.transcribe(
                samples,
                language=language,
                task="transcribe",
                fp16=self.device == "cuda",
                verbose=None,
                temperature=0.0,
                beam_size=5,
                condition_on_previous_text=False,
            )
