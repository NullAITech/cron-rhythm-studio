"""Complete Model Context Protocol (MCP) Server for cron-rhythm-studio.

Implements JSON-RPC 2.0 protocol handling over stdio and programmatic interfaces.
Provides registered Tools, Resources, and Prompts for parsing, calculating execution
timelines, synthesizing natural English translations, transpiling across 6 cron dialects,
analyzing 24x7 rhythm heatmap matrices, and querying production schedule templates.
"""

from __future__ import annotations

import json
import os
import platform
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from .catalog import PRESETS, get_categories, get_preset, list_presets, search_presets
from .compat import PlatformInfo, get_platform_info
from .fleet_optimizer import audit_cron_fleet
from .humanizer import explain_cron_parts, humanize_cron
from .models import (
    CronFieldType,
    CronPreset,
    CronScheduleAST,
    CronTargetFormat,
    NextRunItem,
    RhythmMatrixReport,
    TranspileResult,
)
from .parser import parse_cron, tokenize_cron, validate_cron
from .rhythm_matrix import generate_rhythm_matrix, render_ascii_rhythm_matrix
from .timeline_engine import next_run, next_runs
from .timezone_auditor import audit_dst_anomalies, project_world_flight_board
from .transpiler import transpile_all, transpile_cron

# MCP Protocol Version and Metadata
PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "cron-rhythm-studio"
SERVER_VERSION = "0.1.0"

# Target format mapping helper
_TARGET_FORMAT_MAP: Dict[str, CronTargetFormat] = {
    "unix": CronTargetFormat.UNIX,
    "crontab": CronTargetFormat.UNIX,
    "vixie": CronTargetFormat.UNIX,
    "posix": CronTargetFormat.UNIX,
    "quartz": CronTargetFormat.QUARTZ,
    "aws": CronTargetFormat.AWS_EVENTBRIDGE,
    "aws_eventbridge": CronTargetFormat.AWS_EVENTBRIDGE,
    "cloudwatch": CronTargetFormat.AWS_EVENTBRIDGE,
    "systemd": CronTargetFormat.SYSTEMD_TIMER,
    "systemd_timer": CronTargetFormat.SYSTEMD_TIMER,
    "oncalendar": CronTargetFormat.SYSTEMD_TIMER,
    "github": CronTargetFormat.GITHUB_ACTIONS,
    "github_actions": CronTargetFormat.GITHUB_ACTIONS,
    "gha": CronTargetFormat.GITHUB_ACTIONS,
    "kubernetes": CronTargetFormat.KUBERNETES,
    "k8s": CronTargetFormat.KUBERNETES,
    "cronjob": CronTargetFormat.KUBERNETES,
}


def _resolve_target_format(fmt_str: str) -> CronTargetFormat:
    """Normalize string input to CronTargetFormat enum."""
    normalized = fmt_str.strip().lower().replace(" ", "_").replace("-", "_")
    if normalized in _TARGET_FORMAT_MAP:
        return _TARGET_FORMAT_MAP[normalized]
    try:
        return CronTargetFormat(normalized)
    except ValueError:
        return CronTargetFormat.UNIX


# ============================================================================
# Registered Tools Definitions & Implementations
# ============================================================================

