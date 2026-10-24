class ProcessingError(Exception):
    """Safe error code; provider responses and credentials are never exposed."""

    def __init__(self, code: str, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(code)
