"""Structured, application-side event logging and HTTP trace middleware.

The app deliberately keeps the audit stream in a regular file as well as the
process log stream.  The file uses JSON Lines so operators can search or ship
individual events without parsing human-oriented console output.
"""

from __future__ import annotations

import hashlib
import json
import logging
import logging.handlers
import os
import sys
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import date, datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Iterator

from starlette.types import ASGIApp, Message, Receive, Scope, Send


REQUEST_ID = ContextVar("hangout_request_id", default="-")

_CONFIG_LOCK = threading.RLock()
_FILE_HANDLER_MARKER = "_hangout_audit_file_handler"
_CONSOLE_HANDLER_MARKER = "_hangout_audit_console_handler"
_FILE_LOGGERS = ("", "uvicorn", "uvicorn.access")
_SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
    "x-twilio-signature",
    "cf-access-jwt-assertion",
    "cf-access-client-id",
    "cf-access-client-secret",
}
_TEXT_CONTENT_TYPES = (
    "application/json",
    "application/javascript",
    "application/x-www-form-urlencoded",
    "application/xml",
    "text/",
)


def _json_safe(value: Any) -> Any:
    """Convert common application values into JSON-safe audit data."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    value_value = getattr(value, "value", None)
    if value_value is not None and value_value is not value:
        return _json_safe(value_value)
    return str(value)


class PrivateRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Rotating file handler that keeps sensitive audit files owner-readable."""

    def _open(self):  # type: ignore[no-untyped-def]
        stream = super()._open()
        try:
            os.chmod(self.baseFilename, 0o600)
        except OSError:
            # The handler is still useful on filesystems that do not support
            # chmod (for example, some mounted development filesystems).
            pass
        return stream


class ContextFilter(logging.Filter):
    """Attach an event ID and the current request/job ID to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "event_id", None):
            record.event_id = uuid.uuid4().hex
        if not getattr(record, "request_id", None):
            record.request_id = REQUEST_ID.get()
        return True


class JsonEventFormatter(logging.Formatter):
    """One complete structured event per line."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, timezone.utc).isoformat(
            timespec="milliseconds"
        )
        payload: dict[str, Any] = {
            "timestamp": timestamp,
            "event_id": getattr(record, "event_id", uuid.uuid4().hex),
            "request_id": getattr(record, "request_id", REQUEST_ID.get()),
            "level": record.levelname,
            "logger": record.name,
            "process_id": record.process,
            "thread": record.threadName,
            "event": getattr(record, "event_name", None) or "log",
            "message": record.getMessage(),
        }
        event_data = getattr(record, "event_data", None)
        if event_data is not None:
            payload["data"] = _json_safe(event_data)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