TOOL_DEFINITIONS = [
    {
        "name": "cron_parse_expression",
        "description": "Parse a cron string into a structured Abstract Syntax Tree (AST) with validity checks, matched integer sets, token breakdowns, and human description.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Cron expression to parse (e.g., '*/15 * * * *', '0 0 1 1 *', '@daily', '0 30 9 ? * MON-FRI *')."
                },
                "syntax": {
                    "type": "string",
                    "description": "Optional syntax engine hint ('unix', 'quartz', 'aws', 'systemd', 'github', 'kubernetes'). Default: 'unix'.",
                    "default": "unix"
                }
            },
            "required": ["expression"]
        }
    },
    {
        "name": "cron_next_runs",
        "description": "Calculate the next N upcoming execution datetimes with human-readable relative countdowns (e.g., 'in 14 minutes', 'in 2 days, 3 hours').",
        "inputSchema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Cron expression to evaluate."
                },
                "count": {
                    "type": "integer",
                    "description": "Number of upcoming executions to compute (1 to 100). Default: 10.",
                    "default": 10
                },
                "start_time": {
                    "type": "string",
                    "description": "Optional starting ISO-8601 reference datetime (e.g., '2026-09-17T00:00:00Z'). Defaults to current time."
                },
                "timezone": {
                    "type": "string",
                    "description": "Optional timezone name (e.g., 'UTC', 'America/New_York', 'Europe/London'). Default: 'UTC'.",
                    "default": "UTC"
                }
            },
            "required": ["expression"]
        }
    },
    {
        "name": "cron_humanize",
        "description": "Convert a cron expression into a natural, grammatically correct human English sentence with per-field cadence breakdown.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Cron expression to translate."
                },
                "syntax": {
                    "type": "string",
                    "description": "Optional syntax dialect ('unix', 'quartz', 'aws', 'systemd', 'github', 'kubernetes'). Default: 'unix'.",
                    "default": "unix"
                }
            },
            "required": ["expression"]
        }
    },
    {
        "name": "cron_transpile",
        "description": "Convert a cron expression between UNIX Crontab, Quartz Scheduler, AWS EventBridge, Systemd OnCalendar, GitHub Actions, and Kubernetes CronJob formats with compatibility notes and caveats.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Source cron expression."
                },
                "to_syntax": {
                    "type": "string",
                    "description": "Target syntax engine ('unix', 'quartz', 'aws', 'systemd', 'github', 'kubernetes').",
                    "enum": ["unix", "quartz", "aws", "systemd", "github", "kubernetes"]
                },
                "from_syntax": {
                    "type": "string",
                    "description": "Optional source syntax dialect (default: 'unix').",
                    "default": "unix"
                }
            },
            "required": ["expression", "to_syntax"]
        }
    },
    {
        "name": "cron_rhythm_matrix",
        "description": "Generate a 24x7 weekly rhythm heatmap matrix, execution counts, peak congestion slots, and collision risk analysis for one or multiple cron expressions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Single cron expression or newline/semicolon/comma-separated multiple cron expressions."
                },
                "granularity": {
                    "type": "string",
                    "description": "Analysis granularity ('hourly' for 24x7 matrix, 'slot' for minute slots). Default: 'hourly'.",
                    "default": "hourly"
                },
                "collision_threshold": {
                    "type": "integer",
                    "description": "Threshold of simultaneous overlapping jobs considered a collision. Default: 2.",
                    "default": 2
                }
            },
            "required": ["expression"]
        }
    },
    {
        "name": "cron_list_presets",
        "description": "List curated battle-tested production schedule templates with expressions, descriptions, categories, and tags (e.g. Backups, DevOps, Security, Housekeeping, Monitoring, Finance).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Optional filter by category (e.g., 'Database & Storage', 'DevOps & CI/CD', 'Security & SSL', 'Housekeeping', 'Monitoring', 'Billing & Finance', 'Emails & Notifications', 'Performance', 'IoT & Edge')."
                },
                "search": {
                    "type": "string",
                    "description": "Optional keyword search string to filter presets by name, description, expression, or tags."
                }
            }
        }
    },
    {
        "name": "cron_diagnostics",
        "description": "Run a multi-OS diagnostics check reporting operating system, Python runtime, timezone resolution, scheduler capabilities, and storage sync status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "check_system_time": {
                    "type": "boolean",
                    "description": "Whether to verify system clock, drift, and UTC offset. Default: true.",
                    "default": True
                }
            }
        }
    },
    {
        "name": "cron_calculate_timeline",
        "description": "Calculate upcoming execution datetimes with relative countdowns (alias for cron_next_runs).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Cron expression to evaluate."
                },
                "count": {
                    "type": "integer",
                    "description": "Number of upcoming executions to compute. Default: 10.",
                    "default": 10
                }
            },
            "required": ["expression"]
        }
    },
    {
        "name": "cron_preset_catalog",
        "description": "List curated schedule templates (alias for cron_list_presets).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Optional filter by category."
                }
            }
        }
    },
    {
        "name": "cron_audit_fleet",
        "description": "Audit a fleet of cron jobs for simultaneous execution spikes and thundering herd risks, and compute optimal phase-shifted schedules.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "jobs": {
                    "type": "object",
                    "description": "Mapping of job names to cron expressions, e.g. {'db_backup': '0 0 * * *', 'email_digest': '0 0 * * *'}."
                },
                "horizon_hours": {
                    "type": "integer",
                    "description": "Analysis horizon window in hours (default: 24).",
                    "default": 24
                },
                "auto_rebalance": {
                    "type": "boolean",
                    "description": "Whether to calculate optimal desynchronized schedules. Default: true.",
                    "default": True
                },
                "max_shift_minutes": {
                    "type": "integer",
                    "description": "Maximum allowed minute phase shift offset. Default: 25.",
                    "default": 25
                }
            },
            "required": ["jobs"]
        }
    },
    {
        "name": "cron_audit_dst_anomalies",
        "description": "Audit a cron expression for Daylight Saving Time (DST) clock discontinuities (skipped executions at Spring Forward, duplicate runs at Fall Back) with safe alternative recommendations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Cron expression to audit (e.g. '0 2 * * *', '30 2 * * 0', '0 1 * * *')."
                },
                "timezone": {
                    "type": "string",
                    "description": "Target timezone to evaluate (default: 'America/New_York').",
                    "default": "America/New_York"
                },
                "reference_year": {
                    "type": "integer",
                    "description": "Reference calendar year for transition dates (default: current year)."
                }
            },
            "required": ["expression"]
        }
    },
    {
        "name": "cron_multizone_flight_board",
        "description": "Synchronize and project upcoming cron execution times across major global tech hubs (UTC, NYC, SF, London, Berlin, Tokyo, Sydney) with business-hour indicator.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Cron expression to evaluate (e.g. '0 14 * * 1-5')."
                },
                "home_timezone": {
                    "type": "string",
                    "description": "Home timezone (default: 'America/New_York').",
                    "default": "America/New_York"
                },
                "count": {
                    "type": "integer",
                    "description": "Number of upcoming runs to project across hubs (default: 3).",
                    "default": 3
                },
                "start_time": {
                    "type": "string",
                    "description": "Optional starting ISO-8601 reference datetime."
                }
            },
            "required": ["expression"]
        }
    }
]


