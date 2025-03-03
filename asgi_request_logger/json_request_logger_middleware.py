import logging
import time
import uuid
import json
from asgiref.typing import (
    ASGI3Application,
    ASGIReceiveCallable,
    ASGISendCallable,
    Scope,
    ASGISendEvent,
)

class JsonRequestLoggerMiddleware:
    def __init__(
        self,
        app: ASGI3Application,
        error_info_name: str = "error_info",
        error_info_mapping: dict[str, str] | None = None,
        event_id_header: str | None = None,
        client_ip_headers: list[str] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """
        Initializes the JSON Request Logger Middleware.

        Args:
            app (ASGI3Application): The ASGI application instance to wrap.
            error_info_name (str, optional): The key name in the request state from which to extract error information.
                Defaults to "error_info".
            error_info_mapping (dict[str, str] | None, optional): A dictionary mapping error information keys (from the request
                state) to desired log field names. For example, {"code": "error_code", "message": "error_message"}.
                Defaults to None.
            event_id_header (str | None, optional): The HTTP header name to extract an event ID from. If not provided or if the header
                is missing, a new UUID will be generated. Defaults to None.
            client_ip_headers (list[str] | None, optional): A list of HTTP header names to check for the client IP address,
                in order of priority. If none are provided, the client IP will be obtained from the scope's "client" value.
                Defaults to None.
            logger (logging.Logger | None, optional): A custom logger to use for logging requests. If not provided, a default
                logger with INFO level is created. Defaults to None.
        """
        self.app = app
        self.error_info_name = error_info_name
        self.error_info_mapping = error_info_mapping or {
            "code": "error_code",
            "message": "error_message",
            "stack_trace": "stack_trace",
        }
        self.event_id_header = event_id_header.lower() if event_id_header else None
        self.client_ip_headers = [h.lower() for h in (client_ip_headers or ["x-forwarded-for", "x-real-ip"])]

        if logger:
            self.logger = logger
        else:
            logger = logging.getLogger("request-logger")
            logger.setLevel(logging.INFO)
            if logger.hasHandlers():
                logger.handlers.clear()
            stream_handler = logging.StreamHandler()
            stream_handler.setLevel(logging.INFO)
            formatter = logging.Formatter("%(message)s")
            stream_handler.setFormatter(formatter)
            logger.addHandler(stream_handler)
            logger.propagate = False
            self.logger = logger

    async def __call__(
        self,
        scope: Scope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        start_time = time.time()

        # Parse headers and convert all keys to lowercase for case-insensitive lookup.
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}

        # Determine event ID from header (case-insensitive) or generate a new UUID.
        if self.event_id_header and self.event_id_header in headers:
            event_id = headers[self.event_id_header]
        else:
            event_id = str(uuid.uuid4())

        # Extract client IP from the specified headers (case-insensitive lookup).
        client_ip = None
        for header in self.client_ip_headers:
            if header in headers:
                client_ip = headers[header].split(",")[0].strip()
                break
        if not client_ip:
            client_ip = scope.get("client", ("unknown",))[0]

        # Default log data
        log_data = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.%fZ", time.gmtime()),
            "event_id": event_id,
            "method": scope.get("method"),
            "path": scope.get("path"),
            "client_ip": client_ip,
            "user_agent": headers.get("user-agent"),
        }

        response_status_code: int | None = None

        async def send_wrapper(message: ASGISendEvent) -> None:
            nonlocal response_status_code
            if message["type"] == "http.response.start":
                response_status_code = message.get("status")
            await send(message)

        await self.app(scope, receive, send_wrapper)

        time_taken_ms = int((time.time() - start_time) * 1000)

        if response_status_code is None:
            response_status_code = 500  # Default to 500 if no response status found
        log_type = "access" if response_status_code < 400 else "error"
        log_level = "ERROR" if response_status_code >= 400 else "INFO"

        # Get error info from state (if available)
        error_info = scope.get("state", {}).get(self.error_info_name, None)
        if not error_info:
            log_data.update({"error": None})
        else:
            log_data.update({"error": {}})
            for src_key, dest_key in self.error_info_mapping.items():
                log_data["error"][dest_key] = error_info.get(src_key)

        # Update log data with additional fields
        log_data.update({
            "time_taken_ms": time_taken_ms,
            "status_code": response_status_code,
            "log_type": log_type,
            "level": log_level,
        })

        log_level_int = logging.getLevelNamesMapping()[log_level]
        self.logger.log(log_level_int, json.dumps(log_data, ensure_ascii=False))
