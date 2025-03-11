# asgi-request-logger
The `asgi-request-logger` package provides `JsonRequestLoggerMiddleware` that logs incoming HTTP requests in JSON format. It captures useful metadata such as timestamp, event ID, HTTP method, path, client IP (using configurable header names), user agent, processing time, and error information (if available). This middleware is designed to be integrated into a FastAPI application.

> Note: <br/>
Due to FastAPI/Starlette’s internal exception handling, when a 500 error occurs the error information may not be captured by the logger because the built-in ServerErrorMiddleware intercepts exceptions and raise `Exception`. In such cases, it’s recommended to log error details directly within your exception handlers.

## Features
- JSON Logging: Logs request details in a structured JSON format.
- Configurable Options:
    - Event ID: Optionally extract an event ID from a specified header; if absent, a new UUID is generated.
    - Client IP: Extract client IP from headers like X-Forwarded-For or X-Real-IP (configurable).
    - Error Info Mapping: Define which keys from the error info (set by exception handlers) should be logged.
    - Custom Logger: Optionally supply your own logging.Logger instance.
  -  Extra Fields Extractor: Provide a callable to extract additional fields from the ASGI scope and include them in the log output.


## Logging Configuration
By default, if no custom logger is provided, the middleware creates a default logger that uses a `QueueHandler` and `QueueListener` to offload logging I/O to a separate thread. This approach helps prevent blocking the main thread in asynchronous environments. You can configure the maximum queue size via the `log_max_queue_size` parameter (default is 1000) to balance memory usage and performance. 

> Note: <br/>
If you provide your own logger, ensure that it uses a `QueueHandler` for non-blocking behavior. The middleware will emit a warning if the supplied logger does not utilize a `QueueHandler`.

## Extra Fields Extractor
You can extend the default log output by supplying an `extra_fields_extractor` callable when adding the middleware. This function receives the entire ASGI scope and returns a dictionary of additional fields to merge into the JSON log output. For example, you might extract a custom header or any other contextual information from the scope:

```python
def extra_fields_extractor(scope: dict) -> dict:
    # Convert headers to lowercase for easy lookup.
    headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}
    extra = {}
    if "x-custom-header" in headers:
        extra["custom_field"] = headers["x-custom-header"]
    return extra
```
Then, you can pass this function when adding the middleware:

```python
app.add_middleware(
    JsonRequestLoggerMiddleware,
    extra_fields_extractor=extra_fields_extractor,
)
```


## Installation

```bash
pip install asgi-request-logger
```

## Usage
Basic Integration
You can add the middleware to your FastAPI/Starlette app using `app.add_middleware()`:

```python
from fastapi import FastAPI
from asgi_request_logger import JsonRequestLoggerMiddleware

app = FastAPI()

# Add JSON Request Logger Middleware with custom configuration.
app.add_middleware(
    JsonRequestLoggerMiddleware,
    event_id_header="X-Event-ID",              # Use this header for the event ID; if absent, a new UUID is generated.
    client_ip_headers=["x-forwarded-for", "x-real-ip"],  # List of headers to determine the client IP.
    error_info_name="error_info",              # The key in the scope where error information is stored.
    error_info_mapping={
        "code": "error_code",
        "message": "error_message",
        "stack_trace": "stack_trace"
    },  # The expected dictionary format for the error information. This value will be logged under the "error" key.
    extra_fields_extractor=extra_fields_extractor  # Extracts additional fields from the ASGI scope.
)

```

For detailed error information, you should pass error-related info to the scope. In a FastAPI application, you can do this in your exception handlers. For example:

```python
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
  "level": "INFO",
  "custom_field": "custom_value"  // This field comes from the extra_fields_extractor, if applicable.
}
```

If error information is present (set by your exception handlers), the log entry will also include keys like `"error_code"`, `"error_message"`, and `"stack_trace"`.

## Performance Considerations

While offloading logging to a separate thread via QueueHandler/QueueListener introduces a small overhead (for example, increasing average request latency from ~79 ms to ~84 ms in our tests), this trade-off is essential in asynchronous environments. It prevents blocking the main thread during heavy I/O operations, and the benefits become even more significant when logging to external systems or handling large volumes of log data.