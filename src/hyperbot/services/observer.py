"""Read-only HTTP API and dashboard server for HyperBot operations."""

from __future__ import annotations

import base64
import json
import secrets
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import parse_qs, urlsplit

from hyperbot.observability import ObservabilityReader, ObserverSettings


def _parse_basic_auth(header_value: str) -> tuple[str, str] | None:
    scheme, separator, token = header_value.partition(" ")
    if separator != " " or scheme.lower() != "basic" or not token.strip():
        return None
    try:
        decoded = base64.b64decode(token.strip(), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    username, separator, password = decoded.partition(":")
    if separator != ":":
        return None
    return username, password


def _auth_matches(
    header_value: str,
    credentials: tuple[str, str],
) -> bool:
    parsed = _parse_basic_auth(header_value)
    if parsed is None:
        return False
    username, password = parsed
    expected_username, expected_password = credentials
    return secrets.compare_digest(
        username, expected_username
    ) and secrets.compare_digest(password, expected_password)


def dashboard_html() -> str:
    return files("hyperbot.ui").joinpath("dashboard.html").read_text(encoding="utf-8")


def build_observer_handler(
    reader: ObservabilityReader,
    settings: ObserverSettings,
) -> type[BaseHTTPRequestHandler]:
    credentials = settings.credentials
    html = dashboard_html().replace(
        "__HYPERBOT_REFRESH_MS__",
        str(settings.refresh_seconds * 1_000),
    )

    class HyperBotObserverHandler(BaseHTTPRequestHandler):
        server_version = "HyperBotObserver/1"
        sys_version = ""

        def do_GET(self) -> None:  # noqa: N802
            self._handle_get(head_only=False)

        def do_HEAD(self) -> None:  # noqa: N802
            self._handle_get(head_only=True)

        def do_POST(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_PUT(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_PATCH(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_DELETE(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def log_message(self, format: str, *args: object) -> None:
            del format, args

        def _handle_get(self, *, head_only: bool) -> None:
            parsed = urlsplit(self.path)
            path = parsed.path
            if path == "/health":
                self._send_json(
                    HTTPStatus.OK,
                    reader.service_health(),
                    head_only=head_only,
                )
                return
            if not self._require_auth(head_only=head_only):
                return
            if path in {"/", "/dashboard"}:
                self._send_html(HTTPStatus.OK, html, head_only=head_only)
                return
            if path == "/favicon.ico":
                self._write_response(
                    HTTPStatus.NO_CONTENT,
                    b"",
                    content_type="image/x-icon",
                    head_only=head_only,
                )
                return
            try:
                route = self._api_routes(parsed.query).get(path)
                if route is None:
                    self._send_json(
                        HTTPStatus.NOT_FOUND,
                        {"error": "not_found"},
                        head_only=head_only,
                    )
                    return
                self._send_json(HTTPStatus.OK, route(), head_only=head_only)
            except ValueError:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "invalid_query"},
                    head_only=head_only,
                )
            except Exception:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "internal_error"},
                    head_only=head_only,
                )

        def _api_routes(
            self,
            query: str,
        ) -> dict[str, Callable[[], dict[str, object]]]:
            parameters = parse_qs(query, keep_blank_values=True)
            raw_limit = parameters.get("limit", ["30"])[0]
            limit = int(raw_limit)
            if limit <= 0:
                raise ValueError("limit must be positive")
            return {
                "/api/overview": lambda: reader.overview(history_limit=limit),
                "/api/status": reader.status,
                "/api/markets": reader.markets,
                "/api/quality/latest": reader.quality_latest,
                "/api/quality/history": lambda: reader.quality_history(limit=limit),
                "/api/incidents": reader.incidents,
                "/api/storage": reader.storage,
                "/api/shadow": reader.shadow,
                "/api/config": reader.configuration,
                "/api/endpoints": lambda: {
                    "read_only": True,
                    "methods": ["GET", "HEAD"],
                    "control_endpoints_enabled": False,
                    "routes": [
                        "/health",
                        "/api/overview",
                        "/api/status",
                        "/api/markets",
                        "/api/quality/latest",
                        "/api/quality/history",
                        "/api/incidents",
                        "/api/storage",
                        "/api/shadow",
                        "/api/config",
                        "/api/endpoints",
                    ],
                },
            }

        def _require_auth(self, *, head_only: bool) -> bool:
            if credentials is None:
                return True
            if _auth_matches(self.headers.get("Authorization", ""), credentials):
                return True
            self._send_json(
                HTTPStatus.UNAUTHORIZED,
                {"error": "authentication_required"},
                headers={
                    "WWW-Authenticate": ('Basic realm="HyperBot UI", charset="UTF-8"')
                },
                head_only=head_only,
            )
            return False

        def _method_not_allowed(self) -> None:
            self._send_json(
                HTTPStatus.METHOD_NOT_ALLOWED,
                {"error": "read_only_api", "allowed_methods": ["GET", "HEAD"]},
                headers={"Allow": "GET, HEAD"},
                head_only=False,
            )

        def _send_json(
            self,
            status: HTTPStatus,
            payload: dict[str, object],
            *,
            headers: dict[str, str] | None = None,
            head_only: bool,
        ) -> None:
            body = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            self._write_response(
                status,
                body,
                content_type="application/json; charset=utf-8",
                headers=headers,
                head_only=head_only,
            )

        def _send_html(
            self,
            status: HTTPStatus,
            payload: str,
            *,
            head_only: bool,
        ) -> None:
            self._write_response(
                status,
                payload.encode("utf-8"),
                content_type="text/html; charset=utf-8",
                head_only=head_only,
            )

        def _write_response(
            self,
            status: HTTPStatus,
            body: bytes,
            *,
            content_type: str,
            head_only: bool,
            headers: dict[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; img-src 'self' data:; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            )
            for header_name, header_value in (headers or {}).items():
                self.send_header(header_name, header_value)
            self.end_headers()
            if head_only:
                return
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                return

    return HyperBotObserverHandler


class ObserverHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def run_observer_server(
    settings: ObserverSettings,
    reader: ObservabilityReader,
) -> None:
    server = ObserverHTTPServer(
        (settings.host, settings.port),
        build_observer_handler(reader, settings),
    )
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
