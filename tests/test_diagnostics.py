import json
from urllib.error import HTTPError

import pytest

from automation import collect_diagnostics
from automation.collect_diagnostics import (
    REQUIRED_SERVICES,
    check_api_endpoint,
    check_services,
)


@pytest.fixture
def healthy_containers():
    """Create mock container data for all required services."""

    containers = []

    for service in REQUIRED_SERVICES:
        health = "healthy" if service == "api" else ""

        containers.append(
            {
                "Service": service,
                "Name": f"opspulse-{service}-1",
                "State": "running",
                "Health": health,
                "Image": f"test-image:{service}",
            }
        )

    return containers


def test_all_services_running(healthy_containers):
    """Running services should be classified accurately."""

    results = check_services(healthy_containers)

    assert len(results) == 7

    service_statuses = {result["service"]: result["status"] for result in results}

    assert service_statuses["api"] == "healthy"

    for service in REQUIRED_SERVICES:
        if service != "api":
            assert service_statuses[service] == "running_unverified"


def test_stopped_container_is_unhealthy(healthy_containers):
    """A stopped container must be classified as unhealthy."""

    for container in healthy_containers:
        if container["Service"] == "grafana":
            container["State"] = "exited"

    results = check_services(healthy_containers)

    grafana = next(result for result in results if result["service"] == "grafana")

    assert grafana["status"] == "unhealthy"


def test_missing_service_is_unhealthy(healthy_containers):
    """A required service with no container must fail."""

    containers = [
        container
        for container in healthy_containers
        if container["Service"] != "jaeger"
    ]

    results = check_services(containers)

    jaeger = next(result for result in results if result["service"] == "jaeger")

    assert jaeger["status"] == "unhealthy"
    assert jaeger["containers"] == []


def test_unhealthy_container_is_detected(healthy_containers):
    """A container reporting unhealthy must fail."""

    for container in healthy_containers:
        if container["Service"] == "api":
            container["Health"] = "unhealthy"

    results = check_services(healthy_containers)

    api = next(result for result in results if result["service"] == "api")

    assert api["status"] == "unhealthy"


def test_api_endpoint_returns_200(monkeypatch):
    """HTTP 200 should produce a healthy API result."""

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

    def fake_urlopen(request, timeout):
        return FakeResponse()

    monkeypatch.setattr(
        "automation.collect_diagnostics.urlopen",
        fake_urlopen,
    )

    result = check_api_endpoint("/health/live")

    assert result["status"] == "healthy"
    assert result["http_status"] == 200
    assert result["latency_ms"] >= 0


def test_api_endpoint_returns_http_error(monkeypatch):
    """An HTTP error must produce an unhealthy API result."""

    def fake_urlopen(request, timeout):
        raise HTTPError(
            url=request.full_url,
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(
        "automation.collect_diagnostics.urlopen",
        fake_urlopen,
    )

    result = check_api_endpoint("/health/ready")

    assert result["status"] == "unhealthy"
    assert result["http_status"] == 503


def test_main_generates_json_report(
    monkeypatch,
    tmp_path,
    healthy_containers,
):
    """The diagnostics tool should save a valid JSON report."""

    monkeypatch.setattr(
        collect_diagnostics,
        "PROJECT_ROOT",
        tmp_path,
    )

    monkeypatch.setattr(
        collect_diagnostics,
        "get_container_data",
        lambda: (
            healthy_containers,
            {
                "status": "ok",
                "container_count": 7,
            },
        ),
    )

    monkeypatch.setattr(
        collect_diagnostics,
        "check_api_endpoint",
        lambda endpoint: {
            "endpoint": endpoint,
            "status": "healthy",
            "http_status": 200,
            "latency_ms": 2.5,
        },
    )

    monkeypatch.setattr(
        collect_diagnostics,
        "collect_system_information",
        lambda: {
            "operating_system": "Windows",
            "architecture": "AMD64",
            "disk": {
                "used_percent": 28.41,
            },
        },
    )

    result = collect_diagnostics.main()

    assert result == 0

    reports = list((tmp_path / "reports").glob("incident-*.json"))

    assert len(reports) == 1

    report = json.loads(reports[0].read_text(encoding="utf-8"))

    assert report["project"] == "opspulse-reliability-lab"
    assert report["overall_status"] == "operational"
    assert report["docker_compose"]["container_count"] == 7

    assert report["health_coverage"]["services_with_docker_health_checks"] == 1

    assert len(report["services"]) == 7
    assert len(report["api_checks"]) == 2