def _execute_tool_parse(arguments: Dict[str, Any]) -> Dict[str, Any]:
    expr = arguments.get("expression", "").strip()
    syntax = arguments.get("syntax", "unix")
    ast = parse_cron(expr)

    if not ast.is_valid:
        return {
            "is_valid": False,
            "expression": expr,
            "error": ast.error_message or "Invalid cron syntax",
            "raw_tokens": ast.raw_tokens,
        }

    parts = explain_cron_parts(ast)
    human_summary = humanize_cron(ast)

    return {
        "is_valid": True,
        "expression": ast.expression,
        "human_summary": human_summary,
        "fields": {k: sorted(list(v)) for k, v in ast.fields.items()},
        "raw_tokens": ast.raw_tokens,
        "has_seconds": ast.has_seconds,
        "has_year": ast.has_year,
        "is_reboot": ast.is_reboot,
        "special_dom": ast.special_dom,
        "special_dow": ast.special_dow,
        "field_explanations": parts,
    }


def _execute_tool_next_runs(arguments: Dict[str, Any]) -> Dict[str, Any]:
    expr = arguments.get("expression", "").strip()
    count = int(arguments.get("count", 10))
    count = max(1, min(count, 100))
    start_time_str = arguments.get("start_time")
    tz_name = arguments.get("timezone", "UTC")

    start_dt: Optional[datetime] = None
    if start_time_str:
        try:
            # Handle ISO string with trailing Z or timezone offset
            clean_str = start_time_str.replace("Z", "+00:00")
            start_dt = datetime.fromisoformat(clean_str)
        except Exception as e:
            return {
                "error": f"Invalid start_time ISO-8601 format: {e}",
                "expression": expr,
            }

    ast = parse_cron(expr)
    if not ast.is_valid:
        return {
            "error": f"Cannot calculate next runs for invalid cron: {ast.error_message}",
            "expression": expr,
        }

    if ast.is_reboot:
        return {
            "expression": expr,
            "human_summary": "Run once at system startup / reboot",
            "runs": [],
            "note": "@reboot triggers upon system boot rather than on a recurring schedule.",
        }

    runs = next_runs(ast, count=count, start_time=start_dt, tz_name=tz_name)
    human_summary = humanize_cron(ast)

    return {
        "expression": ast.expression,
        "human_summary": human_summary,
        "timezone": tz_name,
        "count_requested": count,
        "count_returned": len(runs),
        "upcoming_runs": [r.to_dict() for r in runs],
    }


def _execute_tool_humanize(arguments: Dict[str, Any]) -> Dict[str, Any]:
    expr = arguments.get("expression", "").strip()
    syntax = arguments.get("syntax", "unix")
    ast = parse_cron(expr)

    if not ast.is_valid:
        return {
            "is_valid": False,
            "expression": expr,
            "error": ast.error_message or "Invalid cron syntax",
        }

    summary = humanize_cron(ast)
    parts = explain_cron_parts(ast)

    return {
        "is_valid": True,
        "expression": ast.expression,
        "human_sentence": summary,
        "field_breakdown": parts,
    }


