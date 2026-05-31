import time
from pathlib import Path
from threading import Lock

from django.conf import settings
from django.utils import timezone


class PageAccessLogMiddleware:
    _lock = Lock()

    def __init__(self, get_response):
        self.get_response = get_response
        self.log_file = Path(settings.BASE_DIR) / "logs" / "page_access.log"
        self.excluded_prefixes = self._excluded_prefixes()

    def __call__(self, request):
        started_at = time.perf_counter()
        response = self.get_response(request)
        self._log_response(request, response, started_at)
        return response

    def _log_response(self, request, response, started_at):
        if self._should_skip(request.path):
            return

        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        auth_state = "authenticated" if request.user.is_authenticated else "anonymous"
        timestamp = timezone.now().isoformat()
        line = (
            f"{timestamp} method={request.method} path={request.path} "
            f"status={response.status_code} duration_ms={elapsed_ms} auth={auth_state}\n"
        )

        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self.log_file.open("a", encoding="utf-8") as log:
                log.write(line)

    def _should_skip(self, path):
        return path == "/favicon.ico" or path.startswith(self.excluded_prefixes)

    def _excluded_prefixes(self):
        prefixes = [
            getattr(settings, "STATIC_URL", ""),
            getattr(settings, "MEDIA_URL", ""),
        ]
        normalized_prefixes = []
        for prefix in prefixes:
            if not prefix:
                continue

            normalized_prefix = self._normalize_prefix(prefix)
            if normalized_prefix != "/":
                normalized_prefixes.append(normalized_prefix)

        return tuple(normalized_prefixes)

    def _normalize_prefix(self, prefix):
        if not prefix.startswith("/"):
            prefix = f"/{prefix}"
        return prefix
