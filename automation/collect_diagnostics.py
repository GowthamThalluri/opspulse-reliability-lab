import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]

API_BASE_URL = os.getenv(
    "OPSPULSE_API_URL",
    "http://127.0.0.1:8001",
).rstrip("/")

REQUIRED_SERVICES = [
    "api",
    "prometheus",
    "grafana",
    "loki",
    "alloy",
    "jaeger",
    "node-exporter",
    "alertmanager",
]

API_ENDPOINTS = [
    "/health/live",
    "/health/ready",
]


def get_container_data() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Retrieve container information from Docker Compose."""

    try:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "ps",
                "-a",
                "--format",
                "json",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    except FileNotFoundError:
        return [], {
            "status": "error",
            "message": "Docker command was not found.",
        }

    except subprocess.TimeoutExpired:
        return [], {
            "status": "error",
            "message": "Docker Compose command timed out.",
        }

    if result.returncode != 0:
        return [], {
            "status": "error",
            "message": result.stderr.strip() or "Docker Compose failed.",
        }

    containers = []

    # Docker Compose returns one JSON object per line.
    for line_number, line in enumerate(
        result.stdout.splitlines(),
        start=1,
    ):
        if not line.strip():
            continue

        try:
            container = json.loads(line)

        except json.JSONDecodeError:
            return containers, {
                "status": "error",
                "message": (f"Invalid JSON on output line {line_number}."),
            }

        if isinstance(container, dict):
            containers.append(container)

    return containers, {
        "status": "ok",
        "container_count": len(containers),
    }


def check_services(
    containers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Check container state and distinguish verified from
    unverified health.
    """

    service_results = []

    for service in REQUIRED_SERVICES:
        matches = [
            container for container in containers if container.get("Service") == service
        ]

        if not matches:
            service_results.append(
                {
                    "service": service,
                    "status": "unhealthy",
                    "message": "No container found for this service.",
                    "containers": [],
                }
            )
            continue

        container_details = []

        for container in matches:
            state = str(container.get("State") or "unknown").lower()

            health = str(container.get("Health") or "not_configured").lower()

            if state != "running" or health == "unhealthy":
                status = "unhealthy"

            elif health == "healthy":
                status = "healthy"

            elif health == "not_configured":
                status = "running_unverified"

            else:
                status = "starting"

            container_details.append(
                {
                    "name": (container.get("Name") or container.get("Names")),
                    "state": state,
                    "health": health,
                    "health_check_configured": (health != "not_configured"),
                    "status": status,
                    "image": container.get("Image"),
                }
            )

        statuses = [item["status"] for item in container_details]

        if "unhealthy" in statuses:
            service_status = "unhealthy"

        elif all(status == "healthy" for status in statuses):
            service_status = "healthy"

        elif all(status in {"healthy", "running_unverified"} for status in statuses):
            service_status = "running_unverified"

        else:
            service_status = "starting"

        service_results.append(
            {
                "service": service,
                "status": service_status,
                "containers": container_details,
            }
        )

    return service_results


def check_api_endpoint(endpoint: str) -> dict[str, Any]:
    """Check an API endpoint using HTTP or HTTPS only."""

    url = f"{API_BASE_URL}{endpoint}"

    # Reject unsupported schemes and URLs without a hostname.
    try:
        parsed_url = urlsplit(url)

        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.hostname
            or parsed_url.username is not None
            or parsed_url.password is not None
        ):
            return {
                "endpoint": endpoint,
                "status": "unhealthy",
                "http_status": None,
                "error": "Only HTTP/HTTPS URLs with a hostname are permitted.",
            }

    except ValueError as error:
        return {
            "endpoint": endpoint,
            "status": "unhealthy",
            "http_status": None,
            "error": str(error),
        }

    start_time = time.perf_counter()

    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": "OpsPulse-Diagnostics/1.0",
            },
            timeout=5,
            allow_redirects=False,
        )

        latency_ms = round(
            (time.perf_counter() - start_time) * 1000,
            2,
        )

        status_code = response.status_code

        return {
            "endpoint": endpoint,
            "status": ("healthy" if 200 <= status_code < 300 else "unhealthy"),
            "http_status": status_code,
            "latency_ms": latency_ms,
        }

    except requests.RequestException as error:
        return {
            "endpoint": endpoint,
            "status": "unhealthy",
            "http_status": None,
            "error": str(error),
        }


