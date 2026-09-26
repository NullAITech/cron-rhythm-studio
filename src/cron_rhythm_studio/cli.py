"""Multi-OS Command Line Interface & Web Studio Server for cron-rhythm-studio.

Provides subcommands for parsing, next-run calculation, natural English translation,
multi-dialect transpilation, 24x7 rhythm heatmap analysis, preset querying, MCP server,
system diagnostics doctor, self-verification testing, and Material 3-influenced Web UI.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import pathlib
import platform
import re
import socketserver
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from .catalog import PRESETS, get_categories, get_preset, list_presets, search_presets
from .compat import PlatformInfo, get_platform_info
from .humanizer import explain_cron_parts, humanize_cron
from .mcp_server import (
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    handle_jsonrpc_request,
    process_request,
    run_stdio_server,
)
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
from .timeline_engine import next_run, next_runs, prev_run
from .timezone_auditor import (
    audit_dst_anomalies,
    project_world_flight_board,
    render_ascii_dst_report,
    render_ascii_world_board,
)
from .transpiler import transpile_all, transpile_cron

__version__ = "0.1.0"


# ============================================================================
# Terminal Color & Styling Utilities
# ============================================================================

class TerminalStyler:
    """Handles ANSI escape sequences with automatic disable on non-TTY / NO_COLOR."""

    def __init__(self, enabled: bool = True):
        # Disable colors if NO_COLOR env is set or stdout is not a TTY
        no_color_env = bool(os.environ.get("NO_COLOR", "").strip())
        is_tty = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
        self.enabled = enabled and is_tty and not no_color_env

        # Windows ANSI support setup
        if self.enabled and platform.system() == "Windows":
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
            except Exception:
                pass

    def _wrap(self, code: str, text: str) -> str:
        if not self.enabled:
            return str(text)
        return f"\033[{code}m{text}\033[0m"

    def bold(self, text: str) -> str:
        return self._wrap("1", text)

    def dim(self, text: str) -> str:
        return self._wrap("2", text)

    def italic(self, text: str) -> str:
        return self._wrap("3", text)

    def underline(self, text: str) -> str:
        return self._wrap("4", text)

    def red(self, text: str) -> str:
        return self._wrap("31", text)

    def green(self, text: str) -> str:
        return self._wrap("32", text)

    def yellow(self, text: str) -> str:
        return self._wrap("33", text)

    def blue(self, text: str) -> str:
        return self._wrap("34", text)

    def magenta(self, text: str) -> str:
        return self._wrap("35", text)

    def cyan(self, text: str) -> str:
        return self._wrap("36", text)

    def white(self, text: str) -> str:
        return self._wrap("37", text)

    def success(self, text: str) -> str:
        return self.green(f"✔ {text}")

    def failure(self, text: str) -> str:
        return self.red(f"✘ {text}")

    def warning(self, text: str) -> str:
        return self.yellow(f"⚠ {text}")

    def info(self, text: str) -> str:
        return self.cyan(f"ℹ {text}")


# Default global styler
styler = TerminalStyler()


# ============================================================================
# Subcommand Handlers
# ============================================================================

def cmd_parse(args: argparse.Namespace) -> int:
    """Handler for `parse` subcommand."""
    expr = args.expression.strip()
    ast = parse_cron(expr)

    if args.json:
        out_dict = ast.to_dict()
        if ast.is_valid:
            out_dict["human_summary"] = humanize_cron(ast)
            out_dict["field_explanations"] = explain_cron_parts(ast)
        print(json.dumps(out_dict, indent=2, ensure_ascii=False))
        return 0 if ast.is_valid else 1

    if not ast.is_valid:
        print(styler.failure(f"Invalid Cron Expression: '{expr}'"))
        print(f"  {styler.red('Error:')} {ast.error_message or 'Syntax error'}")
        if ast.raw_tokens:
            print(f"  {styler.dim('Tokens parsed:')} {ast.raw_tokens}")
        return 1

    print(styler.bold(styler.cyan("\n=== Cron Expression AST Breakdown ===")))
    print(f"  {styler.bold('Expression:')}   {styler.green(ast.expression)}")
    print(f"  {styler.bold('Humanized:')}    {styler.yellow(humanize_cron(ast))}")
    print(f"  {styler.bold('Status:')}       {styler.green('VALID')}")

    fields_meta = [
        ("Second", CronFieldType.SECOND.value, ast.has_seconds),
        ("Minute", CronFieldType.MINUTE.value, True),
        ("Hour", CronFieldType.HOUR.value, True),
        ("Day of Month", CronFieldType.DAY_OF_MONTH.value, True),
        ("Month", CronFieldType.MONTH.value, True),
        ("Day of Week", CronFieldType.DAY_OF_WEEK.value, True),
        ("Year", CronFieldType.YEAR.value, ast.has_year),
    ]

    print(f"\n  {'Field':<16} {'Specified Token':<18} {'Matched Integer Values / Rules'}")
    print("  " + "─" * 72)

    tok_idx = 0
    for label, f_key, is_present in fields_meta:
        if not is_present:
            continue
        token_str = ast.raw_tokens[tok_idx] if tok_idx < len(ast.raw_tokens) else "*"
        tok_idx += 1

        vals = sorted(list(ast.fields.get(f_key, set())))
        if f_key == CronFieldType.DAY_OF_MONTH.value and ast.special_dom:
            val_desc = f"Special rule: {ast.special_dom}"
        elif f_key == CronFieldType.DAY_OF_WEEK.value and ast.special_dow:
            val_desc = f"Special rule: {ast.special_dow}"
        elif len(vals) > 15:
            val_desc = f"[{vals[0]}..{vals[-1]}] ({len(vals)} values)"
        else:
            val_desc = str(vals)

        print(f"  {styler.bold(label):<24} {styler.cyan(token_str):<26} {val_desc}")

    print("  " + "─" * 72 + "\n")
    return 0


def cmd_next(args: argparse.Namespace) -> int:
    """Handler for `next` subcommand."""
    expr = args.expression.strip()
    count = max(1, min(args.count, 100))
    tz_name = args.timezone or "UTC"

    start_dt: Optional[datetime] = None
    if args.start:
        try:
            clean_str = args.start.replace("Z", "+00:00")
            start_dt = datetime.fromisoformat(clean_str)
        except Exception as e:
            if args.json:
                print(json.dumps({"error": f"Invalid start datetime: {e}"}))
            else:
                print(styler.failure(f"Invalid start datetime '{args.start}': {e}"))
            return 1

    ast = parse_cron(expr)
    if not ast.is_valid:
        if args.json:
            print(json.dumps({"error": ast.error_message or "Invalid cron expression"}))
        else:
            print(styler.failure(f"Cannot calculate next runs: {ast.error_message}"))
        return 1

    if ast.is_reboot:
        if args.json:
            print(json.dumps({"expression": expr, "runs": [], "is_reboot": True}))
        else:
            print(styler.info(f"Expression '@reboot' runs only once upon system boot."))
        return 0

    runs = next_runs(ast, count=count, start_time=start_dt, tz_name=tz_name)

    if args.json:
        out = {
            "expression": expr,
            "timezone": tz_name,
            "count": len(runs),
            "upcoming_runs": [r.to_dict() for r in runs],
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    print(styler.bold(styler.cyan(f"\n=== Upcoming Execution Timeline ({len(runs)} Runs) ===")))
    print(f"  {styler.bold('Expression:')} {styler.green(expr)} ({styler.yellow(humanize_cron(ast))})")
    print(f"  {styler.bold('Timezone:')}   {tz_name}\n")
    print(f"  {'#':<4} {'ISO Datetime':<28} {'Day':<12} {'Relative Countdown'}")
    print("  " + "─" * 70)

    for r in runs:
        idx_str = f"{r.index + 1}."
        print(f"  {styler.dim(idx_str):<12} {styler.bold(r.datetime_iso):<36} {styler.cyan(r.day_name):<20} {styler.green(r.relative_delta)}")

    print("  " + "─" * 70 + "\n")
    return 0


def cmd_humanize(args: argparse.Namespace) -> int:
    """Handler for `humanize` subcommand."""
    expr = args.expression.strip()
    ast = parse_cron(expr)

    if not ast.is_valid:
        if args.json:
            print(json.dumps({"is_valid": False, "error": ast.error_message or "Invalid syntax"}))
        else:
            print(styler.failure(f"Cannot translate invalid cron: {ast.error_message}"))
        return 1

    summary = humanize_cron(ast)
    parts = explain_cron_parts(ast)

    if args.json:
        print(json.dumps({
            "is_valid": True,
            "expression": expr,
            "human_summary": summary,
            "field_breakdown": parts,
        }, indent=2, ensure_ascii=False))
        return 0

    print(styler.bold(styler.cyan("\n=== Cron Rhythm Humanizer ===")))
    print(f"  {styler.bold('Expression:')} {styler.green(expr)}")
    print(f"  {styler.bold('Sentence:')}   {styler.bold(styler.yellow(summary))}\n")

    if parts:
        print("  " + styler.dim("Field Cadence Breakdown:"))
        for k, v in parts.items():
            if k != "summary" and k != "error":
                print(f"    • {styler.bold(k.replace('_', ' ').title()):<18}: {v}")
    print()
    return 0


def cmd_transpile(args: argparse.Namespace) -> int:
    """Handler for `transpile` subcommand."""
    expr = args.expression.strip()
    ast = parse_cron(expr)

    if not ast.is_valid:
        if args.json:
            print(json.dumps({"error": ast.error_message or "Invalid expression"}))
        else:
            print(styler.failure(f"Cannot transpile invalid expression: {ast.error_message}"))
        return 1

    target_arg = args.target.strip().lower() if args.target else "all"

    if target_arg != "all":
        target_fmt = _resolve_target_format_cli(target_arg)
        res = transpile_cron(ast, target_fmt)

        if args.json:
            print(json.dumps(res.to_dict(), indent=2, ensure_ascii=False))
            return 0

        print(styler.bold(styler.cyan(f"\n=== Transpilation Target: {res.target_format.value.upper()} ===")))
        print(f"  {styler.bold('Source:')}      {styler.dim(expr)}")
        print(f"  {styler.bold('Output:')}      {styler.bold(styler.green(res.output_syntax))}")
        print(f"  {styler.bold('Explanation:')} {res.explanation}")
        if res.warnings:
            print(f"  {styler.yellow('Caveats:')}")
            for w in res.warnings:
                print(f"    ⚠ {w}")
        print()
        return 0

    # Transpile to ALL formats
    all_res = transpile_all(ast)

    if args.json:
        out = {k.value: v.to_dict() for k, v in all_res.items()}
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    print(styler.bold(styler.cyan(f"\n=== Universal Dialect Transpiler across 6 Engines ===")))
    print(f"  {styler.bold('Source Expression:')} {styler.green(expr)} ({styler.yellow(humanize_cron(ast))})\n")

    for fmt, res in all_res.items():
        title = fmt.value.upper().replace("_", " ")
        print(f"  ┌─ {styler.bold(styler.magenta(title))}")
        # Format multi-line syntax cleanly
        for line in res.output_syntax.splitlines():
            print(f"  │  {styler.bold(styler.green(line))}")
        print(f"  │  {styler.dim(res.explanation)}")
        if res.warnings:
            for w in res.warnings:
                print(f"  │  {styler.yellow('⚠ ' + w)}")
        print("  └────────────────────────────────────────────────────────────")
    print()
    return 0


def _resolve_target_format_cli(name: str) -> CronTargetFormat:
    n = name.strip().lower().replace("-", "_")
    mapping = {
        "unix": CronTargetFormat.UNIX,
        "crontab": CronTargetFormat.UNIX,
        "quartz": CronTargetFormat.QUARTZ,
        "aws": CronTargetFormat.AWS_EVENTBRIDGE,
        "eventbridge": CronTargetFormat.AWS_EVENTBRIDGE,
        "systemd": CronTargetFormat.SYSTEMD_TIMER,
        "github": CronTargetFormat.GITHUB_ACTIONS,
        "gha": CronTargetFormat.GITHUB_ACTIONS,
        "kubernetes": CronTargetFormat.KUBERNETES,
        "k8s": CronTargetFormat.KUBERNETES,
    }
    return mapping.get(n, CronTargetFormat.UNIX)


def cmd_rhythm(args: argparse.Namespace) -> int:
    """Handler for `rhythm` subcommand."""
    expr = args.expression.strip()
    ast = parse_cron(expr)

    report = generate_rhythm_matrix(ast)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        return 0

    ascii_matrix = render_ascii_rhythm_matrix(report)
    print("\n" + ascii_matrix + "\n")
    return 0


def cmd_fleet(args: argparse.Namespace) -> int:
    """Handler for `fleet` subcommand: multi-job concurrency optimizer."""
    from .fleet_optimizer import audit_cron_fleet

    jobs: Dict[str, str] = {}
    if getattr(args, "demo", False):
        jobs = {
            "db-backup": "0 2 * * *",
            "log-rotate": "0 2 * * *",
            "elastic-sync": "0 2 * * *",
            "metrics-push": "0 * * * *",
            "cert-renewal": "0 0 1 * *",
        }
    elif getattr(args, "file", None):
        from .compat import read_json_safe
        data = read_json_safe(args.file)
        if isinstance(data, dict):
            jobs = {k: str(v) for k, v in data.items()}
    elif getattr(args, "jobs", None):
        try:
            parsed = json.loads(args.jobs)
            if isinstance(parsed, dict):
                jobs = {k: str(v) for k, v in parsed.items()}
        except Exception:
            for pair in args.jobs.split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    jobs[k.strip()] = v.strip()

    if not jobs:
        jobs = {
            "db-backup": "0 2 * * *",
            "log-rotate": "0 2 * * *",
            "metrics-push": "0 * * * *",
        }

    horizon = max(1, min(getattr(args, "horizon", 24) or 24, 168))
    report = audit_cron_fleet(jobs, horizon_hours=horizon, auto_rebalance=True)

    if getattr(args, "json", False):
        print(json.dumps(report.to_dict(), indent=2))
        return 0

    if getattr(args, "crontab", False):
        print(report.optimized_crontab)
        return 0

    if getattr(args, "k8s", False):
        print(report.kubernetes_manifests)
        return 0

    if getattr(args, "svg", None):
        from .compat import atomic_write_text
        atomic_write_text(args.svg, report.svg_concurrency_chart)
        print(f"Saved SVG concurrency chart to: {args.svg}")
        return 0

    print(styler.bold(styler.cyan(f"\n=== Cron Fleet Concurrency Optimizer (Thundering Herd Defense) ===")))
    print(f"  {styler.bold('Total Fleet Jobs:')}       {report.total_jobs}")
    print(f"  {styler.bold('Analysis Horizon:')}       {report.horizon_hours} hours")
    print(f"  {styler.bold('Peak Concurrency Before:')} {styler.red(str(report.max_concurrency_before))} simultaneous jobs")
    print(f"  {styler.bold('Peak Concurrency After:')}  {styler.green(str(report.max_concurrency_after))} simultaneous jobs")
    print(f"  {styler.bold('Risk Score Before:')}      {report.thundering_herd_score_before:.1f}%")
    print(f"  {styler.bold('Risk Score After:')}       {report.thundering_herd_score_after:.1f}%\n")

    if report.rebalance_suggestions:
        print(styler.bold("  Staggering Rebalance Suggestions:"))
        for s in report.rebalance_suggestions:
            print(f"    • {styler.bold(s.job_name)}: {styler.dim(s.original_expression)} → {styler.green(s.optimized_expression)} (+{s.shift_minutes}m)")
        print()

    print(report.ascii_concurrency_profile)
    print()
    return 0


def cmd_dst_audit(args: argparse.Namespace) -> int:
    """Handler for `dst-audit` subcommand."""
    expr = args.expression
    tz_name = getattr(args, "timezone", "America/New_York") or "America/New_York"
    year = getattr(args, "year", None)
    if year:
        try:
            year = int(year)
        except Exception:
            year = None

    report = audit_dst_anomalies(expr, tz_name=tz_name, reference_year=year)
    if getattr(args, "json", False):
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        return 0

    print(render_ascii_dst_report(report))
    print()
    return 0


def cmd_tz_board(args: argparse.Namespace) -> int:
    """Handler for `tz-board` subcommand."""
    expr = args.expression
    home_tz = getattr(args, "home_tz", "America/New_York") or "America/New_York"
    count = getattr(args, "count", 3) or 3

    reports = project_world_flight_board(expr, home_tz=home_tz, run_count=count)
    if getattr(args, "json", False):
        print(json.dumps([r.to_dict() for r in reports], indent=2, ensure_ascii=False))
        return 0

    for rep in reports:
        print(render_ascii_world_board(rep))
        print()
    return 0


def cmd_presets(args: argparse.Namespace) -> int:
    """Handler for `presets` subcommand."""
    cat = args.category
    query = args.search

    if query:
        results = search_presets(query)
        if cat:
            cat_l = cat.strip().lower()
            results = [p for p in results if p.category.lower() == cat_l]
    else:
        results = list_presets(cat)

    if args.json:
        print(json.dumps([p.to_dict() for p in results], indent=2, ensure_ascii=False))
        return 0

    print(styler.bold(styler.cyan(f"\n=== Curated Production Cron Schedule Presets ({len(results)} Found) ===")))
    if cat:
        print(f"  {styler.bold('Category filter:')} {cat}")
    if query:
        print(f"  {styler.bold('Search query:')}    '{query}'")
    print()

    for p in results:
        print(f"  ┌─ {styler.bold(styler.green(p.title))} [{styler.dim(p.id)}]")
        print(f"  │  {styler.bold('Schedule:')}    {styler.bold(styler.yellow(p.expression))} ({styler.cyan(p.category)})")
        print(f"  │  {styler.bold('Description:')} {p.description}")
        if p.tags:
            print(f"  │  {styler.dim('Tags:')}        {', '.join(p.tags)}")
        print("  └────────────────────────────────────────────────────────────")
    print()
    return 0


def cmd_diagnostics(args: argparse.Namespace) -> int:
    """Handler for `doctor` / `diagnostics` / `platform` subcommand."""
    plat_info = get_platform_info()
    now_utc = datetime.now(timezone.utc)
    now_local = datetime.now()

    diag = {
        "studio": {
            "version": __version__,
            "mcp_protocol": PROTOCOL_VERSION,
        },
        "platform": plat_info.as_dict(),
        "time": {
            "utc_iso": now_utc.isoformat(),
            "local_iso": now_local.isoformat(),
            "timestamp": time.time(),
        },
        "presets_loaded": len(PRESETS),
        "dialects": [fmt.value for fmt in CronTargetFormat],
    }

    if args.json:
        print(json.dumps(diag, indent=2, ensure_ascii=False))
        return 0

    print(styler.bold(styler.cyan("\n=== Cron Rhythm Studio Diagnostics Doctor ===")))
    print(f"  {styler.bold('Studio Version:')}     {__version__}")
    print(f"  {styler.bold('Operating System:')}   {plat_info.system} {plat_info.release} ({plat_info.machine})")
    print(f"  {styler.bold('Python Runtime:')}     {plat_info.python_version}")
    print(f"  {styler.bold('Current UTC Time:')}   {now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  {styler.bold('Local System Time:')}  {now_local.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  {styler.bold('Atomic Fsync:')}       {styler.green('Supported') if plat_info.supports_fsync else styler.yellow('Fallback')}")
    print(f"  {styler.bold('Preset Catalog:')}     {len(PRESETS)} production presets active")
    print(f"  {styler.bold('Dialect Support:')}    UNIX, Quartz, AWS, Systemd, GitHub Actions, Kubernetes")
    print(f"  {styler.bold('MCP JSON-RPC:')}       Ready on stdio transport (protocol {PROTOCOL_VERSION})")
    print(styler.success("\nAll internal subsystems nominal!\n"))
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    """Handler for `mcp` stdio server launch."""
    run_stdio_server()
    return 0


# ============================================================================
# Internal Self-Verification Test Suite
# ============================================================================

def cmd_test(args: argparse.Namespace) -> int:
    """Run internal test runner validating all subsystems."""
    print(styler.bold(styler.cyan("\n=== Cron Rhythm Studio Self-Verification Test Runner ===")))
    start_t = time.time()
    passed = 0
    failed = 0

    def check(name: str, condition: bool, extra: str = ""):
        nonlocal passed, failed
        if condition:
            passed += 1
            print(f"  {styler.green('✔ PASS')} {name}")
        else:
            failed += 1
            print(f"  {styler.red('✘ FAIL')} {name} {extra}")

    # 1. Parser Tests
    ast1 = parse_cron("*/15 * * * *")
    check("Parser: Step expression '*/15 * * * *'", ast1.is_valid and ast1.fields["minute"] == {0, 15, 30, 45})

    ast2 = parse_cron("@daily")
    check("Parser: Alias '@daily'", ast2.is_valid and ast2.fields["hour"] == {0} and ast2.fields["minute"] == {0})

    ast3 = parse_cron("@reboot")
    check("Parser: Macro '@reboot'", ast3.is_valid and ast3.is_reboot)

    ast4 = parse_cron("0 0 1 1 *")
    check("Parser: Specific date '0 0 1 1 *'", ast4.is_valid and ast4.fields["month"] == {1} and ast4.fields["day_of_month"] == {1})

    ast5 = parse_cron("0 9 * * MON-FRI")
    check("Parser: DOW name range 'MON-FRI'", ast5.is_valid and ast5.fields["day_of_week"] == {1, 2, 3, 4, 5})

    # 2. Humanizer Tests
    h1 = humanize_cron("0 0 * * *")
    check("Humanizer: Midnight every day", "12:00 AM" in h1 or "midnight" in h1.lower())

    h2 = humanize_cron("*/15 * * * *")
    check("Humanizer: Every 15 minutes", "every 15 minutes" in h2.lower())

    # 3. Timeline Engine Tests
    ref_dt = datetime(2026, 9, 17, 0, 0, 0)
    nr = next_run("0 0 * * *", start_time=ref_dt)
    check("Timeline: Next run after midnight", nr.day == 18 and nr.hour == 0 and nr.minute == 0)

    nrs = next_runs("0 12 * * *", count=5, start_time=ref_dt)
    check("Timeline: Next 5 runs count", len(nrs) == 5)

    # 4. Transpiler Tests
    tr_quartz = transpile_cron("0 0 * * *", CronTargetFormat.QUARTZ)
    check("Transpiler: Quartz 7-field conversion", "?" in tr_quartz.output_syntax and len(tr_quartz.output_syntax.split()) >= 6)

    tr_aws = transpile_cron("0 0 * * *", CronTargetFormat.AWS_EVENTBRIDGE)
    check("Transpiler: AWS EventBridge cron(...)", tr_aws.output_syntax.startswith("cron(") and "?" in tr_aws.output_syntax)

    tr_k8s = transpile_cron("0 0 * * *", CronTargetFormat.KUBERNETES)
    check("Transpiler: Kubernetes CronJob YAML", "apiVersion: batch/v1" in tr_k8s.output_syntax)

    # 5. Rhythm Matrix Tests
    rep = generate_rhythm_matrix("*/15 * * * *")
    check("Rhythm Matrix: 24x7 heatmap runs", rep.total_runs_per_week == (4 * 24 * 7))

    # 6. Catalog Presets Tests
    presets_all = list_presets()
    check("Catalog: Presets loaded count >= 40", len(presets_all) >= 40)

    db_presets = list_presets("Database & Storage")
    check("Catalog: Category filter 'Database & Storage'", len(db_presets) > 0)

    # 7. MCP JSON-RPC Tests
    rpc_init = handle_jsonrpc_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    check("MCP JSON-RPC: initialize handshake", rpc_init and "result" in rpc_init and rpc_init["result"]["serverInfo"]["name"] == SERVER_NAME)

    rpc_tools = handle_jsonrpc_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    check("MCP JSON-RPC: tools/list registered", rpc_tools and len(rpc_tools["result"]["tools"]) >= 7)

    rpc_call = handle_jsonrpc_request({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "cron_humanize", "arguments": {"expression": "0 0 * * *"}},
    })
    check("MCP JSON-RPC: tools/call execution", rpc_call and "content" in rpc_call["result"])

    elapsed = time.time() - start_t
    print("\n  " + "─" * 60)
    print(f"  {styler.bold('Results:')} {styler.green(f'{passed} Passed')} | {styler.red(f'{failed} Failed')} ({elapsed:.3f}s)")

    return 0 if failed == 0 else 1


# ============================================================================
# Material 3 Influenced Web UI & REST API Server
# ============================================================================

MATERIAL_WEB_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Cron Rhythm Studio - Interactive Schedule Studio & Transpiler</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Google+Sans:wght@400;500;700&family=JetBrains+Mono:wght@400;500;700&family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --md-sys-color-primary: #D0BCFF;
      --md-sys-color-on-primary: #381E72;
      --md-sys-color-primary-container: #4F378B;
      --md-sys-color-on-primary-container: #EADDFF;
      --md-sys-color-surface: #141218;
      --md-sys-color-on-surface: #E6E1E5;
      --md-sys-color-surface-variant: #2B2930;
      --md-sys-color-on-surface-variant: #CAC4D0;
      --md-sys-color-outline: #79747E;
      --md-sys-color-surface-container: #1D1B20;
      --md-sys-color-surface-container-high: #2B2930;
      --md-sys-color-tertiary: #EFB8C8;
      --md-sys-color-success: #A6D39F;
      --md-sys-color-warning: #FFD56B;
      --md-sys-color-error: #F2B8B5;
      --md-shape-corner-medium: 12px;
      --md-shape-corner-large: 16px;
      --md-shape-corner-full: 9999px;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--md-sys-color-surface);
      color: var(--md-sys-color-on-surface);
      font-family: 'Google Sans', 'Roboto', -apple-system, BlinkMacSystemFont, sans-serif;
      line-height: 1.5;
      padding-bottom: 60px;
    }

    header {
      background: linear-gradient(180deg, #211F26 0%, #141218 100%);
      border-bottom: 1px solid rgba(255,255,255,0.08);
      padding: 24px 32px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      position: sticky;
      top: 0;
      z-index: 100;
      backdrop-filter: blur(12px);
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 14px;
    }
    .brand-icon {
      width: 40px;
      height: 40px;
      background: linear-gradient(135deg, var(--md-sys-color-primary) 0%, #B69DF8 100%);
      border-radius: 12px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: #141218;
      font-weight: 700;
      font-size: 20px;
      box-shadow: 0 4px 16px rgba(208, 188, 255, 0.25);
    }
    .brand-title {
      font-size: 22px;
      font-weight: 700;
      letter-spacing: -0.5px;
      background: linear-gradient(90deg, #FFFFFF 0%, #D0BCFF 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }
    .brand-sub {
      font-size: 12px;
      color: var(--md-sys-color-on-surface-variant);
      margin-left: 4px;
    }

    .container {
      max-width: 1280px;
      margin: 0 auto;
      padding: 32px 24px;
      display: grid;
      grid-template-columns: 1fr;
      gap: 28px;
    }

    /* Hero Banner / Expression Card */
    .expression-card {
      background: var(--md-sys-color-surface-container);
      border: 1px solid rgba(255,255,255,0.1);
      border-radius: var(--md-shape-corner-large);
      padding: 28px;
      box-shadow: 0 8px 32px rgba(0,0,0,0.3);
    }

    .human-banner {
      background: linear-gradient(90deg, rgba(208, 188, 255, 0.12) 0%, rgba(239, 184, 200, 0.08) 100%);
      border-left: 4px solid var(--md-sys-color-primary);
      padding: 16px 20px;
      border-radius: 8px;
      margin-bottom: 24px;
      font-size: 18px;
      font-weight: 500;
      color: #FFFFFF;
      display: flex;
      align-items: center;
      gap: 12px;
    }

    .slot-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: 16px;
      margin-bottom: 20px;
    }

    .slot-box {
      background: var(--md-sys-color-surface-container-high);
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: var(--md-shape-corner-medium);
      padding: 14px;
      display: flex;
      flex-direction: column;
      gap: 6px;
      transition: all 0.2s ease;
    }
    .slot-box:focus-within {
      border-color: var(--md-sys-color-primary);
      box-shadow: 0 0 0 2px rgba(208, 188, 255, 0.2);
    }
    .slot-label {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.8px;
      color: var(--md-sys-color-primary);
      font-weight: 700;
    }
    .slot-input {
      background: transparent;
      border: none;
      color: #FFFFFF;
      font-family: 'JetBrains Mono', monospace;
      font-size: 20px;
      font-weight: 700;
      outline: none;
      width: 100%;
    }
    .slot-sub {
      font-size: 11px;
      color: var(--md-sys-color-on-surface-variant);
    }

    .chips-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .chip {
      background: rgba(255,255,255,0.06);
      border: 1px solid rgba(255,255,255,0.1);
      color: var(--md-sys-color-on-surface);
      padding: 6px 14px;
      border-radius: var(--md-shape-corner-full);
      font-size: 13px;
      font-weight: 500;
      cursor: pointer;
      transition: all 0.15s ease;
    }
    .chip:hover {
      background: rgba(208, 188, 255, 0.2);
      border-color: var(--md-sys-color-primary);
      color: #FFFFFF;
    }

    /* Grid Layout: 2 Columns */
    .dashboard-grid {
      display: grid;
      grid-template-columns: 1.2fr 1fr;
      gap: 28px;
    }
    @media (max-width: 900px) {
      .dashboard-grid { grid-template-columns: 1fr; }
    }

    .panel {
      background: var(--md-sys-color-surface-container);
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: var(--md-shape-corner-large);
      padding: 24px;
    }
    .panel-title {
      font-size: 17px;
      font-weight: 700;
      margin-bottom: 18px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    /* Rhythm Heatmap Grid */
    .heatmap-table {
      width: 100%;
      border-collapse: collapse;
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
    }
    .heatmap-table th, .heatmap-table td {
      padding: 4px;
      text-align: center;
    }
    .heatmap-table th {
      color: var(--md-sys-color-on-surface-variant);
      font-weight: 500;
    }
    .cell {
      width: 18px;
      height: 20px;
      border-radius: 4px;
      background: rgba(255,255,255,0.04);
      transition: transform 0.1s ease;
      cursor: pointer;
    }
    .cell:hover { transform: scale(1.3); z-index: 10; }
    .cell-0 { background: rgba(255,255,255,0.03); }
    .cell-1 { background: rgba(208, 188, 255, 0.25); }
    .cell-2 { background: rgba(208, 188, 255, 0.5); }
    .cell-3 { background: rgba(208, 188, 255, 0.75); }
    .cell-4 { background: #D0BCFF; color: #141218; font-weight: 700; }

    /* Timeline Runs List */
    .run-item {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 10px 12px;
      border-bottom: 1px solid rgba(255,255,255,0.05);
      font-size: 13px;
    }
    .run-item:last-child { border-bottom: none; }
    .run-time { font-family: 'JetBrains Mono', monospace; font-weight: 600; }
    .run-badge {
      background: rgba(166, 211, 159, 0.15);
      color: var(--md-sys-color-success);
      padding: 3px 10px;
      border-radius: 6px;
      font-size: 12px;
    }

    /* Transpiler Tabs */
    .tabs {
      display: flex;
      gap: 6px;
      margin-bottom: 16px;
      border-bottom: 1px solid rgba(255,255,255,0.08);
      padding-bottom: 8px;
      overflow-x: auto;
    }
    .tab {
      background: transparent;
      border: none;
      color: var(--md-sys-color-on-surface-variant);
      padding: 8px 14px;
      border-radius: 8px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
    }
    .tab.active {
      background: var(--md-sys-color-primary-container);
      color: var(--md-sys-color-on-primary-container);
    }
    .code-block {
      background: #0E0D12;
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 10px;
      padding: 16px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 13px;
      color: #A6D39F;
      white-space: pre-wrap;
      word-break: break-all;
    }

    /* Presets Gallery */
    .preset-list {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
      gap: 14px;
      max-height: 400px;
      overflow-y: auto;
    }
    .preset-card {
      background: var(--md-sys-color-surface-container-high);
      border: 1px solid rgba(255,255,255,0.06);
      border-radius: 10px;
      padding: 14px;
      cursor: pointer;
      transition: all 0.15s ease;
    }
    .preset-card:hover {
      border-color: var(--md-sys-color-primary);
      transform: translateY(-2px);
    }
    .preset-title { font-size: 14px; font-weight: 700; color: #FFFFFF; }
    .preset-expr { font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--md-sys-color-warning); margin: 4px 0; }
    .preset-desc { font-size: 12px; color: var(--md-sys-color-on-surface-variant); }
  </style>
</head>
<body>

<header>
  <div class="brand">
    <div class="brand-icon">⚡</div>
    <div>
      <span class="brand-title">Cron Rhythm Studio</span>
      <span class="brand-sub">v0.1.0 • Material 3</span>
    </div>
  </div>
  <div class="chips-row">
    <span class="chip" onclick="loadExpression('0 0 * * *')">Daily Midnight</span>
    <span class="chip" onclick="loadExpression('*/15 * * * *')">Every 15m</span>
    <span class="chip" onclick="loadExpression('0 9 * * 1-5')">Weekdays 9 AM</span>
    <span class="chip" onclick="loadExpression('0 0 1,15 * *')">1st & 15th</span>
  </div>
</header>

<div class="container">
  <!-- Interactive Builder Card -->
  <div class="expression-card">
    <div id="humanBanner" class="human-banner">
      ✨ At 12:00 AM, every day
    </div>

    <div class="slot-grid">
      <div class="slot-box">
        <span class="slot-label">Minute</span>
        <input id="slotMin" class="slot-input" value="0" oninput="onSlotChange()">
        <span class="slot-sub">0-59, *, */5</span>
      </div>
      <div class="slot-box">
        <span class="slot-label">Hour</span>
        <input id="slotHr" class="slot-input" value="0" oninput="onSlotChange()">
        <span class="slot-sub">0-23, *, */2</span>
      </div>
      <div class="slot-box">
        <span class="slot-label">Day of Month</span>
        <input id="slotDom" class="slot-input" value="*" oninput="onSlotChange()">
        <span class="slot-sub">1-31, *, L, 1,15</span>
      </div>
      <div class="slot-box">
        <span class="slot-label">Month</span>
        <input id="slotMon" class="slot-input" value="*" oninput="onSlotChange()">
        <span class="slot-sub">1-12, JAN-DEC</span>
      </div>
      <div class="slot-box">
        <span class="slot-label">Day of Week</span>
        <input id="slotDow" class="slot-input" value="*" oninput="onSlotChange()">
        <span class="slot-sub">0-7, MON-FRI</span>
      </div>
    </div>
  </div>

  <!-- Dashboard 2-Columns -->
  <div class="dashboard-grid">
    <!-- Rhythm Heatmap Panel -->
    <div class="panel">
      <div class="panel-title">
        <span>24x7 Weekly Rhythm Matrix</span>
        <span id="weeklyRunsBadge" class="run-badge">7 runs / week</span>
      </div>
      <div id="heatmapContainer" style="overflow-x: auto;">
        <!-- Dynamically rendered -->
      </div>
      <div id="collisionAlert" style="margin-top: 14px; font-size: 12px; color: var(--md-sys-color-warning);"></div>
    </div>

    <!-- Next Runs Timeline Panel -->
    <div class="panel">
      <div class="panel-title">
        <span>Upcoming Executions (Next 8 Runs)</span>
        <span style="font-size: 12px; color: var(--md-sys-color-outline);">UTC</span>
      </div>
      <div id="timelineContainer">
        <!-- Dynamically rendered -->
      </div>
    </div>
  </div>

  <!-- Universal Transpiler Panel -->
  <div class="panel">
    <div class="panel-title">Universal Engine Transpiler</div>
    <div class="tabs">
      <button class="tab active" onclick="switchTranspileTab('unix', this)">UNIX Crontab</button>
      <button class="tab" onclick="switchTranspileTab('quartz', this)">Quartz Scheduler</button>
      <button class="tab" onclick="switchTranspileTab('aws', this)">AWS EventBridge</button>
      <button class="tab" onclick="switchTranspileTab('systemd', this)">Systemd OnCalendar</button>
      <button class="tab" onclick="switchTranspileTab('github', this)">GitHub Actions</button>
      <button class="tab" onclick="switchTranspileTab('kubernetes', this)">Kubernetes CronJob</button>
    </div>
    <div id="transpileOutput" class="code-block">Loading transpilation...</div>
  </div>

  <!-- Preset Catalog Panel -->
  <div class="panel">
    <div class="panel-title">
      <span>Curated Production Presets</span>
      <input id="presetSearch" placeholder="Search presets..." oninput="filterPresets(this.value)" style="background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.1); color:#fff; padding:6px 12px; border-radius:8px; font-size:13px;">
    </div>
    <div id="presetsGrid" class="preset-list">
      <!-- Dynamically loaded -->
    </div>
  </div>
</div>

<script>
  let currentExpression = "0 0 * * *";
  let activeTranspileTab = "unix";
  let allPresets = [];

  function getExpressionFromSlots() {
    const min = document.getElementById("slotMin").value.trim() || "*";
    const hr = document.getElementById("slotHr").value.trim() || "*";
    const dom = document.getElementById("slotDom").value.trim() || "*";
    const mon = document.getElementById("slotMon").value.trim() || "*";
    const dow = document.getElementById("slotDow").value.trim() || "*";
    return `${min} ${hr} ${dom} ${mon} ${dow}`;
  }

  function loadExpression(expr) {
    const parts = expr.split(/\s+/);
    if (parts.length >= 5) {
      document.getElementById("slotMin").value = parts[0];
      document.getElementById("slotHr").value = parts[1];
      document.getElementById("slotDom").value = parts[2];
      document.getElementById("slotMon").value = parts[3];
      document.getElementById("slotDow").value = parts[4];
      onSlotChange();
    }
  }

  async function onSlotChange() {
    currentExpression = getExpressionFromSlots();
    refreshAll();
  }

  async function refreshAll() {
    try {
      // 1. Humanize
      const humRes = await fetch(`/api/humanize?expr=${encodeURIComponent(currentExpression)}`).then(r => r.json());
      document.getElementById("humanBanner").innerText = humRes.human_sentence ? `✨ ${humRes.human_sentence}` : `⚠ ${humRes.error || "Invalid expression"}`;

      // 2. Rhythm Matrix
      const rhyRes = await fetch(`/api/rhythm?expr=${encodeURIComponent(currentExpression)}`).then(r => r.json());
      if (rhyRes.matrix) {
        renderHeatmap(rhyRes.matrix);
        document.getElementById("weeklyRunsBadge").innerText = `${rhyRes.total_runs_per_week.toLocaleString()} runs / week`;
        const alertBox = document.getElementById("collisionAlert");
        if (rhyRes.collision_risks && rhyRes.collision_risks.length > 0) {
          alertBox.innerText = `⚠ ${rhyRes.collision_risks[0]}`;
        } else {
          alertBox.innerText = "";
        }
      }

      // 3. Timeline
      const timeRes = await fetch(`/api/next?expr=${encodeURIComponent(currentExpression)}&count=8`).then(r => r.json());
      renderTimeline(timeRes.upcoming_runs || []);

      // 4. Transpile
      refreshTranspile();
    } catch (e) {
      console.error(e);
    }
  }

  function renderHeatmap(matrix) {
    const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    let html = '<table class="heatmap-table"><thead><tr><th></th>';
    for (let h = 0; h < 24; h += 2) {
      html += `<th colspan="2">${h.toString().padStart(2, '0')}</th>`;
    }
    html += '</tr></thead><tbody>';

    matrix.forEach((row, dIdx) => {
      html += `<tr><td style="color:#CAC4D0; font-weight:700;">${days[dIdx]}</td>`;
      row.forEach(cell => {
        let cls = "cell-0";
        if (cell.execution_count > 0) {
          if (cell.intensity <= 0.25) cls = "cell-1";
          else if (cell.intensity <= 0.5) cls = "cell-2";
          else if (cell.intensity <= 0.75) cls = "cell-3";
          else cls = "cell-4";
        }
        html += `<td><div class="cell ${cls}" title="${days[dIdx]} ${cell.hour}:00 - ${cell.execution_count} runs"></div></td>`;
      });
      html += '</tr>';
    });
    html += '</tbody></table>';
    document.getElementById("heatmapContainer").innerHTML = html;
  }

  function renderTimeline(runs) {
    const cont = document.getElementById("timelineContainer");
    if (!runs || runs.length === 0) {
      cont.innerHTML = '<div style="padding:20px; text-align:center; color:#79747E;">No upcoming executions found</div>';
      return;
    }
    let html = '';
    runs.forEach(r => {
      html += `
        <div class="run-item">
          <div>
            <span class="run-time">${r.datetime_iso}</span>
            <span style="color:#CAC4D0; margin-left:8px;">(${r.day_name})</span>
          </div>
          <span class="run-badge">${r.relative_delta}</span>
        </div>
      `;
    });
    cont.innerHTML = html;
  }

  async function switchTranspileTab(tabName, el) {
    activeTranspileTab = tabName;
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    if (el) el.classList.add("active");
    refreshTranspile();
  }

  async function refreshTranspile() {
    try {
      const res = await fetch(`/api/transpile?expr=${encodeURIComponent(currentExpression)}&target=${activeTranspileTab}`).then(r => r.json());
      let text = res.output_syntax || res.error || "No output";
      if (res.warnings && res.warnings.length > 0) {
        text += "\\n\\n# Caveats:\\n# " + res.warnings.join("\\n# ");
      }
      document.getElementById("transpileOutput").innerText = text;
    } catch (e) {
      document.getElementById("transpileOutput").innerText = "Error transpiling";
    }
  }

  async function fetchPresets() {
    try {
      const res = await fetch('/api/presets').then(r => r.json());
      allPresets = res.presets || [];
      renderPresets(allPresets);
    } catch (e) {
      console.error(e);
    }
  }

  function renderPresets(presets) {
    const grid = document.getElementById("presetsGrid");
    grid.innerHTML = presets.map(p => `
      <div class="preset-card" onclick="loadExpression('${p.expression}')">
        <div class="preset-title">${p.title}</div>
        <div class="preset-expr">${p.expression}</div>
        <div class="preset-desc">${p.description}</div>
      </div>
    `).join('');
  }

  function filterPresets(q) {
    const qLower = q.toLowerCase();
    const filtered = allPresets.filter(p => 
      p.title.toLowerCase().includes(qLower) ||
      p.expression.toLowerCase().includes(qLower) ||
      p.category.toLowerCase().includes(qLower) ||
      p.description.toLowerCase().includes(qLower)
    );
    renderPresets(filtered);
  }

  // Initial Load
  window.addEventListener("DOMContentLoaded", () => {
    refreshAll();
    fetchPresets();
  });
</script>
</body>
</html>
"""


