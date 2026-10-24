import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .errors import ProcessingError


class HTTPTransport:
    def __init__(self, timeout=120, attempts=3):
        if timeout <= 0 or not 1 <= attempts <= 3:
            raise ValueError("Invalid timeout/attempts")
        self.timeout, self.attempts = timeout, attempts

    def post(self, url, headers, body):
        for attempt in range(self.attempts):
            try:
                with urlopen(
                    Request(url, data=body, headers=headers, method="POST"), timeout=self.timeout
                ) as response:
                    result = json.loads(response.read())
                if not isinstance(result, dict):
                    raise ProcessingError("INVALID_PROVIDER_RESPONSE")
                return result
            except HTTPError as error:
                status = error.code
                error.close()
                failure = ProcessingError(
                    f"PROVIDER_HTTP_{status}", status in (408, 429, 500, 502, 503, 504)
                )
            except (URLError, TimeoutError):
                failure = ProcessingError("PROVIDER_NETWORK_ERROR", True)
            except (ValueError, UnicodeError):
                raise ProcessingError("INVALID_PROVIDER_JSON") from None
            if not failure.retryable or attempt + 1 == self.attempts:
                raise failure from None
            time.sleep(2**attempt)
