"""Unit tests for Multi-Job Cron Fleet Concurrency Optimizer and Desynchronizer."""

import json
from datetime import datetime, timezone
import pytest

from cron_rhythm_studio import (
    FleetAuditReport,
    FleetConcurrencyPeak,
    FleetRebalanceSuggestion,
    audit_cron_fleet,
)
from cron_rhythm_studio.cli import main as cli_main
from cron_rhythm_studio.mcp_server import handle_jsonrpc_message


def test_fleet_audit_empty_and_single_job():
    # Empty fleet
    report_empty = audit_cron_fleet({})
    assert isinstance(report_empty, FleetAuditReport)
    assert report_empty.total_jobs == 0
    assert report_empty.max_concurrency_before == 0
    assert report_empty.thundering_herd_score_before == 0.0

    # Single job (cannot collide with anything)
    report_single = audit_cron_fleet({"job1": "0 0 * * *"})
    assert report_single.total_jobs == 1
    assert report_single.max_concurrency_before == 1
    assert report_single.max_concurrency_after == 1
    assert report_single.thundering_herd_score_before == 0.0


def test_fleet_audit_thundering_herd_rebalancing():
    # 4 jobs configured to fire at the exact same minute midnight every day
    jobs = {
        "db_backup": "0 0 * * *",
        "billing_sync": "0 0 * * *",
        "nightly_analytics": "0 0 * * *",
        "cache_invalidation": "0 0 * * *",
    }
    t0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    report = audit_cron_fleet(jobs, horizon_hours=24, start_time=t0, auto_rebalance=True)

    assert report.total_jobs == 4
    assert report.max_concurrency_before == 4
    # Rebalance should phase-shift jobs so concurrency peak is lower
    assert report.max_concurrency_after < report.max_concurrency_before
    assert report.thundering_herd_score_after < report.thundering_herd_score_before
    assert len(report.rebalance_suggestions) >= 3

    # Check to_dict serialization
    d = report.to_dict()
    assert d["total_jobs"] == 4
    assert "rebalance_suggestions" in d
    assert "optimized_crontab" in d
    assert "kubernetes_manifests" in d
    assert "ascii_concurrency_profile" in d
    assert "svg_concurrency_chart" in d
    assert "<svg" in report.svg_concurrency_chart
    assert "apiVersion: batch/v1" in report.kubernetes_manifests
    assert "kind: CronJob" in report.kubernetes_manifests


def test_fleet_audit_step_intervals():
    # Jobs firing every 15 minutes
    jobs = {
        "metrics_pull": "*/15 * * * *",
        "health_check": "*/15 * * * *",
    }
    t0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    report = audit_cron_fleet(jobs, horizon_hours=2, start_time=t0, auto_rebalance=True)

    assert report.total_jobs == 2
    assert report.max_concurrency_before == 2
    assert report.max_concurrency_after <= 1


def test_fleet_mcp_tool_call():
    req = {
        "jsonrpc": "2.0",
        "id": "test-fleet-1",
        "method": "tools/call",
        "params": {
            "name": "cron_audit_fleet",
            "arguments": {
                "jobs": {
                    "backup": "0 * * * *",
                    "sync": "0 * * * *",
                },
                "horizon_hours": 12,
            },
        },
    }
    resp = handle_jsonrpc_message(req)
    assert resp is not None
    assert "result" in resp
    content = resp["result"]["content"]
    assert len(content) > 0
    parsed_res = json.loads(content[0]["text"])
    assert parsed_res["total_jobs"] == 2
    assert parsed_res["max_concurrency_before"] == 2


def test_fleet_cli_subcommand(capsys):
    ret = cli_main(["fleet", "--demo", "--json"])
    assert ret == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["total_jobs"] >= 3
    assert "thundering_herd_score_before" in data

    # Crontab output flag
    ret_crontab = cli_main(["fleet", "--demo", "--crontab"])
    assert ret_crontab == 0
    out_crontab = capsys.readouterr().out
    assert "# Optimized Desynchronized Fleet Crontab" in out_crontab

    # Kubernetes manifests flag
    ret_k8s = cli_main(["fleet", "--demo", "--k8s"])
    assert ret_k8s == 0
    out_k8s = capsys.readouterr().out
    assert "kind: CronJob" in out_k8s
