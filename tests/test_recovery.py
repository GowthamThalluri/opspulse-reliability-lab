import sys
from unittest.mock import Mock

import pytest

from automation import recover_service


@pytest.mark.parametrize(
    ("state", "health", "expected"),
    [
        ("running", "unhealthy", True),
        ("running", "healthy", False),
        ("running", "starting", False),
        ("running", "not_configured", False),
        ("exited", "unhealthy", False),
    ],
)
def test_restart_eligibility(state, health, expected):
    """Only running containers explicitly marked unhealthy are eligible."""

    container = {
        "State": state,
        "Health": health,
    }

    eligible, reason = recover_service.restart_eligibility(container)

    assert eligible is expected
    assert isinstance(reason, str)
    assert reason


def test_dry_run_does_not_restart_healthy_service(monkeypatch):
    """Dry-run must never invoke the restart operation."""

    monkeypatch.setattr(
        sys,
        "argv",
        ["recover_service", "--service", "api"],
    )

    monkeypatch.setattr(
        recover_service,
        "get_service_container",
        lambda service: {
            "Service": service,
            "State": "running",
            "Health": "healthy",
        },
    )

    restart_mock = Mock()

    monkeypatch.setattr(
        recover_service,
        "restart_service",
        restart_mock,
    )

    result = recover_service.main()

    assert result == 0
    restart_mock.assert_not_called()


def test_apply_restarts_unhealthy_service(monkeypatch):
    """Apply mode should restart an eligible service and verify recovery."""

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "recover_service",
            "--service",
            "api",
            "--apply",
            "--wait-seconds",
            "5",
        ],
    )

    monkeypatch.setattr(
        recover_service,
        "get_service_container",
        lambda service: {
            "Service": service,
            "State": "running",
            "Health": "unhealthy",
        },
    )

    restart_mock = Mock()
    wait_mock = Mock(return_value=True)
    endpoint_mock = Mock(return_value=True)

    monkeypatch.setattr(
        recover_service,
        "restart_service",
        restart_mock,
    )

    monkeypatch.setattr(
        recover_service,
        "wait_for_healthy",
        wait_mock,
    )

    monkeypatch.setattr(
        recover_service,
        "verify_api_endpoints",
        endpoint_mock,
    )

    result = recover_service.main()

    assert result == 0
    restart_mock.assert_called_once_with("api")
    wait_mock.assert_called_once_with("api", 5)
    endpoint_mock.assert_called_once()


def test_recovery_fails_when_docker_health_does_not_recover(
    monkeypatch,
):
    """Recovery must fail when Docker never reports healthy."""

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "recover_service",
            "--service",
            "api",
            "--apply",
            "--wait-seconds",
            "5",
        ],
    )

    monkeypatch.setattr(
        recover_service,
        "get_service_container",
        lambda service: {
            "Service": service,
            "State": "running",
            "Health": "unhealthy",
        },
    )

    restart_mock = Mock()
    endpoint_mock = Mock(return_value=True)

    monkeypatch.setattr(
        recover_service,
        "restart_service",
        restart_mock,
    )

    monkeypatch.setattr(
        recover_service,
        "wait_for_healthy",
        Mock(return_value=False),
    )

    monkeypatch.setattr(
        recover_service,
        "verify_api_endpoints",
        endpoint_mock,
    )

    result = recover_service.main()

    assert result == 1
    restart_mock.assert_called_once_with("api")
    endpoint_mock.assert_not_called()


def test_recovery_fails_when_api_endpoint_verification_fails(
    monkeypatch,
):
    """Docker health alone is insufficient if API endpoints fail."""

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "recover_service",
            "--service",
            "api",
            "--apply",
            "--wait-seconds",
            "5",
        ],
    )

    monkeypatch.setattr(
        recover_service,
        "get_service_container",
        lambda service: {
            "Service": service,
            "State": "running",
            "Health": "unhealthy",
        },
    )

    monkeypatch.setattr(
        recover_service,
        "restart_service",
        Mock(),
    )

    monkeypatch.setattr(
        recover_service,
        "wait_for_healthy",
        Mock(return_value=True),
    )

    monkeypatch.setattr(
        recover_service,
        "verify_api_endpoints",
        Mock(return_value=False),
    )

    result = recover_service.main()

    assert result == 1
