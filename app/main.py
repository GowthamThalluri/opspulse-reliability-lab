from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.responses import Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    generate_latest,
)

app = FastAPI(
    title="OpsPulse API",
    description="A DevOps reliability and observability lab",
    version="1.0.0",
)

# Count HTTP requests.
HTTP_REQUESTS = Counter(
    "opspulse_http_requests_total",
    "Total number of HTTP requests",
    ["method", "path", "status_code"],
)

# Measure how long HTTP requests take.
HTTP_DURATION = Histogram(
    "opspulse_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
)


@app.middleware("http")
async def observe_requests(request: Request, call_next):
    # Do not count Prometheus scraping requests.
    if request.url.path == "/metrics":
        return await call_next(request)

    start_time = perf_counter()
    status_code = "500"

    try:
        response = await call_next(request)
        status_code = str(response.status_code)
        return response

    finally:
        duration = perf_counter() - start_time

        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)

        HTTP_REQUESTS.labels(request.method, path, status_code).inc()

        HTTP_DURATION.labels(request.method, path).observe(duration)


@app.get("/")
async def root():
    return {
        "application": "OpsPulse API",
        "version": "1.0.0",
        "status": "running",
    }


@app.get("/health/live")
async def liveness():
    return {"status": "alive"}


@app.get("/health/ready")
async def readiness():
    return {"status": "ready"}


@app.get("/metrics")
async def metrics():
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
