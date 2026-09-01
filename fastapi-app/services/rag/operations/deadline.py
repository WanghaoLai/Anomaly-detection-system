"""RAG 请求总截止时间的稳定错误语义。"""


class RagRequestDeadlineExceeded(RuntimeError):
    code = "request_deadline_exceeded"


__all__ = ["RagRequestDeadlineExceeded"]
