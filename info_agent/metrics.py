"""Count SDK HTTP attempts separately from logical selection calls."""
from contextvars import ContextVar

attempts = ContextVar('info_agent_http_attempts', default=None)


def record_request(request):
    counter = attempts.get()
    if counter is not None:
        counter['http_attempts'] += 1
