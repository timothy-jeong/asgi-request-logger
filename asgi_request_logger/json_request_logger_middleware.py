import logging
import time
import uuid
import json
import queue
from typing import Optional, Dict, List, Callable, Any
from asgiref.typing import ASGI3Application, ASGIReceiveCallable, ASGISendCallable, Scope, ASGISendEvent
from logging.handlers import QueueHandler, QueueListener

class JsonRequestLoggerMiddleware:
    def __init__(
        self,
        app: ASGI3Application,
        error_info_name: str = "error_info",
        error_info_mapping: Optional[Dict[str, str]] = None,
        event_id_header: Optional[str] = None,
        client_ip_headers: Optional[List[str]] = None,
        logger: Optional[logging.Logger] = None,
        log_max_queue_size: int = 1000,
        extra_fields_extractor: Optional[Callable[[Scope], Dict[str, Any]]] = None,
    ) -> None:
        """
        Initializes the JSON Request Logger Middleware.

        Args:
            app (ASGI3Application): The ASGI application instance to wrap.
            error_info_name (str, optional): The key name in the request state from which to extract error information.
                Defaults to "error_info".
            error_info_mapping (Optional[Dict[str, str]], optional): A dictionary mapping error information keys (from the request
                state) to desired log field names. For example, {"code": "error_code", "message": "error_message", "stack_trace": "stack_trace"}.
                Defaults to a mapping for "code", "message", and "stack_trace".
            event_id_header (Optional[str], optional): The HTTP header name to extract an event ID from. If not provided or if the header
                is missing, a new UUID will be generated. Defaults to None.
            client_ip_headers (Optional[List[str]], optional): A list of HTTP header names to check for the client IP address,
                in order of priority. If none are provided, the client IP will be obtained from the scope's "client" value.
                Defaults to ["x-forwarded-for", "x-real-ip"].
            logger (Optional[logging.Logger], optional): A custom logger to use for logging requests. If not provided, a default
                logger with INFO level is created and configured to use a QueueHandler.
            log_max_queue_size (int): The maximum size for the logging queue. Defaults to 1000.
            extra_fields_extractor (Optional[Callable[[Scope], Dict[str, Any]]], optional): A callable that receives the entire scope
                and returns a dictionary of additional fields to add to the log output. This allows custom mapping of scope items
                to JSON fields.
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
        self.log_max_queue_size = log_max_queue_size
        self.extra_fields_extractor = extra_fields_extractor

        if logger:
            self.logger = logger
            if not any(isinstance(h, QueueHandler) for h in logger.handlers):
                self.logger.warning(
                    "JsonRequestLoggerMiddleware: The provided logger does not use a QueueHandler. It is recommended to use QueueHandler "
                    "to avoid blocking in an asynchronous environment."
                )
        else:
            # Create a default logger that uses QueueHandler and QueueListener.
            log_queue = queue.Queue(maxsize=self.log_max_queue_size)
            queue_handler = QueueHandler(log_queue)
            
            # Create a stream handler for output.
            stream_handler = logging.StreamHandler()
            stream_handler.setLevel(logging.INFO)
            formatter = logging.Formatter("%(message)s")
            stream_handler.setFormatter(formatter)
            
            # Create a listener that will process log records from the queue.
            listener = QueueListener(log_queue, stream_handler)
            listener.start()

            logger = logging.getLogger("request-logger")
            logger.setLevel(logging.INFO)
            logger.handlers.clear()
            logger.addHandler(queue_handler)
            logger.propagate = False
            self.logger = logger
            self._listener = listener  # store listener so it can be stopped later if needed

    async def __call__(
        self,
        scope: Scope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        start_time = time.time()

        # Parse headers and convert keys to lowercase for case-insensitive lookup.
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}

        # Determine event ID from header or generate a new UUID.
        if self.event_id_header and self.event_id_header in headers:
            event_id = headers[self.event_id_header]
        else:
            event_id = str(uuid.uuid4())

        # Extract client IP from the specified headers.
        client_ip = None
        for header in self.client_ip_headers:
            if header in headers:
                client_ip = headers[header].split(",")[0].strip()
                break
        if not client_ip:
            client_ip = scope.get("client", ("unknown",))[0]

        # Default log data.
        log_data = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.%fZ", time.gmtime()),
            "event_id": event_id,
            "method": scope.get("method"),
            "path": scope.get("path"),
            "client_ip": client_ip,
            "user_agent": headers.get("user-agent"),
        }

        response_status_code: Optional[int] = None

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

        log_data.update({
            "time_taken_ms": time_taken_ms,
            "status_code": response_status_code,
            "log_type": log_type,
            "level": log_level,
        })

        # Apply additional custom fields extracted from scope, if any.
        if self.extra_fields_extractor:
            extra_fields = self.extra_fields_extractor(scope)
            if isinstance(extra_fields, dict):
                log_data.update(extra_fields)

        # Fallback for logging.getLevelNamesMapping if not available.
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
        
    def __del__(self):
        if hasattr(self, "_listener"):
            self._listener.stop()
    
    def shutdown(self):
        """
        when application about to shutdown, you better call this method or __del__ directly
        """
        self.__del__()