def _execute_tool_transpile(arguments: Dict[str, Any]) -> Dict[str, Any]:
    expr = arguments.get("expression", "").strip()
    to_syntax_str = arguments.get("to_syntax") or arguments.get("target_syntax", "unix")
    from_syntax_str = arguments.get("from_syntax", "unix")

    # Handle "all" target: return all transpilations
    if to_syntax_str == "all":
        all_results = transpile_all(expr)
        _key_map = {
            "unix": "unix",
            "quartz": "quartz",
            "aws_eventbridge": "aws_eventbridge",
            "systemd_timer": "systemd_timer",
            "github_actions": "github_actions",
            "kubernetes": "kubernetes",
        }
        transpiled: Dict[str, Any] = {}
        for fmt, res in all_results.items():
            key = fmt.value.lower().replace("-", "_").replace(" ", "_")
            transpiled[key] = res.output_syntax
        return {
            "source_expression": expr,
            "target_format": "all",
            "transpiled": transpiled,
            "is_valid": True,
        }

    target_fmt = _resolve_target_format(to_syntax_str)
    res = transpile_cron(expr, target_fmt)

    return {
        "source_expression": expr,
        "target_format": res.target_format.value,
        "output_syntax": res.output_syntax,
        "explanation": res.explanation,
        "warnings": res.warnings,
        "is_valid": bool(res.output_syntax),
    }


def _execute_tool_rhythm_matrix(arguments: Dict[str, Any]) -> Dict[str, Any]:
    raw_expr = arguments.get("expression", "").strip()
    granularity = arguments.get("granularity", "hourly")
    collision_thresh = int(arguments.get("collision_threshold", 2))

    # Split multiple expressions if provided
    expressions = [e.strip() for e in re.split(r"[\n;,]+", raw_expr) if e.strip()]

    if not expressions:
        return {"error": "No cron expression provided"}

    if len(expressions) == 1:
        report = generate_rhythm_matrix(expressions[0])
        ascii_grid = render_ascii_rhythm_matrix(report)
        return {
            "expression": expressions[0],
            "total_weekly_runs": report.total_runs_per_week,
            "total_runs_per_week": report.total_runs_per_week,
            "runs_per_day": report.runs_per_day,
            "peak_hour": report.peak_hour,
            "collision_risks": report.collision_risks,
            "ascii_heatmap": ascii_grid,
            "ascii_matrix": ascii_grid,
            "matrix": [[cell.to_dict() for cell in row] for row in report.matrix],
        }

    # Multiple expressions aggregation
    reports = [generate_rhythm_matrix(e) for e in expressions]
    total_runs = sum(r.total_runs_per_week for r in reports)
    day_totals = {d: 0 for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}
    for r in reports:
        for d, count in r.runs_per_day.items():
            day_totals[d] += count

    # Combined 7x24 collision map
    collision_slots: List[Dict[str, Any]] = []
    combined_counts: List[List[int]] = [[0 for _ in range(24)] for _ in range(7)]

    for r_idx, rep in enumerate(reports):
        for d_idx in range(7):
            for h_idx in range(24):
                if rep.matrix[d_idx][h_idx].execution_count > 0:
                    combined_counts[d_idx][h_idx] += 1

    days_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for d_idx in range(7):
        for h_idx in range(24):
            overlap_count = combined_counts[d_idx][h_idx]
            if overlap_count >= collision_thresh:
                collision_slots.append({
                    "day": days_labels[d_idx],
                    "hour": h_idx,
                    "overlapping_jobs_count": overlap_count,
                })

    primary_ascii = render_ascii_rhythm_matrix(reports[0])

    return {
        "expressions": expressions,
        "total_weekly_runs_all_jobs": total_runs,
        "runs_per_day": day_totals,
        "job_count": len(expressions),
        "collision_threshold": collision_thresh,
        "detected_collisions_count": len(collision_slots),
        "collision_hotspots": collision_slots,
        "primary_job_ascii_heatmap": primary_ascii,
    }