class StudioAPIHandler(http.server.BaseHTTPRequestHandler):
    """Custom HTTP request handler serving REST API and Material 3 Web UI."""

    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query = urllib.parse.parse_qs(parsed_url.query)

        # 1. Static Files from public directory (HTML, CSS, JS, Images)
        public_root = pathlib.Path(__file__).resolve().parent.parent.parent / "public"
        safe_rel = path.lstrip("/")
        if not safe_rel or safe_rel in ("index.html", "studio"):
            safe_rel = "index.html"
        candidate_file = (public_root / safe_rel).resolve()
        try:
            if candidate_file.is_file() and public_root in candidate_file.parents:
                content_type = "text/html; charset=utf-8"
                if safe_rel.endswith(".css"):
                    content_type = "text/css; charset=utf-8"
                elif safe_rel.endswith(".js"):
                    content_type = "application/javascript; charset=utf-8"
                elif safe_rel.endswith(".png"):
                    content_type = "image/png"
                elif safe_rel.endswith(".svg"):
                    content_type = "image/svg+xml"
                elif safe_rel.endswith(".json"):
                    content_type = "application/json"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(candidate_file.read_bytes())
                return
        except Exception:
            pass

        if path in ("/", "/index.html", "/studio"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(MATERIAL_WEB_HTML.encode("utf-8"))
            return

        # 2. REST API: /api/parse
        if path == "/api/parse":
            expr = query.get("expr", ["* * * * *"])[0]
            ast = parse_cron(expr)
            resp_data = ast.to_dict()
            if ast.is_valid:
                resp_data["human_summary"] = humanize_cron(ast)
                resp_data["field_explanations"] = explain_cron_parts(ast)
            self._send_json(resp_data)
            return

        # 3. REST API: /api/humanize
        if path == "/api/humanize":
            expr = query.get("expr", ["* * * * *"])[0]
            ast = parse_cron(expr)
            if not ast.is_valid:
                self._send_json({"is_valid": False, "error": ast.error_message})
                return
            self._send_json({
                "is_valid": True,
                "expression": expr,
                "human_sentence": humanize_cron(ast),
                "field_breakdown": explain_cron_parts(ast),
            })
            return

        # 4. REST API: /api/next
        if path == "/api/next":
            expr = query.get("expr", ["* * * * *"])[0]
            count = int(query.get("count", ["10"])[0])
            tz_name = query.get("tz", ["UTC"])[0]
            ast = parse_cron(expr)
            if not ast.is_valid:
                self._send_json({"error": ast.error_message})
                return
            runs = next_runs(ast, count=count, tz_name=tz_name)
            self._send_json({
                "expression": expr,
                "timezone": tz_name,
                "upcoming_runs": [r.to_dict() for r in runs],
            })
            return

        # 5. REST API: /api/transpile
        if path == "/api/transpile":
            expr = query.get("expr", ["* * * * *"])[0]
            target_str = query.get("target", ["unix"])[0]
            ast = parse_cron(expr)
            if not ast.is_valid:
                self._send_json({"error": ast.error_message})
                return
            target_fmt = _resolve_target_format_cli(target_str)
            res = transpile_cron(ast, target_fmt)
            self._send_json(res.to_dict())
            return

        # 6. REST API: /api/rhythm
        if path == "/api/rhythm":
            expr = query.get("expr", ["* * * * *"])[0]
            ast = parse_cron(expr)
            report = generate_rhythm_matrix(ast)
            self._send_json(report.to_dict())
            return

        # 7. REST API: /api/presets
        if path == "/api/presets":
            cat = query.get("category", [None])[0]
            search_q = query.get("search", [None])[0]
            if search_q:
                res = search_presets(search_q)
                if cat:
                    res = [p for p in res if p.category.lower() == cat.lower()]
            else:
                res = list_presets(cat)
            self._send_json({
                "categories": get_categories(),
                "presets": [p.to_dict() for p in res],
            })
            return

        # 7b. REST API: /api/fleet
        if path == "/api/fleet":
            from .fleet_optimizer import audit_cron_fleet
            jobs_param = query.get("jobs", [None])[0]
            horizon = int(query.get("horizon", ["24"])[0])
            jobs: Dict[str, str] = {}
            if jobs_param:
                try:
                    parsed = json.loads(jobs_param)
                    if isinstance(parsed, dict):
                        jobs = {str(k): str(v) for k, v in parsed.items()}
                except Exception:
                    pass
            if not jobs:
                jobs = {
                    "db_backup": "0 0 * * *",
                    "nightly_analytics": "0 0 * * *",
                    "billing_reconciliation": "0 0 * * *",
                    "cache_eviction": "0 0 * * *",
                }
            report = audit_cron_fleet(jobs, horizon_hours=horizon, auto_rebalance=True)
            self._send_json(report.to_dict())
            return

        # 7c. REST API: /api/dst-audit
        if path == "/api/dst-audit":
            expr = query.get("expr", query.get("expression", ["0 2 * * *"]))[0]
            tz = query.get("tz", query.get("timezone", ["America/New_York"]))[0]
            year_val = query.get("year", [None])[0]
            year = int(year_val) if year_val and year_val.isdigit() else None
            rep = audit_dst_anomalies(expr, tz_name=tz, reference_year=year)
            self._send_json(rep.to_dict())
            return

        # 7d. REST API: /api/tz-board
        if path == "/api/tz-board":
            expr = query.get("expr", query.get("expression", ["0 14 * * 1-5"]))[0]
            home_tz = query.get("home_tz", ["America/New_York"])[0]
            count = int(query.get("count", ["3"])[0])
            reps = project_world_flight_board(expr, home_tz=home_tz, run_count=count)
            self._send_json({
                "expression": expr,
                "home_timezone": home_tz,
                "runs": [r.to_dict() for r in reps],
            })
            return

        # 8. REST API: /api/health or /api/diagnostics
        if path in ("/api/health", "/api/diagnostics"):
            plat_info = get_platform_info()
            self._send_json({
                "status": "healthy",
                "version": __version__,
                "platform": plat_info.as_dict(),
                "time_utc": datetime.now(timezone.utc).isoformat(),
            })
            return

        # 404 Not Found
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error": "Endpoint not found"}')

    def do_POST(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if path == "/api/fleet":
            from .fleet_optimizer import audit_cron_fleet
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            try:
                data = json.loads(body.decode("utf-8"))
            except Exception:
                data = {}
            jobs = data.get("jobs", {})
            horizon = int(data.get("horizon_hours", 24))
            auto_rebal = bool(data.get("auto_rebalance", True))
            max_shift = int(data.get("max_shift_minutes", 25))
            if not jobs or not isinstance(jobs, dict):
                jobs = {
                    "db_backup": "0 0 * * *",
                    "nightly_analytics": "0 0 * * *",
                    "billing_reconciliation": "0 0 * * *",
                }
            report = audit_cron_fleet(
                jobs,
                horizon_hours=horizon,
                auto_rebalance=auto_rebal,
                max_shift_minutes=max_shift,
            )
            self._send_json(report.to_dict())
            return

        if path == "/api/dst-audit":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            try:
                data = json.loads(body.decode("utf-8"))
            except Exception:
                data = {}
            expr = str(data.get("expression", data.get("expr", "0 2 * * *")))
            tz = str(data.get("timezone", data.get("tz", "America/New_York")))
            year = data.get("year")
            rep = audit_dst_anomalies(expr, tz_name=tz, reference_year=int(year) if year else None)
            self._send_json(rep.to_dict())
            return

        if path == "/api/tz-board":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            try:
                data = json.loads(body.decode("utf-8"))
            except Exception:
                data = {}
            expr = str(data.get("expression", data.get("expr", "0 14 * * 1-5")))
            home_tz = str(data.get("home_timezone", data.get("home_tz", "America/New_York")))
            count = int(data.get("count", 3))
            reps = project_world_flight_board(expr, home_tz=home_tz, run_count=count)
            self._send_json({
                "expression": expr,
                "home_timezone": home_tz,
                "runs": [r.to_dict() for r in reps],
            })
            return

        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error": "Endpoint not found"}')

    def _send_json(self, data: Any, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        payload = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress noisy standard HTTP access logs in quiet mode
        pass


def cmd_serve(args: argparse.Namespace) -> int:
    """Launch the Cron Rhythm Studio Web UI & REST Server (design influenced by Material 3 tokens)."""
    host = args.host or "127.0.0.1"
    port = args.port or 8080

    print(styler.bold(styler.cyan("\n=== Launching Cron Rhythm Studio Web UI & REST Server ===")))
    print(f"  {styler.bold('Local URL:')}   {styler.bold(styler.green(f'http://{host}:{port}/'))}")
    print(f"  {styler.bold('REST API:')}    {styler.dim(f'http://{host}:{port}/api/parse, /api/next, /api/rhythm')}")
    print(f"  {styler.bold('Status:')}      Running (Press Ctrl+C to stop)\n")

    class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True

    try:
        with ThreadedHTTPServer((host, port), StudioAPIHandler) as httpd:
            httpd.serve_forever()
    except KeyboardInterrupt:
        print(styler.info("\nCron Rhythm Studio server stopped gracefully.\n"))
        return 0
    except Exception as e:
        print(styler.failure(f"Failed to start HTTP server on {host}:{port} -> {e}"))
        return 1


# ============================================================================
# Argument Parser Construction & Global Flags
# ============================================================================

def build_parser() -> argparse.ArgumentParser:
    """Build unified multi-OS CLI argument parser."""
    # Parent parser for global flags that can appear before or after subcommands
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument("--no-color", action="store_true", help="Disable ANSI terminal color output")
    parent_parser.add_argument("-q", "--quiet", action="store_true", help="Quiet mode (minimal output)")
    parent_parser.add_argument("-v", "--version", action="version", version=f"cron-rhythm-studio {__version__}")

    parser = argparse.ArgumentParser(
        prog="cron-rhythm-studio",
        description="High-precision Cron AST Parser, Timeline Calculator, Rhythm Heatmap Matrix, Universal Transpiler, and MCP Server.",
        parents=[parent_parser],
    )

    subparsers = parser.add_subparsers(dest="subcommand", title="Available Commands", help="Subcommand to execute")

    # 1. parse
    p_parse = subparsers.add_parser("parse", parents=[parent_parser], help="Parse cron string into AST and token breakdown")
    p_parse.add_argument("expression", type=str, help="Cron expression to parse")
    p_parse.add_argument("--json", action="store_true", help="Output structured JSON AST")
    p_parse.set_defaults(func=cmd_parse)

    # 2. next
    p_next = subparsers.add_parser("next", parents=[parent_parser], help="Calculate upcoming execution datetimes")
    p_next.add_argument("expression", type=str, help="Cron expression")
    p_next.add_argument("-n", "--count", type=int, default=10, help="Number of upcoming execution dates (default: 10)")
    p_next.add_argument("--start", type=str, default=None, help="Starting reference datetime in ISO-8601 format")
    p_next.add_argument("-tz", "--timezone", type=str, default="UTC", help="Timezone name (default: UTC)")
    p_next.add_argument("--json", action="store_true", help="Output JSON list of dates")
    p_next.set_defaults(func=cmd_next)

    # 3. humanize
    p_hum = subparsers.add_parser("humanize", parents=[parent_parser], help="Translate cron expression into human English")
    p_hum.add_argument("expression", type=str, help="Cron expression to translate")
    p_hum.add_argument("--verbose", action="store_true", help="Include detailed per-field breakdown")
    p_hum.add_argument("--json", action="store_true", help="Output JSON result")
    p_hum.set_defaults(func=cmd_humanize)

    # 4. transpile
    p_trans = subparsers.add_parser("transpile", parents=[parent_parser], help="Transpile cron expression to target dialect")
    p_trans.add_argument("expression", type=str, help="Source cron expression")
    p_trans.add_argument("-t", "--target", type=str, default="all", help="Target dialect (unix, quartz, aws, systemd, github, kubernetes, all)")
    p_trans.add_argument("-f", "--from", dest="from_syntax", type=str, default="unix", help="Source dialect hint")
    p_trans.add_argument("--json", action="store_true", help="Output JSON transpiled object")
    p_trans.set_defaults(func=cmd_transpile)

    # 5. rhythm
    p_rhy = subparsers.add_parser("rhythm", parents=[parent_parser], help="Generate 24x7 weekly rhythm matrix heatmap")
    p_rhy.add_argument("expression", type=str, help="Cron expression")
    p_rhy.add_argument("--collision-threshold", type=int, default=2, help="Overlap threshold for collision warnings")
    p_rhy.add_argument("--granularity", type=str, default="hourly", help="Granularity ('hourly' or 'slot')")
    p_rhy.add_argument("--json", action="store_true", help="Output JSON matrix report")
    p_rhy.set_defaults(func=cmd_rhythm)

    # 5b. fleet
    p_fleet = subparsers.add_parser("fleet", parents=[parent_parser], help="Audit multi-job fleet concurrency and desynchronize thundering herd")
    p_fleet.add_argument("--jobs", type=str, default=None, help="JSON string or name=expr pairs of fleet jobs")
    p_fleet.add_argument("-f", "--file", type=str, default=None, help="Path to JSON file with fleet jobs mapping")
    p_fleet.add_argument("--demo", action="store_true", help="Use built-in microservice fleet sample")
    p_fleet.add_argument("-H", "--horizon", type=int, default=24, help="Analysis horizon in hours (default: 24)")
    p_fleet.add_argument("--crontab", action="store_true", help="Print optimized desynchronized Unix crontab")
    p_fleet.add_argument("--k8s", action="store_true", help="Print Kubernetes CronJob YAML manifests")
    p_fleet.add_argument("--svg", type=str, default=None, help="Save SVG concurrency chart to file")
    p_fleet.add_argument("--json", action="store_true", help="Output JSON audit report")
    p_fleet.set_defaults(func=cmd_fleet)

    # 5c. dst-audit
    p_dst = subparsers.add_parser("dst-audit", parents=[parent_parser], help="Audit Daylight Saving Time clock shift risks (skipped & duplicate runs)")
    p_dst.add_argument("expression", type=str, help="Cron expression to audit")
    p_dst.add_argument("-tz", "--timezone", type=str, default="America/New_York", help="Target timezone (default: America/New_York)")
    p_dst.add_argument("-y", "--year", type=int, default=None, help="Reference calendar year (default: current year)")
    p_dst.add_argument("--json", action="store_true", help="Output JSON audit report")
    p_dst.set_defaults(func=cmd_dst_audit)

    # 5d. tz-board
    p_board = subparsers.add_parser("tz-board", parents=[parent_parser], help="Synchronize next runs across global tech hubs (flight departure board)")
    p_board.add_argument("expression", type=str, help="Cron expression")
    p_board.add_argument("--home-tz", type=str, default="America/New_York", help="Home timezone (default: America/New_York)")
    p_board.add_argument("-n", "--count", type=int, default=3, help="Number of upcoming executions to project (default: 3)")
    p_board.add_argument("--json", action="store_true", help="Output JSON flight board")
    p_board.set_defaults(func=cmd_tz_board)

    # 6. presets
    p_pre = subparsers.add_parser("presets", parents=[parent_parser], help="List curated production cron templates")
    p_pre.add_argument("-c", "--category", type=str, default=None, help="Filter by category")
    p_pre.add_argument("-s", "--search", type=str, default=None, help="Search keyword query")
    p_pre.add_argument("--json", action="store_true", help="Output JSON catalog")
    p_pre.set_defaults(func=cmd_presets)

    # 7. serve
    p_serve = subparsers.add_parser("serve", parents=[parent_parser], help="Launch the Material 3 Web UI & REST Server")
    p_serve.add_argument("--host", type=str, default="127.0.0.1", help="Host IP to bind (default: 127.0.0.1)")
    p_serve.add_argument("-p", "--port", type=int, default=8080, help="Port to listen on (default: 8080)")
    p_serve.set_defaults(func=cmd_serve)

    # 8. mcp
    p_mcp = subparsers.add_parser("mcp", parents=[parent_parser], help="Run MCP JSON-RPC 2.0 server over stdio")
    p_mcp.set_defaults(func=cmd_mcp)

    # 9. diagnostics / doctor / platform
    for diag_cmd in ("diagnostics", "doctor", "platform"):
        p_diag = subparsers.add_parser(diag_cmd, parents=[parent_parser], help="Multi-OS platform diagnostics doctor")
        p_diag.add_argument("--json", action="store_true", help="Output JSON diagnostics report")
        p_diag.set_defaults(func=cmd_diagnostics)

    # 10. test
    p_test = subparsers.add_parser("test", parents=[parent_parser], help="Run internal self-verification test suite")
    p_test.set_defaults(func=cmd_test)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Main CLI entrypoint."""
    global styler
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "no_color", False):
        styler = TerminalStyler(enabled=False)

    if not hasattr(args, "func"):
        parser.print_help()
        return 0

    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(styler.failure(f"Fatal error: {exc}"))
        return 1


if __name__ == "__main__":
    sys.exit(main())
