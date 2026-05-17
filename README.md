# Homa Energy — PM Analytics Workspace

Self-service product analytics for the Homa Energy platform: a local
Metabase instance for dashboards plus a Claude Code MCP server for
ad-hoc investigation. Everything runs on your laptop, against a
read-only snapshot of the product-analytics database.

---

## Prerequisites

- **Docker Desktop** — required for the local Metabase
- **Python 3.10 or newer** — `python3 --version`
- **Claude Code** — install from [claude.ai/code](https://claude.ai/code)

---

## Setup (one time)

### macOS / Linux

```bash
chmod +x setup.sh
./setup.sh
```

### Windows

```bat
setup.bat
```

The setup script:

1. Extracts the bundled analytics database if it's still zipped
2. Creates a `.venv/` virtual environment and installs the MCP server
3. Starts the local Metabase via `docker compose up -d`

On first boot, Metabase takes ~60-90 seconds to finish initialising —
the script waits for it. When it returns, four dashboards are already
provisioned.

---

### Verify the layout

```
homa_pm_analyst/
├── homa_pm_events.sqlite3        ← must be here (extract from zip first)
├── homa_pm_events.sqlite3.zip
├── CLAUDE.md
├── .mcp.json
├── setup.sh / setup.bat
├── mcp/
│   ├── server.py
│   ├── metabase_client.py
│   └── requirements.txt
└── metabase/
    ├── docker-compose.yml
    └── mb-data/                  # populated dashboards live here
```

The database is distributed as `homa_pm_events.sqlite3.zip` because the
uncompressed file exceeds 100 MB. If `setup.sh` didn't extract it
automatically:

```bash
# macOS / Linux
unzip homa_pm_events.sqlite3.zip

# Windows (PowerShell)
Expand-Archive homa_pm_events.sqlite3.zip .
```

---

### Open the project

```bash
cd homa_pm_analyst
claude
```

Claude Code auto-detects `.mcp.json` and connects to the MCP server on
startup. You should see `homa-pm-analytics` listed under active MCP
servers.

For dashboards: open [http://localhost:3000](http://localhost:3000) and
sign in with your PM account:

- email: `pm@homa.local`
- password: `HomaPm1!`

This account can view and query everything but **cannot modify** the
four canonical dashboards in the `Official` collection — they are the
team's source of truth and protected against accidental edits. Charts
you create yourself (whether via Metabase UI or via Claude+MCP) land in
your own Personal Collection or in `Our analytics`, where you have full
control.

> **Admin access** is also available (`admin@homa.local` / `HomaAdmin1!`)
> for changes to the canonical dashboards themselves, but day-to-day
> work should stay on the PM account.

---

## How it works

```
You (natural language question)
        ↓
  Claude Code (CLAUDE.md + pm_analytics skill set context)
        ↓
  MCP server (server.py)
        ↓
  homa_pm_events.sqlite3 (read-only)            ──►   answer in plain language
                                                       (optionally) new chart
                                                       created in Metabase
```

For day-to-day dashboards, work in Metabase. For ad-hoc questions and
investigations, work in Claude — it has read access to the same data
and can also create new Metabase cards on your behalf.

---

## MCP tools

| Tool | Description |
|------|-------------|
| `list_tables` | All tables with business descriptions |
| `describe_table` | Columns + descriptions for a specific table |
| `get_schema` | Full schema + event taxonomy in one call |
| `list_events` | Enumerated event_name values stored in `app_events` |
| `list_experiments` | Active feature flags / A-B tests |
| `execute_query` | Run a SELECT query (max 500 rows returned) |
| `metabase_list_dashboards` | Enumerate Metabase dashboards |
| `metabase_create_chart` | Create a new Metabase chart from SQL |

Only `SELECT` and `WITH …` queries are permitted. Any attempt to modify
data is blocked.

---

## Example session

```
> Why did our weekly active users plateau in mid-March?

> Where is onboarding broken? Slice the funnel by platform and OS version.

> Which push variant — eco-friendly or fear-based — performed better?
  Sanity-check the sample sizes.

> Find users at risk of churn this month and suggest a re-engagement segment.

> Create a chart of daily signups from the Netherlands and add it to
  the Product Health Overview dashboard.
```

---

## Dashboards

Four pre-built dashboards in Metabase, refreshed on every page load:

| Dashboard | Covers |
|-----------|--------|
| 01 — Product Health Overview | DAU, WAU by platform, weekly signups, W1 retention by cohort |
| 02 — Onboarding Funnel | Signup → pair → first-dashboard funnel with platform breakdown |
| 03 — Premium & Paywall | Premium funnel, starts vs cancellations, paywall sources, churn reasons |
| 04 — Push Campaigns | Per-campaign open / click rates, A-B variant comparison |

---

## Database tables

| Table | Rows (approx.) | Description |
|-------|---------------|-------------|
| `users` | 500 | User dimension (country, locale, Premium status, registration) |
| `app_events` | ~493 000 | Central behavioural event log |
| `experiments` | 3 | Feature flags / A-B tests |
| `experiment_assignments` | 1 500 | Per-user variant assignment |
| `subscription_events` | ~110 | Premium plan transitions |
| `notification_campaigns` | 4 | Push campaigns including A-B copy variants |
| `notification_deliveries` | 792 | Per-user delivery + interaction |

Data covers **January 1 – April 30, 2026**.

---

## Troubleshooting

**`Database not found` error**
→ Make sure `homa_pm_events.sqlite3` is in the workspace root (same
level as `CLAUDE.md`). Extract from `homa_pm_events.sqlite3.zip` if
needed.

**`ModuleNotFoundError: mcp`**
→ Run `pip install -r mcp/requirements.txt` and restart Claude Code.

**Metabase not loading at localhost:3000**
→ Check `docker compose ps` — the container should show
`(healthy)`. First boot takes 60-90 s; subsequent boots are fast.

**Metabase says "Cannot reach Metabase" from MCP**
→ Make sure `docker compose up -d` is running. If you stopped Metabase
to free resources, MCP chart-creation tools will return an error until
you start it again.

**MCP server not connecting on Windows**
→ Change `"command": "python3"` to `"command": "python"` in `.mcp.json`.

**Claude says it cannot run queries**
→ Confirm the MCP server is listed as active (green) in Claude Code. If
not, restart Claude Code from the workspace directory.