def _execute_tool_list_presets(arguments: Dict[str, Any]) -> Dict[str, Any]:
    cat = arguments.get("category")
    search_term = arguments.get("search")

    if search_term:
        results = search_presets(search_term)
        if cat:
            cat_l = cat.strip().lower()
            results = [p for p in results if p.category.lower() == cat_l]
    else:
        results = list_presets(cat)

    presets_data = []
    for p in results:
        ast = parse_cron(p.expression)
        human = humanize_cron(ast) if ast.is_valid else p.description
        presets_data.append({
            "id": p.id,
            "title": p.title,
            "category": p.category,
            "expression": p.expression,
            "human_summary": human,
            "description": p.description,
            "tags": p.tags,
        })

    return {
        "categories_available": get_categories(),
        "total_matching": len(presets_data),
        "presets": presets_data,
    }


def _execute_tool_diagnostics(arguments: Dict[str, Any]) -> Dict[str, Any]:
    plat_info = get_platform_info()
    now_utc = datetime.now(timezone.utc)
    now_local = datetime.now()

    return {
        "server": {
            "name": SERVER_NAME,
            "version": SERVER_VERSION,
            "protocol_version": PROTOCOL_VERSION,
        },
        "platform": plat_info.as_dict(),
        "time": {
            "utc_iso": now_utc.isoformat(),
            "local_iso": now_local.isoformat(),
            "timestamp": time.time(),
        },
        "capabilities": {
            "supported_dialects": [fmt.value for fmt in CronTargetFormat],
            "preset_count": len(PRESETS),
            "timezone_aware": True,
            "fsync_atomic_write": plat_info.supports_fsync,
        },
    }



def _execute_tool_audit_fleet(arguments: Dict[str, Any]) -> Dict[str, Any]:
    raw_jobs = arguments.get("jobs", {})
    horizon = int(arguments.get("horizon_hours", 24))
    auto_rebal = bool(arguments.get("auto_rebalance", True))
    max_shift = int(arguments.get("max_shift_minutes", 25))

    start_time_arg = arguments.get("start_time")
    start_dt = None
    if start_time_arg:
        try:
            start_dt = datetime.fromisoformat(str(start_time_arg).replace("Z", "+00:00"))
        except Exception:
            start_dt = None

    jobs: Dict[str, str] = {}
    if isinstance(raw_jobs, dict):
        jobs = {str(k): str(v) for k, v in raw_jobs.items()}
    elif isinstance(raw_jobs, list):
        for idx, item in enumerate(raw_jobs):
            if isinstance(item, dict) and "expression" in item:
                name = item.get("name", f"job_{idx + 1}")
                jobs[name] = str(item["expression"])
            elif isinstance(item, str):
                jobs[f"job_{idx + 1}"] = item

    report = audit_cron_fleet(
        jobs=jobs,
        horizon_hours=horizon,
        auto_rebalance=auto_rebal,
        max_shift_minutes=max_shift,
        start_time=start_dt,
    )
    return report.to_dict()


def _execute_tool_audit_dst(arguments: Dict[str, Any]) -> Dict[str, Any]:
    expr = str(arguments.get("expression", "")).strip()
    tz_name = str(arguments.get("timezone", "America/New_York")).strip()
    ref_year = arguments.get("reference_year")
    if ref_year is not None:
        try:
            ref_year = int(ref_year)
        except (ValueError, TypeError):
            ref_year = None
    report = audit_dst_anomalies(expr, tz_name=tz_name, reference_year=ref_year)
    return report.to_dict()


def _execute_tool_flight_board(arguments: Dict[str, Any]) -> Dict[str, Any]:
    expr = str(arguments.get("expression", "")).strip()
    home_tz = str(arguments.get("home_timezone", "America/New_York")).strip()
    count = int(arguments.get("count", 3))
    start_time_str = arguments.get("start_time")
    start_dt = None
    if start_time_str:
        try:
            start_dt = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
        except Exception:
            start_dt = None

    reports = project_world_flight_board(expr, home_tz=home_tz, run_count=count, start_time=start_dt)
    return {
        "expression": expr,
        "home_timezone": home_tz,
        "runs": [r.to_dict() for r in reports],
    }


# Tool Dispatcher Map
TOOL_HANDLERS = {
    "cron_parse_expression": _execute_tool_parse,
    "cron_next_runs": _execute_tool_next_runs,
    "cron_humanize": _execute_tool_humanize,
    "cron_transpile": _execute_tool_transpile,
    "cron_rhythm_matrix": _execute_tool_rhythm_matrix,
    "cron_list_presets": _execute_tool_list_presets,
    "cron_diagnostics": _execute_tool_diagnostics,
    "cron_calculate_timeline": _execute_tool_next_runs,
    "cron_preset_catalog": _execute_tool_list_presets,
    "cron_audit_fleet": _execute_tool_audit_fleet,
    "cron_audit_dst_anomalies": _execute_tool_audit_dst,
    "cron_multizone_flight_board": _execute_tool_flight_board,
}


