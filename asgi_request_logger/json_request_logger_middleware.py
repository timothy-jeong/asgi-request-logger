import logging
import time
import uuid
import json
from typing import Optional, Dict, List
from asgiref.typing import ASGI3Application, ASGIReceiveCallable, ASGISendCallable, Scope, ASGISendEvent

_default_error_info_mapping = {
    "code": "error_code",
    "message": "error_message",
    "stack_trace": "stack_trace",
}

_default_log_info_mapping = {
    "method": "method",
    "path": "path",
    "client_ip": "client_ip",
    "user_agent": "user_agent",
}

class JsonRequestLoggerMiddleware:
    def __init__(
        self,
        app: ASGI3Application,
        logger: logging.Logger,
        log_info_mapping: Optional[Dict[str, str]] = _default_log_info_mapping,        
        error_info_name: str = "error_info",
        error_info_mapping: Optional[Dict[str, str]] = _default_error_info_mapping,
        event_id_header: Optional[str] = None,
        client_ip_headers: Optional[List[str]] = None,
    ) -> None:
        """
        Initializes the JSON Request Logger Middleware.

        Args:
            app (ASGI3Application): The ASGI application instance to wrap.
            error_info_name (str, optional): The key in the request state from which to extract error information.
                Defaults to "error_info".
            error_info_mapping (Optional[Dict[str, str]], optional): A dictionary mapping error information keys
                (from the request state) to desired log field names. For example, {"code": "error_code",
                "message": "error_message", "stack_trace": "stack_trace"}. Defaults to _default_error_info_mapping.
            event_id_header (Optional[str], optional): The HTTP header name to extract an event ID from.
                If not provided or missing, a new UUID is generated. Defaults to None.
            client_ip_headers (Optional[List[str]], optional): A list of HTTP header names to check for the client IP.
                If none are provided, the client IP will be obtained from the scope's "client" value.
                Defaults to ["x-forwarded-for", "x-real-ip"].
            logger (logging.Logger], optional): A custom logger for logging requests.
                If not provided, a default logger with INFO level is created and configured to use a QueueHandler.
            log_info_mapping (Optional[Dict[str, str]], optional): A dictionary mapping keys from the ASGI scope or headers
                to additional fields in the JSON log output. For example, {"method": "method", "path": "path",
                "client_ip": "client_ip", "user_agent": "user_agent"}. Defaults to _default_log_info_mapping.
        """
        self.app = app
        self.error_info_name = error_info_name
        self.error_info_mapping = error_info_mapping
        self.event_id_header = event_id_header.lower() if event_id_header else None
        self.client_ip_headers = [h.lower() for h in (client_ip_headers or ["x-forwarded-for", "x-real-ip"])]
        
        self.log_info_mapping = log_info_mapping
        
        if logger is None:
            raise ValueError("A logger must be provided")

    async def __call__(
        self,
        scope: Scope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        start_time = time.time()

        # Convert headers to a dictionary with lowercase keys.
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}

        # Determine event ID from header or generate a new UUID.
        if self.event_id_header and self.event_id_header in headers:
            event_id = headers[self.event_id_header]
        else:
            event_id = str(uuid.uuid4())

        # Extract client IP from headers.
        client_ip = None
        for header in self.client_ip_headers:
            if header in headers:
                client_ip = headers[header].split(",")[0].strip()
                break
        if not client_ip:
            client_ip = scope.get("client", ("unknown",))[0]

        # Build the default log data with computed fields.
        log_data = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.%fZ", time.gmtime()),
            "event_id": event_id,
        }
        # Populate log_data using log_info_mapping.
        for source_key, dest_key in self.log_info_mapping.items():
            value = headers.get(source_key.lower())
            if value is None:
                value = scope.get(source_key)
            if value is not None:
                log_data[dest_key] = value

        response_status_code: Optional[int] = None

        async def send_wrapper(message: ASGISendEvent) -> None:
            nonlocal response_status_code
            if message["type"] == "http.response.start":
                response_status_code = message.get("status")
            await send(message)

        await self.app(scope, receive, send_wrapper)
        time_taken_ms = int((time.time() - start_time) * 1000)

        if response_status_code is None:
            response_status_code = 500
        log_type = "access" if response_status_code < 400 else "error"
        log_level = "ERROR" if response_status_code >= 400 else "INFO"

        # Process error info from scope state.
        error_info = scope.get("state", {}).get(self.error_info_name, None)
        if error_info is None:
            log_data["error"] = None
        else:
            log_data["error"] = {}
            for src_key, dest_key in self.error_info_mapping.items():
                log_data["error"][dest_key] = error_info.get(src_key)

        log_data.update({
            "time_taken_ms": time_taken_ms,
            "status_code": response_status_code,
            "log_type": log_type,
            "level": log_level,
        })

        # Use a fallback for logging.getLevelNamesMapping if not available.
        try:
            level_mapping = logging.getLevelNamesMapping()
        except AttributeError:
            level_mapping = {
                "DEBUG": logging.DEBUG,
                "INFO": logging.INFO,
                "WARNING": logging.WARNING,
                "ERROR": logging.ERROR,
                "CRITICAL": logging.CRITICAL,
            }
        log_level_int = level_mapping.get(log_level, logging.INFO)
        self.logger.log(log_level_int, json.dumps(log_data, ensure_ascii=False))