class ConsoleEventFormatter(logging.Formatter):
    """Readable format for local terminals and the systemd journal."""

    def __init__(self) -> None:
        super().__init__(
            "%(asctime)s %(levelname)s [%(name)s] "
            "[event_id=%(event_id)s request_id=%(request_id)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )


def _owned_handlers() -> set[logging.Handler]:
    handlers: set[logging.Handler] = set()
    for logger_name in _FILE_LOGGERS:
        logger = logging.getLogger(logger_name)
        handlers.update(
            handler for handler in logger.handlers if getattr(handler, _FILE_HANDLER_MARKER, False)
        )
    return handlers


def configure_logging(settings: Any) -> None:
    """Configure console/journal and rotating JSONL file logging.

    The function is idempotent and replaces a previous audit file handler if a
    test or a process supervisor supplies a different path during startup.
    """

    log_path = Path(settings.log_file).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not log_path.parent.is_symlink():
        try:
            if not log_path.parent.exists():
                log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError:
            pass

    level_name = str(getattr(settings, "log_level", "INFO") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    if not isinstance(level, int):
        level = logging.INFO

    with _CONFIG_LOCK:
        root = logging.getLogger()
        root.setLevel(level)

        existing_file_handlers = _owned_handlers()
        for handler in existing_file_handlers:
            handler_path = getattr(handler, "baseFilename", None)
            if handler_path != str(log_path.resolve()):
                for logger_name in _FILE_LOGGERS:
                    logging.getLogger(logger_name).removeHandler(handler)
                handler.close()

        file_handler = next(
            (
                handler
                for handler in _owned_handlers()
                if getattr(handler, "baseFilename", None) == str(log_path.resolve())
            ),
            None,
        )
        if file_handler is None:
            file_handler = PrivateRotatingFileHandler(
                log_path,
                maxBytes=max(1, int(settings.log_max_bytes)),
                backupCount=max(0, int(settings.log_backup_count)),
                encoding="utf-8",
            )
            setattr(file_handler, _FILE_HANDLER_MARKER, True)
            file_handler.setFormatter(JsonEventFormatter())
            file_handler.addFilter(ContextFilter())
            root.addHandler(file_handler)
            logging.getLogger("uvicorn").addHandler(file_handler)
            # Uvicorn intentionally disables propagation for access logs, so
            # attach the same file handler directly to retain its access lines.
            logging.getLogger("uvicorn.access").addHandler(file_handler)
        else:
            file_handler.setLevel(level)
            file_handler.setFormatter(JsonEventFormatter())

        file_handler.setLevel(level)
        if not any(isinstance(item, ContextFilter) for item in file_handler.filters):
            file_handler.addFilter(ContextFilter())
        for logger_name in _FILE_LOGGERS:
            logger = logging.getLogger(logger_name)
            if file_handler not in logger.handlers:
                logger.addHandler(file_handler)

        if not any(getattr(handler, _CONSOLE_HANDLER_MARKER, False) for handler in root.handlers):
            console_handler = logging.StreamHandler(sys.stderr)
            setattr(console_handler, _CONSOLE_HANDLER_MARKER, True)
            console_handler.setLevel(level)
            console_handler.setFormatter(ConsoleEventFormatter())
            console_handler.addFilter(ContextFilter())
            root.addHandler(console_handler)
        else:
            for handler in root.handlers:
                if getattr(handler, _CONSOLE_HANDLER_MARKER, False):
                    handler.setLevel(level)

        for logger_name in ("uvicorn", "uvicorn.access"):
            logging.getLogger(logger_name).setLevel(level)


def new_request_id() -> str:
    return uuid.uuid4().hex


def current_request_id() -> str:
    return REQUEST_ID.get()


@contextmanager
def request_context(request_id: str | None = None) -> Iterator[str]:
    """Set a trace ID for a request or background operation."""

    value = request_id or new_request_id()
    token = REQUEST_ID.set(value)
    try:
        yield value
    finally:
        REQUEST_ID.reset(token)


def audit_event(
    event_name: str,
    *,
    level: int = logging.INFO,
    exc_info: Any = None,
    **details: Any,
) -> str:
    """Write a structured application event and return its event ID."""

    event_id = uuid.uuid4().hex
    logging.getLogger("hangout.audit").log(
        level,
        event_name,
        extra={
            "event_id": event_id,
            "event_name": event_name,
            "event_data": details,
        },
        exc_info=exc_info,
    )
    return event_id


def _header_map(headers: list[tuple[bytes, bytes]]) -> dict[str, str]:
    values: dict[str, list[str]] = {}
    for raw_name, raw_value in headers:
        name = raw_name.decode("latin-1").lower()
        value = raw_value.decode("latin-1")
        values.setdefault(name, []).append(value)
    return {name: ", ".join(items) for name, items in values.items()}


def _safe_headers(headers: dict[str, str]) -> dict[str, str]:
    return {name: value for name, value in headers.items() if name not in _SENSITIVE_HEADERS}


def _scope_request_data(scope: Scope) -> dict[str, Any]:
    headers = _header_map(scope.get("headers", []))
    client = scope.get("client") or (None, None)
    server = scope.get("server") or (None, None)
    query_string = scope.get("query_string", b"").decode("latin-1")
    return {
        "method": scope.get("method"),
        "path": scope.get("path"),
        "query_string": query_string,
        "scheme": scope.get("scheme"),
        "http_version": scope.get("http_version"),
        "client_host": client[0],
        "client_port": client[1],
        "server_host": server[0],
        "server_port": server[1],
        "source": {
            "direct_client": client[0],
            "x_forwarded_for": headers.get("x-forwarded-for"),
            "x_real_ip": headers.get("x-real-ip"),
            "cf_connecting_ip": headers.get("cf-connecting-ip"),
            "cf_ipcountry": headers.get("cf-ipcountry"),
            "cf_ray": headers.get("cf-ray"),
            "access_identity": headers.get("cf-access-authenticated-user-email"),
        },
        "user_agent": headers.get("user-agent"),
        "referer": headers.get("referer"),
        "host": headers.get("host"),
        "content_type": headers.get("content-type"),
        "headers": _safe_headers(headers),
        "sensitive_header_names": sorted(name for name in headers if name in _SENSITIVE_HEADERS),
    }


class BodyCapture:
    """Keep a bounded readable body sample while hashing the complete body."""

    def __init__(self, limit: int) -> None:
        self.limit = max(0, limit)
        self.total_bytes = 0
        self._sample = bytearray()
        self._hash = hashlib.sha256()

    def add(self, body: bytes) -> None:
        if not body:
            return
        self.total_bytes += len(body)
        self._hash.update(body)
        remaining = self.limit - len(self._sample)
        if remaining > 0:
            self._sample.extend(body[:remaining])

    @property
    def truncated(self) -> bool:
        return self.total_bytes > self.limit

    def fields(self, content_type: str | None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "bytes": self.total_bytes,
            "sha256": self._hash.hexdigest(),
            "truncated": self.truncated,
        }
        if not self._sample:
            return result

        media_type = (content_type or "").split(";", 1)[0].strip().lower()
        is_text = media_type.startswith(_TEXT_CONTENT_TYPES)
        if is_text or not media_type:
            try:
                value = bytes(self._sample).decode("utf-8")
            except UnicodeDecodeError:
                value = bytes(self._sample).decode("utf-8", "replace")
                result["encoding"] = "utf-8-replaced"
            else:
                result["encoding"] = "utf-8"
            result["body"] = value
        else:
            result["encoding"] = "binary"
            result["body_omitted"] = True
        return result


class EventTraceMiddleware:
    """Capture every HTTP request/response with source and trace metadata."""

    def __init__(self, app: ASGIApp, *, body_limit: int = 262_144) -> None:
        self.app = app
        self.body_limit = body_limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request_id = new_request_id()
        token: Token[str] = REQUEST_ID.set(request_id)
        started = perf_counter()
        request_data = _scope_request_data(scope)
        request_body = BodyCapture(self.body_limit)
        response_body = BodyCapture(self.body_limit)
        response_status: int | None = None
        response_headers: dict[str, str] = {}
        response_started = False
        terminal_logged = False

        audit_event("http.request.received", **request_data)

        async def receive_wrapper() -> Message:
            message = await receive()
            if message.get("type") == "http.request":
                request_body.add(message.get("body", b""))
            return message

        async def send_wrapper(message: Message) -> None:
            nonlocal response_status, response_headers, response_started, terminal_logged
            if message.get("type") == "http.response.start":
                response_started = True
                response_status = int(message.get("status", 0))
                response_headers = _header_map(message.get("headers", []))
                headers = list(message.get("headers", []))
                if not any(name.lower() == b"x-request-id" for name, _ in headers):
                    headers.append((b"x-request-id", request_id.encode("ascii")))
                    response_headers["x-request-id"] = request_id
                    message = {**message, "headers": headers}

            if message.get("type") == "http.response.body":
                response_body.add(message.get("body", b""))

            await send(message)

            if (
                message.get("type") == "http.response.body"
                and not message.get("more_body", False)
                and not terminal_logged
            ):
                terminal_logged = True
                audit_event(
                    "http.request.completed",
                    **request_data,
                    route_path=getattr(scope.get("route"), "path", None),
                    route_name=getattr(scope.get("route"), "name", None),
                    duration_ms=round((perf_counter() - started) * 1000, 3),
                    request_body=request_body.fields(request_data.get("content_type")),
                    response={
                        "status_code": response_status,
                        "headers": _safe_headers(response_headers),
                        "sensitive_header_names": sorted(
                            name for name in response_headers if name in _SENSITIVE_HEADERS
                        ),
                        "body": response_body.fields(response_headers.get("content-type")),
                    },
                )

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except BaseException as exc:
            if not terminal_logged:
                terminal_logged = True
                audit_event(
                    "http.request.failed",
                    level=logging.ERROR,
                    exc_info=True,
                    **request_data,
                    route_path=getattr(scope.get("route"), "path", None),
                    route_name=getattr(scope.get("route"), "name", None),
                    duration_ms=round((perf_counter() - started) * 1000, 3),
                    request_body=request_body.fields(request_data.get("content_type")),
                    response_status=response_status,
                    response_started=response_started,
                    exception_type=type(exc).__name__,
                    exception_message=str(exc),
                )
            raise
        finally:
            if not terminal_logged:
                audit_event(
                    "http.request.terminated",
                    level=logging.WARNING,
                    **request_data,
                    duration_ms=round((perf_counter() - started) * 1000, 3),
                    request_body=request_body.fields(request_data.get("content_type")),
                    response_status=response_status,
                    response_started=response_started,
                )
            REQUEST_ID.reset(token)