# ============================================================================
# Registered Resources Definitions & Handlers
# ============================================================================

RESOURCE_DEFINITIONS = [
    {
        "uri": "cron://presets",
        "name": "Cron Schedule Presets Catalog",
        "description": "Curated catalog of 40+ production cron schedules spanning Database, DevOps, Security, Monitoring, Finance, Housekeeping, and IoT.",
        "mimeType": "application/json"
    },
    {
        "uri": "cron://specs/syntax-comparison",
        "name": "Cron Syntax Comparison Matrix across 6 Engines",
        "description": "Detailed specification comparison table and conversion rules across UNIX, Quartz, AWS EventBridge, Systemd OnCalendar, GitHub Actions, and Kubernetes.",
        "mimeType": "text/markdown"
    }
]

SYNTAX_COMPARISON_MARKDOWN = """# Cron Syntax Comparison Matrix across 6 Major Engines

| Feature / Engine | UNIX (Vixie / POSIX) | Quartz Scheduler | AWS EventBridge | Systemd OnCalendar | GitHub Actions | Kubernetes CronJob |
|---|---|---|---|---|---|---|
| **Field Count** | 5 fields | 6 or 7 fields | 6 fields | Calendar Spec string | 5 fields | 5 fields |
| **Field Order** | `min hr dom mon dow` | `sec min hr dom mon dow [yr]` | `min hr dom mon dow yr` | `dow yr-mo-day hr:min:sec` | `min hr dom mon dow` | `min hr dom mon dow` |
| **Seconds Support** | No | Yes (0-59) | No | Yes (0-59) | No | No |
| **Years Support** | No | Yes (1970-2099) | Yes (1970-2199) | Yes (YYYY) | No | No |
| **Day of Week Index** | 0-7 (0=Sun, 7=Sun) | 1-7 (1=Sun, 7=Sat) or Names | 1-7 (1=Sun, 7=Sat) or Names | Mon..Sun (Names) | 0-6 (0=Sun) | 0-7 (0=Sun) |
| **No-Specific-Value (?)** | Unsupported | Required in DOM or DOW | Required in DOM or DOW | N/A | Unsupported | Unsupported |
| **Last Day (L, LW, nL)** | Unsupported | Supported (`L`, `LW`, `5L`) | Supported (`L`, `5L`) | Partial (`*-*-~01`) | Unsupported | Unsupported |
| **Nth Occurrence (n#m)**| Unsupported | Supported (`2#1` = 1st Mon) | Supported (`2#1`) | Unsupported | Unsupported | Unsupported |
| **Nearest Weekday (nW)**| Unsupported | Supported (`15W`) | Supported (`15W`) | Unsupported | Unsupported | Unsupported |
| **Timezone Support** | System/CRON_TZ | Configured per Scheduler | UTC only (`cron(...)`) | Local / Systemd TZ | UTC strictly enforced | `spec.timeZone` (1.27+) |
| **Reboot Macro** | `@reboot` | Immediate Trigger | Event pattern | `OnBootSec=0` | `workflow_dispatch` | N/A |

### Engine Caveats & Operational Rules:
1. **AWS EventBridge**: Requires `?` in either Day-of-Month or Day-of-Week; specifying `*` in both produces a deployment syntax error.
2. **Quartz Scheduler**: When Day-of-Month is specified, Day-of-Week must be set to `?` (and vice-versa).
3. **GitHub Actions**: Runs exclusively in UTC. Workflows are queued and subject to runner availability delays; high frequency schedules (<5 min) are throttled.
4. **Systemd OnCalendar**: Employs ISO-like calendar expressions (`Mon..Fri *-*-* 09:00:00`) with persistent state catch-up (`Persistent=true`).
5. **Kubernetes CronJob**: Runs against the kube-controller-manager timezone unless explicit `spec.timeZone` is defined (GA in K8s v1.27).
"""