def collect_system_information() -> dict[str, Any]:
    """Collect host operating system and project drive information."""

    disk = shutil.disk_usage(PROJECT_ROOT)

    disk_usage_percent = (
        round((disk.used / disk.total) * 100, 2) if disk.total else None
    )

    return {
        "operating_system": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "python_version": platform.python_version(),
        "project_drive": PROJECT_ROOT.anchor,
        "disk": {
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
            "used_percent": disk_usage_percent,
        },
    }


def main() -> int:
    """Collect diagnostics and generate a JSON report."""

    print("Starting OpsPulse Python diagnostics...\n")

    # 1. Collect Docker Compose container information.
    containers, compose_result = get_container_data()

    # 2. Evaluate the required services.
    service_results = check_services(containers)

    # 3. Check API liveness and readiness.
    api_results = [check_api_endpoint(endpoint) for endpoint in API_ENDPOINTS]

    # 4. Collect host information.
    system_information = collect_system_information()

    # 5. Determine the overall operational status.
    all_services_operational = all(
        item["status"]
        in {
            "healthy",
            "running_unverified",
        }
        for item in service_results
    )

    all_api_healthy = all(item["status"] == "healthy" for item in api_results)

    overall_status = (
        "operational"
        if compose_result["status"] == "ok"
        and all_services_operational
        and all_api_healthy
        else "unhealthy"
    )

    # Count services that have Docker health checks configured.
    health_checked_services = sum(
        1
        for service in service_results
        if any(
            container["health_check_configured"] for container in service["containers"]
        )
    )

    # 6. Build the diagnostic report.
    generated_at = datetime.now().astimezone()

    report = {
        "schema_version": 1,
        "project": "opspulse-reliability-lab",
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "overall_status": overall_status,
        "docker_compose": compose_result,
        "health_coverage": {
            "services_with_docker_health_checks": (health_checked_services),
            "required_services": len(REQUIRED_SERVICES),
            "note": (
                "Services without Docker health checks are "
                "assessed by container state only."
            ),
        },
        "services": service_results,
        "api_checks": api_results,
        "system": system_information,
    }

    # 7. Save the report locally.
    reports_directory = PROJECT_ROOT / "reports"
    reports_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    filename = f"incident-{generated_at.strftime('%Y%m%d-%H%M%S')}.json"

    report_path = reports_directory / filename

    report_path.write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )

    # 8. Print a readable summary.
    print("Container checks:")

    for service in service_results:
        print(f"{service['status'].upper():20} {service['service']}")

    print("\nAPI checks:")

    for api in api_results:
        print(f"{api['status'].upper():10} {api['endpoint']} HTTP {api['http_status']}")

    print("\nSystem information:")

    print(f"Operating system: {system_information['operating_system']}")

    print(f"Architecture: {system_information['architecture']}")

    print(f"Disk usage: {system_information['disk']['used_percent']}%")

    print("\nHealth-check coverage:")

    print(
        f"{health_checked_services}/"
        f"{len(REQUIRED_SERVICES)} services "
        f"have Docker health checks configured."
    )

    print("\n========== DIAGNOSTIC SUMMARY ==========")

    print(f"Overall status: {overall_status.upper()}")

    print(f"Report saved: {report_path.relative_to(PROJECT_ROOT)}")

    print("========================================")

    return 0 if overall_status == "operational" else 1


if __name__ == "__main__":
    sys.exit(main())
