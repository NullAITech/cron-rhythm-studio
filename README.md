# Cron Rhythm Studio ⏰✨

> **Multi-OS Model Context Protocol (MCP) Server, CLI & Interactive Studio (design influenced by Material 3 tokens) for Cron AST Parsing, 24×7 Rhythm Heatmaps, Humanization, and Multi-Dialect Transpilation.**
> **Zero External Dependencies** — 100% Python Standard Library (3.9–3.13).

[![CI](https://github.com/NullAITech/cron-rhythm-studio/actions/workflows/ci.yml/badge.svg)](https://github.com/NullAITech/cron-rhythm-studio/actions/workflows/ci.yml)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![FastMCP](https://img.shields.io/badge/MCP-Protocol%202.0-green.svg)](https://modelcontextprotocol.io/)

---

## Overview

**Cron Rhythm Studio** is an all-in-one developer toolkit for understanding, visualizing, and transpiling periodic job schedules across production platforms. It parses standard 5-field UNIX crontabs, 6-field Quartz schedules with seconds, and 7-field enterprise expressions into a rich AST, computes exact upcoming timeline runs, translates syntax into natural human English, maps weekly workload distributions onto a 24×7 rhythm heatmap, and converts expressions between UNIX, Quartz, AWS EventBridge, Systemd Timers, GitHub Actions, and Kubernetes CronJobs.

---

## Key Features

- **High-Precision Cron AST Parser**:
  - Supports 5-field UNIX (`* * * * *`), 6-field with seconds (`0 * * * * *`), and 7-field with year (`0 0 12 1 1 ? 2026`).
  - Handles special symbols: ranges (`1-5`), steps (`*/15`), lists (`1,3,5`), day names (`MON-FRI`), month names (`JAN-DEC`), `L` (last day of month), `W` (nearest weekday), `?` (no specific value), and `#` (nth weekday).
  - Handles standard macros: `@yearly`, `@annually`, `@monthly`, `@weekly`, `@daily`, `@midnight`, `@hourly`, `@reboot`.
- **Iterative Timeline Engine**:
  - Exact calendar calculation honoring month lengths (28/29/30/31 days), leap years, and weekday shifts.
  - Computes the next $N$ runs with human-readable relative countdowns ("in 14 minutes", "tomorrow at 04:00 AM").
- **Natural Language Humanizer**:
  - Synthesizes crisp English sentences describing schedule execution without ambiguous phrases.
- **Universal Multi-Dialect Transpiler**:
  - Transpiles seamlessly across **UNIX Crontab**, **Quartz Scheduler**, **AWS EventBridge**, **Systemd Timers** (`OnCalendar`), **GitHub Actions Workflows**, and **Kubernetes CronJobs**.
- **24×7 Rhythm Heatmap Matrix**:
  - Maps 168 weekly hour slots ($7 \text{ days} \times 24 \text{ hours}$) to evaluate load distribution, peak hours, and collision risks.
- **DST Transition Anomaly Auditor**:
  - Audits cron schedules for critical clock discontinuities during Daylight Saving Time shifts (identifies skipped jobs during Spring Forward and duplicate executions during Fall Back with safe alternative recommendations).
- **Multi-City World Run Radar (Flight Departure Board)**:
  - Synchronizes upcoming cron execution times across major global tech hubs (UTC, New York, San Francisco, London, Berlin/Paris, Tokyo, Sydney) with real-time business hour indicators.
- **Production Schedule Catalog**:
  - 40+ curated presets for database backups, SSL renewal, log rotation, cache warming, and telemetry probes.
- **FastMCP Protocol 2.0 Server**:
  - First-class stdio JSON-RPC server enabling AI coding assistants (Claude Desktop, Cursor, Cline, Windsurf) to analyze and generate schedules.
- **Cron Rhythm Studio Web App** (Design influenced by Material 3 tokens):
  - Interactive web interface with live cron builder wheels, human translation pill, 24×7 rhythm heatmap, upcoming run timeline, and dark/light themes.

---

## Installation

```bash
pip install cron-rhythm-studio
```

Or install from source:

```bash
git clone https://github.com/NullAITech/cron-rhythm-studio.git
cd cron-rhythm-studio
pip install -e .
```

---

## Quickstart & CLI Reference

```bash
# 1. Parse a cron expression into AST
cron-rhythm parse "*/15 9-17 * * 1-5"

# 2. Translate into natural human English
cron-rhythm humanize "0 4 1,15 * *"
# Output: At 04:00 AM, on day 1 and 15 of the month

# 3. Calculate upcoming 10 execution timestamps
cron-rhythm next "0 0 * * *" --count 10

# 4. Transpile cron expression to AWS EventBridge or Systemd Timer
cron-rhythm transpile "0 4 * * 1" --target aws
cron-rhythm transpile "0 4 * * 1" --target systemd

# 5. Generate 24x7 Rhythm Heatmap
cron-rhythm rhythm "*/30 * * * *"

# 6. Audit Daylight Saving Time (DST) clock discontinuities (skipped / duplicate runs)
cron-rhythm dst-audit "0 2 * * *" --timezone America/New_York

# 7. Project next runs across global tech hubs (flight departure board)
cron-rhythm tz-board "0 14 * * 1-5" --count 3

# 8. Browse built-in production schedule templates
cron-rhythm presets --search backup

# 9. Launch the Cron Rhythm Studio Web App (Material 3 influenced)
cron-rhythm serve --port 8080
```

---

## FastMCP Configuration

Integrate Cron Rhythm Studio directly into your AI workflow:

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "cron-rhythm": {
      "command": "cron-rhythm",
      "args": ["mcp"]
    }
  }
}
```

### Cursor / Cline / Antigravity (`mcp.json`)

```json
{
  "mcpServers": {
    "cron-rhythm": {
      "command": "python3",
      "args": ["-m", "cron_rhythm_studio", "mcp"]
    }
  }
}
```

### Registered MCP Tools:
- `cron_parse_expression`: Parse expression into structured AST.
- `cron_next_runs`: Calculate next $N$ executions with relative countdowns.
- `cron_humanize`: Generate human-readable English description.
- `cron_transpile`: Transpile between UNIX, Quartz, AWS, Systemd, GitHub, and K8s.
- `cron_rhythm_matrix`: Generate 24×7 weekly distribution and collision analysis.
- `cron_list_presets`: Query curated production presets.
- `cron_diagnostics`: Multi-OS runtime diagnostics.

---

## Python API Usage

```python
from cron_rhythm_studio import parse_cron, humanize_cron, next_runs, transpile_cron

# Parse expression
ast = parse_cron("*/20 8-18 * * 1-5")
print("Valid:", ast.is_valid)

# Humanize
print(humanize_cron("*/20 8-18 * * 1-5"))
# "Every 20 minutes, between 08:00 AM and 06:59 PM, Monday through Friday"

# Next executions
for item in next_runs("*/20 8-18 * * 1-5", count=5):
    print(f"{item.datetime_iso} ({item.relative_delta})")

# Transpile to Systemd Timer
res = transpile_cron("0 4 * * 1", target="systemd")
print(res.output_syntax)
# "*-*-* 04:00:00 Mon"
```

---

## Cross-Platform Compatibility

Tested across:
- **Linux**: Ubuntu 20.04+, Debian, Fedora, Arch Linux, Parrot OS
- **macOS**: Sonoma, Ventura, Monterey (Apple Silicon & Intel)
- **Windows**: Windows 10/11 (PowerShell, CMD, WSL)
- **Mobile / Android**: Termux
- **Python**: 3.9, 3.10, 3.11, 3.12, 3.13

---

## License

MIT License. Crafted with zero external dependencies using 100% Python Standard Library.
