import atexit
import os
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.responses import Response
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
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


# Configure distributed tracing only when an OTLP endpoint exists.
def configure_tracing(application: FastAPI) -> None:
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")

    if not endpoint:
        return

    resource = Resource.create(
        {
            "service.name": os.getenv("OTEL_SERVICE_NAME", "opspulse-api"),
            "service.version": "1.0.0",
            "deployment.environment": "local",
        }
    )

    provider = TracerProvider(resource=resource)

    exporter = OTLPSpanExporter(endpoint=endpoint)

    provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(
        application,
        tracer_provider=provider,
    )

    atexit.register(provider.shutdown)


configure_tracing(app)

tracer = trace.get_tracer("opspulse.api")


# Prometheus metrics.
HTTP_REQUESTS = Counter(
    "opspulse_http_requests_total",
    "Total number of HTTP requests",
    ["method", "path", "status_code"],
)

HTTP_DURATION = Histogram(
    "opspulse_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
)


@app.middleware("http")
async def observe_requests(request: Request, call_next):
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

        HTTP_REQUESTS.labels(
            request.method,
            path,
            status_code,
        ).inc()

        HTTP_DURATION.labels(
            request.method,
            path,
        ).observe(duration)


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
    # A custom child span inside the automatically generated request span.
    with tracer.start_as_current_span("readiness.check") as span:
        span.set_attribute("opspulse.health.check", "readiness")
        span.set_attribute("opspulse.health.status", "ready")

        return {"status": "ready"}


@app.get("/metrics")
async def metrics():
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
