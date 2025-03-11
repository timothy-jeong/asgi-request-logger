# asgi-request-logger

The `asgi-request-logger` package provides `JsonRequestLoggerMiddleware` that logs incoming HTTP requests in JSON format. It captures useful metadata such as timestamp, event ID, HTTP method, path, client IP (using configurable header names), user agent, processing time, and error information (if available). This middleware is designed to be integrated into a FastAPI (or any ASGI) application.

> **Note:**  
> Due to FastAPI/Starlette’s internal exception handling, when a 500 error occurs the error information may not be captured by the logger because the built-in ServerErrorMiddleware intercepts exceptions and raises an `Exception`. In such cases, it’s recommended to log error details directly within your exception handlers.

## Features

- **JSON Logging:**  
  Logs request details in a structured JSON format.

- **Configurable Options:**
  - **Event ID:** Optionally extract an event ID from a specified header; if absent, a new UUID is generated.
  - **Client IP:** Extract client IP from headers like `X-Forwarded-For` or `X-Real-IP` (configurable).
  - **Error Info Mapping:**  
    Define which keys from the error information (set by your exception handlers) should be logged. For example, mapping the error’s `"code"` to `"error_code"`.
  - **Custom Logger Injection:**  
    **This middleware now requires you to inject your own logger.** You must provide a logger that is configured with a `QueueHandler` (or one of your choosing) to avoid blocking the event loop.
  - **Log Info Mapping:**  
    Use the `log_info_mapping` parameter to specify which fields from the ASGI scope (or headers) should be included in the log output. By default, it maps:
    - `"method"` → `"method"`
    - `"path"` → `"path"`
    - `"client_ip"` → `"client_ip"`
    - `"user_agent"` → `"user_agent"`

## Usage


### Basic Integration
Add the middleware to your FastAPI/Starlette app by injecting your logger and (optionally) overriding the default field mappings:

```python
import logging
from fastapi import FastAPI
from asgi_request_logger import JsonRequestLoggerMiddleware

# Configure your logger (ensure it has a QueueHandler for non-blocking logging)
logger = logging.getLogger("my_logger")
logger.setLevel(logging.INFO)
# (Your logger configuration should add a QueueHandler, e.g., via your own setup or using a helper)

app = FastAPI()

app.add_middleware(
    JsonRequestLoggerMiddleware,
    logger=logger,
    event_id_header="X-Event-ID",              # Use this header for event ID; if absent, a new UUID is generated.
    client_ip_headers=["x-forwarded-for", "x-real-ip"],  # List of headers to determine the client IP.
    error_info_name="error_info",              # The key in the scope where error information is stored.
    error_info_mapping={
        "code": "error_code",
        "message": "error_message",
        "stack_trace": "stack_trace"
    },  # Maps error info keys to desired log field names.
    log_info_mapping={
        "method": "method",
        "path": "path",
        "client_ip": "client_ip",
        "user_agent": "user_agent"
    }  # Maps fields from the ASGI scope/headers to log output.
)

```

### Passing Error Information
For detailed error logging, pass error-related info to the scope (typically in your exception handlers). For example, in FastAPI:

```python
from fastapi import Request, Response
import json, traceback

async def http_exception_handler(request: Request, exc: HTTPException):
    my_exception = MyCustomException(http_exception=exc)
    await _pass_error_info(
        request=request,
        my_exception=my_exception,
        stack_trace=traceback.format_exc().splitlines()
    )
    return await _to_response(my_exception=my_exception)

async def _pass_error_info(
    request: Request,
    my_exception: MyCustomException,
    stack_trace: list[str]
):
    request.state.error_info = {
        "code": my_exception.code,
        "message": my_exception.reason,
        "http_status": my_exception.http_status,
        "stack_trace": stack_trace,
    }

async def _to_response(my_exception: MyCustomException):
    return Response(
        status_code=my_exception.http_status,
        content=json.dumps(
            {"code": my_exception.code, "message": my_exception.reason}, ensure_ascii=False
        )
    )
```
## Example JSON Log Output
A typical log entry might look like this:

```json
{
  "timestamp": "2025-03-02T08:17:40.123456Z",
  "event_id": "ab427b0c-629b-4792-891e-bce4c94d1084",
  "method": "GET",
  "path": "/items/3fa85f64-5717-4562-b3fc-2c963f66afa4",
  "client_ip": "203.0.113.195",
  "user_agent": "Mozilla/5.0 (Macintosh; ...)",
  "time_taken_ms": 12,
  "status_code": 200,
  "log_type": "access",
  "level": "INFO"
}

```

If error information is present (as set by your exception handlers), the log entry will also include keys like `"error_code"`, `"error_message"`, and `"stack_trace"`. Additionally, any fields specified via log_info_mapping will be added from the ASGI scope or headers.