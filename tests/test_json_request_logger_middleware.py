import json
import logging

import pytest
from asgiref.typing import Scope, ASGIReceiveCallable, ASGISendCallable

from asgi_request_logger import JsonRequestLoggerMiddleware

# Custom logging handler to capture log records in a list.
class ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(self.format(record))

# A dummy ASGI app that returns a 200 response.
async def dummy_app(scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable):
    # Send response start message with status 200.
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [(b"content-type", b"text/plain")],
    })
    # Send response body.
    await send({
        "type": "http.response.body",
        "body": b"OK",
    })

@pytest.mark.asyncio
async def test_json_request_logger_success():
    # Set up a custom logger with ListHandler to capture log output.
    test_logger = logging.getLogger("test_logger_success")
    test_logger.setLevel(logging.INFO)
    list_handler = ListHandler()
    formatter = logging.Formatter("%(message)s")
    list_handler.setFormatter(formatter)
    test_logger.handlers = [list_handler]
    test_logger.propagate = False

    # Wrap the dummy app with the JsonRequestLoggerMiddleware.
    middleware = JsonRequestLoggerMiddleware(
        app=dummy_app,
        event_id_header="X-Event-ID",
        client_ip_headers=["x-forwarded-for", "x-real-ip"],
        logger=test_logger,
    )
    
    # remove warning
    list_handler.records.clear()

    # Create a fake HTTP scope with headers.
    scope: Scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": "/test",
        "headers": [
            (b"x-forwarded-for", b"203.0.113.1"),
            (b"user-agent", b"pytest"),
            (b"x-event-id", b"test-event-id"),
        ],
        "client": ("127.0.0.1", 12345),
        "state": {},  # No error info.
    }

    # Dummy receive function.
    async def receive() -> dict:
        return {"type": "http.request"}

    # Dummy send function that collects sent events.
    sent_messages = []
    async def send(message: dict) -> None:
        sent_messages.append(message)

    # Invoke the middleware.
    await middleware(scope, receive, send)

    # Ensure a log record was captured.
    assert len(list_handler.records) > 0

    # Parse the logged JSON.
    log_record = json.loads(list_handler.records[0])
    assert log_record["method"] == "GET"
    assert log_record["path"] == "/test"
    # event_id should come from the header.
    assert log_record["event_id"] == "test-event-id"
    # client_ip should be extracted from x-forwarded-for header.
    assert log_record["client_ip"] == "203.0.113.1"
    # status_code is 200.
    assert log_record["status_code"] == 200
    # Log type and level.
    assert log_record["log_type"] == "access"
    assert log_record["level"] == "INFO"
    # time_taken_ms should be an integer.
    assert isinstance(log_record["time_taken_ms"], int)
    # error should be None.
    assert log_record["error"] is None

@pytest.mark.asyncio
async def test_json_request_logger_error():
    # Set up a custom logger to capture log output.
    test_logger = logging.getLogger("test_logger_error")
    test_logger.setLevel(logging.INFO)
    list_handler = ListHandler()
    formatter = logging.Formatter("%(message)s")
    list_handler.setFormatter(formatter)
    test_logger.handlers = [list_handler]
    test_logger.propagate = False

    # Create a dummy ASGI app that simulates a 500 error.
    async def error_app(scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable):
        await send({
            "type": "http.response.start",
            "status": 500,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({
            "type": "http.response.body",
            "body": b"Internal Server Error",
        })

    # Wrap the error app with the JsonRequestLoggerMiddleware.
    middleware = JsonRequestLoggerMiddleware(
        app=error_app,
        logger=test_logger,
    )
    
    # remove warning
    list_handler.records.clear()

    # Create a fake HTTP scope with error information in state.
    scope: Scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "path": "/error",
        "headers": [
            (b"user-agent", b"pytest"),
        ],
        "client": ("127.0.0.1", 12345),
        "state": {
            "error_info": {
                "code": "TEST_ERROR",
                "message": "An error occurred",
                "stack_trace": ["trace1", "trace2"],
            }
        },
    }

    async def receive() -> dict:
        return {"type": "http.request"}

    sent_messages = []
    async def send(message: dict) -> None:
        sent_messages.append(message)

    await middleware(scope, receive, send)

    # Ensure a log record was captured.
    assert len(list_handler.records) > 0

    # Parse the logged JSON.
    log_record = json.loads(list_handler.records[0])
    assert log_record["method"] == "POST"
    assert log_record["path"] == "/error"
    assert log_record["status_code"] == 500
    # Log type should be error and level ERROR.
    assert log_record["log_type"] == "error"
    assert log_record["level"] == "ERROR"
    # Check error details (default mapping: "code" -> "error_code", etc.)
    error_info = log_record["error"]
    assert error_info["error_code"] == "TEST_ERROR"
    assert error_info["error_message"] == "An error occurred"
    assert error_info["stack_trace"] == ["trace1", "trace2"]
