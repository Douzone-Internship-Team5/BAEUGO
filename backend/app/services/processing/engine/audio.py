import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath

from .errors import ProcessingError


class AudioExtractor:
    def __init__(self, media_root, ffmpeg="ffmpeg", timeout=180):
        self.root = Path(media_root).resolve()
        self.ffmpeg = ffmpeg
        self.timeout = timeout

    def resolve(self, key):
        # Keys are portable POSIX relative paths, never host paths or URLs.
        if (
            not key
            or "\\" in key
            or ":" in key
            or PureWindowsPath(key).drive
            or Path(key).is_absolute()
        ):
            raise ProcessingError("INVALID_STORAGE_KEY")
        if any(part in ("..", ".", "") for part in key.split("/")):
            raise ProcessingError("INVALID_STORAGE_KEY")
        target = (self.root / key).resolve()
        if not target.is_relative_to(self.root) or not target.is_file():
            raise ProcessingError("MEDIA_NOT_FOUND")
        return target

    @contextmanager
    def extract(self, key):
        source = self.resolve(key)
        with tempfile.TemporaryDirectory(prefix="baeugo-stt-") as temp:
            audio = Path(temp) / "audio.wav"
            try:
                subprocess.run(
                    [
                        self.ffmpeg,
                        "-nostdin",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-i",
                        str(source),
                        "-map",
                        "0:a:0",
                        "-vn",
                        "-ac",
                        "1",
                        "-ar",
                        "16000",
                        "-c:a",
                        "pcm_s16le",
                        str(audio),
                    ],
                    check=True,
                    capture_output=True,
                    timeout=self.timeout,
                )
            except FileNotFoundError:
                raise ProcessingError("FFMPEG_NOT_FOUND") from None
            except subprocess.TimeoutExpired:
                raise ProcessingError("AUDIO_TIMEOUT", True) from None
            except subprocess.CalledProcessError:
                raise ProcessingError("AUDIO_EXTRACTION_FAILED") from None
            if not audio.exists() or audio.stat().st_size <= 44:
                raise ProcessingError("EMPTY_AUDIO")
            yield audio
