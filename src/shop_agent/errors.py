from typing import Literal


ErrorCode = Literal[
    "INTENT_PARSE_FAILED",
    "EVIDENCE_PARSE_FAILED",
    "EMBEDDING_UNAVAILABLE",
    "RETRIEVAL_UNAVAILABLE",
    "RERANK_UNAVAILABLE",
    "GENERATION_FAILED",
    "INTERNAL_ERROR",
]


class ServiceError(RuntimeError):
    def __init__(self, code: ErrorCode, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def to_payload(self) -> dict[str, str | bool]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
