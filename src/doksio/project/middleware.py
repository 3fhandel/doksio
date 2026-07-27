from __future__ import annotations

from time import perf_counter


class RequestTimingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request._doksio_request_started_at = perf_counter()
        response = self.get_response(request)
        duration_ms = (
            perf_counter() - request._doksio_request_started_at
        ) * 1000
        response["Server-Timing"] = f"app;dur={duration_ms:.2f}"
        return response
