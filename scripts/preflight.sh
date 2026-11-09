#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

API_BASE_URL="${OPSPULSE_API_URL:-http://127.0.0.1:8001}"
API_BASE_URL="${API_BASE_URL%/}"

REQUIRED_SERVICES=(
  api
  prometheus
  grafana
  loki
  alloy
  jaeger
  node-exporter
)

PASSED=0
FAILED=0

pass() {
  printf 'PASS: %s\n' "$1"
  PASSED=$((PASSED + 1))
}

fail() {
  printf 'FAIL: %s\n' "$1"
  FAILED=$((FAILED + 1))
}

finish() {
  printf '\n========== PREFLIGHT SUMMARY ==========\n'
  printf 'Passed: %s\n' "$PASSED"
  printf 'Failed: %s\n' "$FAILED"
  printf '=======================================\n'

  if [[ "$FAILED" -gt 0 ]]; then
    exit 1
  fi

  exit 0
}

echo "Starting OpsPulse preflight checks..."
echo

# Check required command-line tools.
if command -v docker >/dev/null 2>&1; then
  pass "Docker command is available"
else
  fail "Docker command is not available"
  finish
fi

if command -v curl >/dev/null 2>&1; then
  pass "curl command is available"
else
  fail "curl command is not available"
  finish
fi

# Check Docker daemon connectivity.
if docker info >/dev/null 2>&1; then
  pass "Docker daemon is reachable"
else
  fail "Docker daemon is not reachable"
  finish
fi

# Validate the Compose configuration before inspecting services.
if docker compose config --quiet; then
  pass "Docker Compose configuration is valid"
else
  fail "Docker Compose configuration is invalid"
  finish
fi

echo
echo "Checking containers..."

# Check every expected Compose service.
for service in "${REQUIRED_SERVICES[@]}"; do
  container_id="$(docker compose ps -a -q "$service")"

  if [[ -z "$container_id" ]]; then
    fail "$service container does not exist"
    continue
  fi

  state="$(docker inspect --format '{{.State.Status}}' "$container_id" 2>/dev/null || echo unknown)"

  if [[ "$state" != "running" ]]; then
    fail "$service container state is $state"
    continue
  fi

  pass "$service container is running"

  health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id" 2>/dev/null || echo unknown)"

  if [[ "$health" != "none" ]]; then
    if [[ "$health" == "healthy" ]]; then
      pass "$service health check is healthy"
    else
      fail "$service health check is $health"
    fi
  fi
done

echo
echo "Checking API endpoints..."

# Check API liveness and readiness.
for endpoint in /health/live /health/ready; do
  url="${API_BASE_URL}${endpoint}"

  if status_code="$(curl --silent --output /dev/null \
      --write-out '%{http_code}' --max-time 5 "$url" 2>/dev/null)"; then

    if [[ "$status_code" == "200" ]]; then
      pass "$endpoint returned HTTP 200"
    else
      fail "$endpoint returned HTTP $status_code"
    fi
  else
    fail "$endpoint could not be reached"
  fi
done

echo
finish
