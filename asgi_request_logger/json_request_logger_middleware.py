import logging
import time
import uuid
import json
from datetime import datetime, timezone
from typing import Optional, Dict, List
from asgiref.typing import ASGI3Application, ASGIReceiveCallable, ASGISendCallable, Scope, ASGISendEvent

from asgi_request_logger.logger import get_logger

# Default mappings for error and log information.
_default_error_info_mapping = {
    "code": "error_code",
    "message": "error_message",
    "stack_trace": "stack_trace",
}

# Default mappings for log information.
_default_log_info_mapping = {
    "method": "method",
    "path": "path",
    "client_ip": "client_ip",
    "user_agent": "user_agent",
}

class JsonRequestLoggerMiddleware:
    """
    ASGI middleware for logging HTTP request access and error information in JSON format.
    
    Attributes:
        app (ASGI3Application): The ASGI application to wrap.
        logger (logging.Logger): The logger to use. If not provided, a default logger is used.
        log_info_mapping (Dict[str, str]): Mapping of keys from the ASGI scope or headers to additional log fields.
        error_info_name (str): The key from the request state to extract error information.
        error_info_mapping (Dict[str, str]): Mapping from error info keys to desired log field names.
        event_id_header (Optional[str]): HTTP header name to extract an event ID from.
        client_ip_headers (List[str]): List of HTTP header names to check for client IP.
        is_preflight (bool): Whether to log CORS preflight (OPTIONS) requests.
    """
    def __init__(
        self,
        app: ASGI3Application,
        logger: Optional[logging.Logger],
        log_info_mapping: Optional[Dict[str, str]] = _default_log_info_mapping,        
        error_info_name: str = "error_info",
        error_info_mapping: Optional[Dict[str, str]] = _default_error_info_mapping,
        event_id_header: Optional[str] = None,
        client_ip_headers: Optional[List[str]] = None,
        is_preflight: bool = False
    ) -> None:
        self.app = app
        self.logger = logger or get_logger()
        self.log_info_mapping = log_info_mapping
        self.error_info_name = error_info_name
        self.error_info_mapping = error_info_mapping
        self.event_id_header = event_id_header.lower() if event_id_header else None
        # Convert client IP header keys to lowercase for uniformity.
        self.client_ip_headers = [h.lower() for h in (client_ip_headers or ["x-forwarded-for", "x-real-ip"])]
        self.is_preflight = is_preflight
        
    async def __call__(
        self,
        scope: Scope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        # Bypass non-HTTP requests.
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        
        # Optionally skip logging for preflight OPTIONS requests.
        if not self.is_preflight and scope.get('method', '').lower() == 'option':
            return await self.app(scope, receive, send)
            
        # Record the start time of the request.
        start_time = time.time()

        # Decode ASGI scope headers and create a dictionary with lowercase keys.
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}

        # Extract event ID from the specified header or generate a new UUID.
        if self.event_id_header and self.event_id_header in headers:
            event_id = headers[self.event_id_header]
        else:
            event_id = str(uuid.uuid4())

        # Extract client IP from headers, falling back to scope's client value.
        client_ip = None
        for header in self.client_ip_headers:
            if header in headers:
                client_ip = headers[header].split(",")[0].strip()
                break
        if not client_ip:
            client_ip = scope.get("client", ("unknown",))[0]

        # Generate the current timestamp in ISO 8601 format (UTC).
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        # Build the base log data.
        log_data = {
            "timestamp": timestamp,
            "event_id": event_id,
        }
        # Populate additional log fields based on log_info_mapping.
        for source_key, dest_key in self.log_info_mapping.items():
            value = headers.get(source_key.lower())
            if value is None:
                value = scope.get(source_key)
            if value is not None:
                log_data[dest_key] = value

        response_status_code: Optional[int] = None

        async def send_wrapper(message: ASGISendEvent) -> None:
            nonlocal response_status_code
            # Record the response status code when the response starts.
            if message["type"] == "http.response.start":
                response_status_code = message.get("status")
            await send(message)

        # Call the wrapped ASGI application.
        await self.app(scope, receive, send_wrapper)
        time_taken_ms = int((time.time() - start_time) * 1000)

        # Set a default status code if none was captured.
        if response_status_code is None:
            response_status_code = 500

        # Determine log type and level based on the response status.
        log_type = "access" if response_status_code < 400 else "error"
        log_level = "ERROR" if response_status_code >= 400 else "INFO"

        # Extract error information from the scope state if available.
        error_info = scope.get("state", {}).get(self.error_info_name, None)
        if error_info is None:
            log_data["error"] = None
        else:
            log_data["error"] = {}
            for src_key, dest_key in self.error_info_mapping.items():
                log_data["error"][dest_key] = error_info.get(src_key)
            # Reset the error info in the state after logging.
            if "state" in scope and isinstance(scope["state"], dict):
                scope["state"][self.error_info_name] = None
                
        # Update the log data with additional fields.
        log_data.update({
            "time_taken_ms": time_taken_ms,
            "status_code": response_status_code,
            "log_type": log_type,
            "level": log_level,
        })

        # Determine the numeric logging level (with compatibility fallback for older Python versions).
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
        
        # Log the JSON-formatted log data (ensure_ascii=False to support Unicode characters).
        self.logger.log(log_level_int, json.dumps(log_data, ensure_ascii=False))