def _read_resource(uri: str) -> Optional[Dict[str, Any]]:
    clean_uri = uri.strip()
    if clean_uri == "cron://presets":
        catalog_items = [p.to_dict() for p in PRESETS]
        return {
            "uri": "cron://presets",
            "mimeType": "application/json",
            "text": json.dumps(catalog_items, indent=2, ensure_ascii=False),
        }
    elif clean_uri == "cron://specs/syntax-comparison":
        return {
            "uri": "cron://specs/syntax-comparison",
            "mimeType": "text/markdown",
            "text": SYNTAX_COMPARISON_MARKDOWN,
        }
    return None


# ============================================================================
# Registered Prompts Definitions & Handlers
# ============================================================================

PROMPT_DEFINITIONS = [
    {
        "name": "cron_schedule_planner_prompt",
        "description": "Assistant prompt for designing optimal periodic job schedules, avoiding thundering herd problems, load spikes, and maintenance collisions.",
        "arguments": [
            {
                "name": "job_description",
                "description": "What the scheduled workload accomplishes and its desired frequency or business requirement.",
                "required": True
            },
            {
                "name": "target_platform",
                "description": "Target scheduler platform ('unix', 'aws', 'kubernetes', 'github', 'systemd', 'quartz'). Default: 'unix'.",
                "required": False
            },
            {
                "name": "constraints",
                "description": "Operational constraints (e.g., 'avoid top of the hour midnight spike', 'business hours EST only', 'stagger after ETL batch').",
                "required": False
            }
        ]
    },
    {
        "name": "cron_conflict_resolver_prompt",
        "description": "Strategy prompt for rebalancing colliding cron jobs to eliminate resource contention on CPU, disk IOPS, and database connection pools.",
        "arguments": [
            {
                "name": "job_list",
                "description": "List of current cron expressions and their associated job descriptions.",
                "required": True
            },
            {
                "name": "new_job",
                "description": "New job schedule and description to integrate into the existing schedule.",
                "required": True
            },
            {
                "name": "max_parallel_jobs",
                "description": "Maximum number of simultaneous running jobs permitted on the infrastructure. Default: '2'.",
                "required": False
            }
        ]
    }
]


def _get_prompt(name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if name == "cron_schedule_planner_prompt":
        job_desc = arguments.get("job_description", "Periodic batch task")
        plat = arguments.get("target_platform", "unix")
        constraints = arguments.get("constraints", "None specified")

        user_content = f"""You are the principal Site Reliability Engineer and Cron Rhythm Architect.
Design an optimal, production-grade schedule for the following workload:

Job Description: {job_desc}
Target Platform: {plat}
Operational Constraints: {constraints}

Please provide:
1. Recommended Cron Expression (formatted for {plat}).
2. Natural English explanation of the schedule cadence.
3. Jitter / Phase Offset strategy to avoid top-of-the-hour thundering herds (e.g., choosing minute 17 or 43 rather than 00).
4. Failure recovery & retry timeout guidance.
5. Transpiled variations for Kubernetes CronJob and AWS EventBridge if applicable.
"""
        return {
            "description": "Production Cron Schedule Planning & Architecture Guide",
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": user_content.strip(),
                    }
                }
            ]
        }

    elif name == "cron_conflict_resolver_prompt":
        job_list = arguments.get("job_list", "")
        new_job = arguments.get("new_job", "")
        max_par = arguments.get("max_parallel_jobs", "2")

        user_content = f"""You are the Cron Collision & Resource Contention Architect.
Analyze the following scheduled jobs for concurrency overlaps, disk IOPS spikes, and database lock collisions:

Existing Schedules:
{job_list}

New Job to Integrate:
{new_job}

Maximum Permissible Concurrent Jobs: {max_par}

Please provide:
1. Overlap Analysis: Identify specific hour/minute slots where simultaneous jobs collide.
2. Contention Assessment: Evaluate CPU, RAM, Network Egress, and Database lock risks.
3. Rebalanced Schedule: Provide adjusted cron expressions with staggered phase offsets.
4. 24x7 Weekly Rhythm Matrix verification showing smooth distribution across all 168 weekly hours.
"""
        return {
            "description": "Cron Collision Resolution & Schedule Rebalancing",
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": user_content.strip(),
                    }
                }
            ]
        }

    return None


# ============================================================================
# JSON-RPC 2.0 Protocol Handler & Message Processing
# ============================================================================

