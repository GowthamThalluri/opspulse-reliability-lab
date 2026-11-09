import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from automation.collect_diagnostics import check_api_endpoint

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Only explicitly approved services can be recovered.
ALLOWED_SERVICES = {"api"}

POLL_INTERVAL_SECONDS = 2


def get_compose_containers() -> list[dict[str, Any]]:
    """Retrieve current container information from Docker Compose."""

    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "-a", "--format", "json"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"Could not execute Docker Compose: {error}") from error

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Docker Compose inspection failed.")

    containers = []

    for line_number, line in enumerate(
        result.stdout.splitlines(),
        start=1,
    ):
        if not line.strip():
            continue

        try:
            container = json.loads(line)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"Invalid Docker JSON on line {line_number}.") from error

        if isinstance(container, dict):
            containers.append(container)

    return containers


def get_service_container(service: str) -> dict[str, Any]:
    """Find exactly one container belonging to the requested service."""

    containers = get_compose_containers()

    matches = [
        container for container in containers if container.get("Service") == service
    ]

    if not matches:
        raise RuntimeError(f"No container found for service '{service}'.")

    if len(matches) != 1:
        raise RuntimeError(
            f"Expected one '{service}' container, found {len(matches)}. "
            "Automatic recovery is disabled for scaled services."
        )

    return matches[0]


def restart_eligibility(
    container: dict[str, Any],
) -> tuple[bool, str]:
    """Allow recovery only for a running container marked unhealthy."""

    state = str(container.get("State") or "unknown").lower()
    health = str(container.get("Health") or "not_configured").lower()

    if state != "running":
        return False, f"Container state is '{state}', not running."

    if health != "unhealthy":
        return (
            False,
            f"Docker health is '{health}', not unhealthy. Restart is not permitted.",
        )

    return True, "Container is confirmed unhealthy."


def restart_service(service: str) -> None:
    """Restart one explicitly approved Compose service."""

    result = subprocess.run(
        ["docker", "compose", "restart", service],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"Failed to restart {service}.")

    print(result.stdout.strip() or f"Restart command completed for {service}.")


def wait_for_healthy(
    service: str,
    timeout_seconds: int,
) -> bool:
    """Wait until Docker reports the service container healthy."""

    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        container = get_service_container(service)

        state = str(container.get("State") or "unknown").lower()
        health = str(container.get("Health") or "not_configured").lower()

        print(f"Recovery verification: state={state}, health={health}")

        if state == "running" and health == "healthy":
            return True

        time.sleep(POLL_INTERVAL_SECONDS)

    return False


def verify_api_endpoints() -> bool:
    """Verify both externally accessible API health endpoints."""

    all_healthy = True

    for endpoint in ("/health/live", "/health/ready"):
        result = check_api_endpoint(endpoint)

        print(f"{result['status'].upper()}: {endpoint} HTTP {result['http_status']}")

        if result["status"] != "healthy":
            all_healthy = False

    return all_healthy


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Safely recover an unhealthy OpsPulse service."
    )

    parser.add_argument(
        "--service",
        required=True,
        choices=sorted(ALLOWED_SERVICES),
        help="Approved service to inspect or recover.",
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually restart the service. Default mode is dry-run.",
    )

    parser.add_argument(
        "--wait-seconds",
        type=int,
        default=120,
        help="Maximum time to wait for Docker health recovery.",
    )

    args = parser.parse_args()

    if args.wait_seconds < 1:
        print("ERROR: --wait-seconds must be greater than zero.")
        return 2

    service = args.service

    print("OpsPulse Controlled Recovery")
    print(f"Target service: {service}")
    print(f"Mode: {'APPLY' if args.apply else 'DRY RUN'}")
    print()

    try:
        container = get_service_container(service)

        state = container.get("State", "unknown")
        health = container.get("Health") or "not_configured"

        print(f"Current container state: {state}")
        print(f"Current Docker health: {health}")
        print()

        eligible, reason = restart_eligibility(container)

        if not eligible:
            print(f"RECOVERY NOT PERMITTED: {reason}")
            print("No changes were made.")
            return 0

        print(f"Recovery eligibility: {reason}")

        if not args.apply:
            print()
            print("DRY RUN: A restart would be permitted.")
            print("No changes were made.")
            return 0

        print()
        print(f"Restarting approved service: {service}")

        restart_service(service)

        print()
        print("Waiting for Docker health verification...")

        recovered = wait_for_healthy(
            service,
            args.wait_seconds,
        )

        if not recovered:
            print("RECOVERY FAILED: Docker did not report healthy.")
            return 1

        print()
        print("Docker health verification passed.")
        print("Checking external API endpoints...")

        endpoints_healthy = verify_api_endpoints()

        if not endpoints_healthy:
            print("RECOVERY INCOMPLETE: API endpoint verification failed.")
            return 1

        print()
        print("========== RECOVERY SUMMARY ==========")
        print(f"Service: {service}")
        print("Result: RECOVERED")
        print("Docker health: healthy")
        print("API endpoints: healthy")
        print("======================================")

        return 0

    except (RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"RECOVERY ERROR: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
