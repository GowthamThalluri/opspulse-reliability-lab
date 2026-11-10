from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]

ALERT_RULES_FILE = (
    PROJECT_ROOT / "observability" / "prometheus" / "alerts" / "opspulse-alerts.yml"
)

PROMETHEUS_CONFIG_FILE = (
    PROJECT_ROOT / "observability" / "prometheus" / "prometheus.yml"
)

ALERTMANAGER_CONFIG_FILE = (
    PROJECT_ROOT / "observability" / "alertmanager" / "alertmanager.yml"
)


def load_yaml(path):
    """Load and parse a YAML configuration file."""

    with path.open(encoding="utf-8") as file:
        return yaml.safe_load(file)


def get_alert_rules():
    """Return all alert rules indexed by alert name."""

    config = load_yaml(ALERT_RULES_FILE)

    return {
        rule["alert"]: rule for group in config["groups"] for rule in group["rules"]
    }


def test_alert_configuration_has_two_groups():
    config = load_yaml(ALERT_RULES_FILE)

    assert len(config["groups"]) == 2

    group_names = {group["name"] for group in config["groups"]}

    assert group_names == {
        "opspulse-api-alerts",
        "opspulse-host-alerts",
    }


def test_exactly_five_permanent_alerts_exist():
    rules = get_alert_rules()

    expected_alerts = {
        "OpsPulseAPIDown",
        "OpsPulseHigh5xxErrorRate",
        "OpsPulseHighP95Latency",
        "OpsPulseHostHighCPU",
        "OpsPulseHostDiskSpaceLow",
    }

    assert set(rules.keys()) == expected_alerts
    assert "OpsPulseAlertPipelineTest" not in rules


def test_every_alert_has_required_metadata():
    rules = get_alert_rules()

    for rule in rules.values():
        assert rule.get("expr")
        assert rule.get("labels", {}).get("severity")
        assert rule.get("labels", {}).get("category")
        assert rule.get("annotations", {}).get("summary")
        assert rule.get("annotations", {}).get("description")


def test_api_down_alert():
    rule = get_alert_rules()["OpsPulseAPIDown"]

    assert 'up{job="opspulse-api"} == 0' in rule["expr"]
    assert rule["for"] == "1m"
    assert rule["labels"]["severity"] == "critical"


def test_high_5xx_alert():
    rule = get_alert_rules()["OpsPulseHigh5xxErrorRate"]

    assert 'status_code=~"5.."' in rule["expr"]
    assert "> 0.05" in rule["expr"]
    assert rule["for"] == "5m"


def test_high_latency_alert():
    rule = get_alert_rules()["OpsPulseHighP95Latency"]

    assert "histogram_quantile" in rule["expr"]
    assert "opspulse_http_request_duration_seconds_bucket" in rule["expr"]
    assert "> 1" in rule["expr"]
    assert rule["for"] == "5m"


def test_high_cpu_alert():
    rule = get_alert_rules()["OpsPulseHostHighCPU"]

    assert "node_cpu_seconds_total" in rule["expr"]
    assert "> 80" in rule["expr"]
    assert rule["for"] == "5m"


def test_low_disk_space_alert():
    rule = get_alert_rules()["OpsPulseHostDiskSpaceLow"]

    assert "node_filesystem_avail_bytes" in rule["expr"]
    assert "/mnt/host/[cd]" in rule["expr"]
    assert "< 10" in rule["expr"]
    assert rule["for"] == "5m"


def test_prometheus_forwards_alerts_to_alertmanager():
    config = load_yaml(PROMETHEUS_CONFIG_FILE)

    assert "/etc/prometheus/alerts/opspulse-alerts.yml" in config["rule_files"]

    targets = config["alerting"]["alertmanagers"][0]["static_configs"][0]["targets"]

    assert "alertmanager:9093" in targets


def test_alertmanager_has_local_receiver():
    config = load_yaml(ALERTMANAGER_CONFIG_FILE)

    receiver_names = {receiver["name"] for receiver in config["receivers"]}

    assert config["route"]["receiver"] == "opspulse-local"
    assert "opspulse-local" in receiver_names