def handle_jsonrpc_request(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Handle a single parsed JSON-RPC 2.0 request dictionary.
    
    Args:
        request: Parsed JSON dictionary containing 'jsonrpc', 'method', 'id', 'params'.
        
    Returns:
        JSON-RPC 2.0 response dictionary, or None for notifications.
    """
    if not isinstance(request, dict):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "Invalid Request: expected JSON object"},
        }

    req_id = request.get("id")
    method = request.get("method")
    params = request.get("params", {}) or {}

    if method is None:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32600, "message": "Invalid Request: missing method name"},
        }

    # 1. Handshake: initialize
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                    "prompts": {"listChanged": False},
                    "logging": {},
                },
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION,
                },
            },
        }

    # 2. Handshake: notifications/initialized (notification - no response if req_id is None)
    if method == "notifications/initialized":
        if req_id is not None:
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}
        return None

    # 3. Liveness: ping
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    # 4. Tools: tools/list
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOL_DEFINITIONS},
        }

    # 5. Tools: tools/call
    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {}) or {}

        if tool_name not in TOOL_HANDLERS:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": f"Tool not found: '{tool_name}'",
                },
            }

        handler = TOOL_HANDLERS[tool_name]
        try:
            tool_result = handler(arguments)
            formatted_text = json.dumps(tool_result, indent=2, ensure_ascii=False)
            is_error = bool(tool_result.get("error") or tool_result.get("is_valid") is False)

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": formatted_text,
                        }
                    ],
                    "isError": is_error,
                },
            }
        except Exception as exc:
            err_msg = f"Error executing tool '{tool_name}': {str(exc)}\n{traceback.format_exc()}"
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": err_msg}],
                    "isError": True,
                },
            }

    # 6. Resources: resources/list
    if method == "resources/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"resources": RESOURCE_DEFINITIONS},
        }

    # 7. Resources: resources/read
    if method == "resources/read":
        uri = params.get("uri")
        if not uri:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": "Missing 'uri' parameter"},
            }

        res = _read_resource(uri)
        if res is None:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": f"Resource not found: '{uri}'"},
            }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"contents": [res]},
        }

    # 8. Prompts: prompts/list
    if method == "prompts/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"prompts": PROMPT_DEFINITIONS},
        }

    # 9. Prompts: prompts/get
    if method == "prompts/get":
        prompt_name = params.get("name")
        args = params.get("arguments", {}) or {}
        if not prompt_name:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": "Missing 'name' parameter"},
            }

        p_res = _get_prompt(prompt_name, args)
        if p_res is None:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": f"Prompt not found: '{prompt_name}'"},
            }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": p_res,
        }

    # Unknown Method
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: '{method}'"},
    }


def process_request(request: Union[str, Dict[str, Any]]) -> Union[str, Dict[str, Any]]:
    """Process a raw JSON string or dictionary request and return appropriate output."""
    if isinstance(request, str):
        try:
            parsed = json.loads(request)
        except json.JSONDecodeError as exc:
            err_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {str(exc)}"},
            }
            return json.dumps(err_resp)

        resp = handle_jsonrpc_request(parsed)
        if resp is None:
            return ""
        return json.dumps(resp, ensure_ascii=False)

    elif isinstance(request, dict):
        resp = handle_jsonrpc_request(request)
        return resp if resp is not None else {}

    raise ValueError("Request must be a JSON string or dict")


# Alias for backwards/test compatibility
handle_jsonrpc_message = handle_jsonrpc_request


def run_stdio_server() -> None:
    """Run the MCP server over standard input/output (stdio) streams."""
    # Ensure stdout is in unbuffered or line-buffered mode
    sys.stderr.write(f"[{SERVER_NAME} v{SERVER_VERSION}] MCP stdio server starting...\n")
    sys.stderr.flush()

    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break  # EOF

            trimmed = line.strip()
            if not trimmed:
                continue

            # Handle possible HTTP-style Content-Length header framing if present
            if trimmed.lower().startswith("content-length:"):
                try:
                    length = int(trimmed.split(":", 1)[1].strip())
                    # Consume empty line
                    _ = sys.stdin.readline()
                    body = sys.stdin.read(length)
                    trimmed = body.strip()
                except Exception:
                    pass

            response_str = process_request(trimmed)
            if response_str:
                sys.stdout.write(str(response_str) + "\n")
                sys.stdout.flush()

        except KeyboardInterrupt:
            break
        except Exception as err:
            sys.stderr.write(f"[{SERVER_NAME}] Fatal stdio error: {err}\n")
            sys.stderr.flush()
            break

    sys.stderr.write(f"[{SERVER_NAME}] MCP stdio server shut down cleanly.\n")
    sys.stderr.flush()


if __name__ == "__main__":
    run_stdio_server()
