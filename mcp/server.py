"""
Homa PM Analytics — MCP server.

Read-only SQLite access plus embedded schema, event-catalog and metric
context for the Homa Energy product-analytics database.

The database covers user-level behavioural data only: app events,
experiments, subscriptions, push campaigns and a denormalised users
dimension. The operational tables (devices, energy readings, support
tickets) live in a separate analyst workspace and are not exposed here.

Tools exposed:
    list_tables             — table directory with business descriptions
    describe_table          — column-level docs for a specific table
    get_schema              — full schema + event taxonomy in one call
    list_events             — event_name taxonomy (app_events.event_name)
    list_experiments        — active feature flags / A-B tests
    execute_query           — read-only SELECT (max 500 rows)
    metabase_list_dashboards— enumerate Metabase dashboards
    metabase_create_chart   — create a Metabase card from SQL + chart type
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from mcp.server.fastmcp import FastMCP


DB_PATH = Path(__file__).resolve().parent.parent / "homa_pm_events.sqlite3"

mcp = FastMCP("Homa PM Analytics")


# ── Table descriptions ──────────────────────────────────────────────────────

TABLE_DESCRIPTIONS = {
    "users": (
        "User dimension: one row per registered user with country, locale, "
        "current Premium status, registration timestamp and last activity. "
        "Always filter business queries with `WHERE is_staff=0 AND is_superuser=0`."
    ),
    "app_events": (
        "Central product-event log. One row per discrete user action — signup "
        "steps, screen views, paywall views, push interactions, premium funnel, "
        "feature usage. Use this for funnels, retention, DAU/WAU and feature "
        "adoption. Call `list_events()` to see the full event_name taxonomy."
    ),
    "experiments": (
        "Registry of A-B tests and feature-flag rollouts. Each row has a key "
        "(e.g. 'monthly_savings_card'), a started_at date and a rollout_pct."
    ),
    "experiment_assignments": (
        "Per-user assignment to an experiment variant (control | treatment). "
        "Join on user_id to slice any metric by variant."
    ),
    "subscription_events": (
        "Premium plan transitions: started_premium, cancelled_premium, "
        "reactivated. Compared to users.is_premium (current state) this gives "
        "the full history and supports cohort/churn analysis."
    ),
    "notification_campaigns": (
        "Push and email campaigns. Includes the actual title/body text, target "
        "segment, send timestamp and total audience size. Use `variant` to "
        "compare A-B copy."
    ),
    "notification_deliveries": (
        "Per-user delivery + interaction for each campaign. opened_at NULL = "
        "not opened; clicked_at NULL = not clicked through."
    ),
}


COLUMN_DESCRIPTIONS = {
    "users": {
        "id":              "Primary key",
        "username":        "Unique login handle",
        "email":           "Contact email",
        "first_name":      "Given name",
        "last_name":       "Family name",
        "phone":           "Optional phone number",
        "country":         "ISO 3166-1 alpha-2 code: NL, DE, BE, FR, SE, DK",
        "city":            "City of residence",
        "locale":          "Language/locale tag e.g. en-NL",
        "is_premium":      "1 = current paid Premium subscriber, 0 = free tier "
                           "(this is a snapshot — use subscription_events for full history)",
        "registered_at":   "Account creation timestamp (UTC ISO-8601). "
                           "Use this as the cohort anchor for retention.",
        "last_active_at":  "Timestamp of the most recent app session.",
        "is_active":       "1 = account enabled (Django flag)",
        "is_staff":        "1 = internal staff. Exclude from business analysis.",
        "is_superuser":    "1 = admin. Exclude from business analysis.",
        "date_joined":     "Django built-in (same as registered_at).",
    },
    "app_events": {
        "id":          "Primary key",
        "user_id":     "FK → users.id",
        "session_id":  "Reference to the originating mobile/web session "
                       "(soft link — the session table lives in the operational DB).",
        "event_name":  "Taxonomy value — call list_events() for the full set",
        "event_props": "Optional JSON blob with event-specific payload "
                       "(e.g. {'screen_name': 'dashboard'}, {'source': 'csv_export'})",
        "platform":    "ios | android | web",
        "app_version": "Client app version, e.g. '1.4.2'",
        "os_version":  "OS version string, e.g. 'Android 14', 'iOS 17'",
        "country":     "Denormalised user country (ISO-2)",
        "occurred_at": "Event timestamp (UTC ISO-8601)",
    },
    "experiments": {
        "id":          "Primary key",
        "key":         "Stable machine key, e.g. 'monthly_savings_card'",
        "name":        "Human-readable name",
        "description": "What the experiment does",
        "started_at":  "Experiment go-live timestamp",
        "ended_at":    "NULL while still running",
        "rollout_pct": "Share of users assigned to the treatment variant",
        "status":      "running | completed",
    },
    "experiment_assignments": {
        "id":            "Primary key",
        "experiment_id": "FK → experiments.id",
        "user_id":       "FK → users.id",
        "variant":       "control | treatment",
        "assigned_at":   "When the assignment was decided",
    },
    "subscription_events": {
        "id":                  "Primary key",
        "user_id":             "FK → users.id",
        "event_type":          "started_premium | cancelled_premium | reactivated",
        "plan":                "free | premium",
        "occurred_at":         "Transition timestamp",
        "cancellation_reason": "Free-text reason; only set on cancelled_premium. "
                               "Enumerated: too_expensive, not_using_enough, "
                               "switched_to_competitor, moved_house, other.",
    },
    "notification_campaigns": {
        "id":             "Primary key",
        "key":            "Stable machine key, e.g. 'high_consumption_alert_eco_v1'",
        "campaign_type":  "push | email",
        "variant":        "Optional variant tag for A-B tests, e.g. 'eco_friendly', 'fear_based'",
        "title":          "Notification title (user-visible copy)",
        "body":           "Notification body / preview",
        "target_segment": "Target audience name",
        "sent_at":        "When the campaign was dispatched",
        "total_sent":     "Total audience size",
    },
    "notification_deliveries": {
        "id":           "Primary key",
        "campaign_id":  "FK → notification_campaigns.id",
        "user_id":      "FK → users.id",
        "delivered_at": "Device confirmed delivery",
        "opened_at":    "User opened the app via the push (NULL if not opened)",
        "clicked_at":   "User clicked through to linked content (NULL if not clicked)",
    },
}


# ── Event taxonomy ──────────────────────────────────────────────────────────

EVENT_CATALOG = {
    # Onboarding funnel (in order)
    "signup_started":          "User landed on the signup screen.",
    "signup_email_entered":    "Submitted a valid email.",
    "signup_password_set":     "Finished password creation.",
    "device_pair_started":     "Reached the 'pair your tracker' step.",
    "device_pair_completed":   "Tracker successfully paired.",
    "first_dashboard_view":    "First time the user sees the dashboard.",
    # Engagement
    "app_opened":              "App was foregrounded — primary signal for DAU/MAU/retention.",
    "screen_viewed":           "Navigated to a specific screen. Props: {'screen_name': ...}.",
    # Premium funnel
    "paywall_viewed":          "Paywall shown. Props: {'source': csv_export | history | settings | ...}.",
    "upgrade_clicked":         "Tapped the upgrade CTA on the paywall.",
    "checkout_started":        "Checkout flow started.",
    "checkout_completed":      "Premium purchase succeeded.",
    "subscription_cancelled":  "User cancelled their Premium plan.",
    # Features
    "csv_export_clicked":      "Tapped the 'Download CSV' button.",
    "csv_export_completed":    "CSV export delivered.",
    "monthly_savings_card_viewed":   "Monthly-savings card became visible.",
    "monthly_savings_card_clicked":  "User opened details from the savings card.",
    # Push
    "push_received":           "Device confirmed delivery of a push.",
    "push_opened":             "User opened the app via the push.",
    "push_dismissed":          "Push dismissed without opening.",
}


KEY_METRICS = [
    {
        "metric": "Daily Active Users (DAU)",
        "definition": "COUNT(DISTINCT user_id) per day where event_name='app_opened'.",
        "tables": ["app_events"],
    },
    {
        "metric": "W1 retention by registration cohort",
        "definition": "% of users from a registration week who fired app_opened "
                      "between day 7 and day 13 after registration.",
        "tables": ["users", "app_events"],
    },
    {
        "metric": "Onboarding funnel conversion",
        "definition": "Per-step distinct-user counts from signup_started "
                      "through first_dashboard_view.",
        "tables": ["app_events"],
        "breakdowns": ["platform", "os_version", "country"],
    },
    {
        "metric": "Premium conversion rate",
        "definition": "checkout_completed users / paywall_viewed users (windowed).",
        "tables": ["app_events", "subscription_events"],
        "breakdowns": ["paywall source"],
    },
    {
        "metric": "Push CTR",
        "definition": "clicked_at NOT NULL count / total_sent per campaign.",
        "tables": ["notification_campaigns", "notification_deliveries"],
        "breakdowns": ["variant"],
    },
    {
        "metric": "Feature adoption",
        "definition": "Distinct users who fired the feature event in a window.",
        "tables": ["app_events"],
    },
]


# ── Safety ──────────────────────────────────────────────────────────────────

_BLOCKED = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|REPLACE|ATTACH|DETACH)\b"
    r"|PRAGMA\s+\w+\s*=",
    re.IGNORECASE,
)


def _validate(query: str) -> None:
    upper = query.lstrip().upper()
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        raise ValueError(
            "Only SELECT (and WITH … SELECT …) queries are allowed. "
            "This database is read-only."
        )
    if _BLOCKED.search(query):
        raise ValueError(
            "Query contains a write or structural keyword that is not permitted."
        )


def _conn() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Database not found at {DB_PATH}. "
            "Run `python -m events.cli generate` from the project root first."
        )
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


# ── Tools ───────────────────────────────────────────────────────────────────

@mcp.tool()
def list_tables() -> list[dict]:
    """
    List all tables in the Homa PM Analytics database with a short business
    description. Call this first to orient yourself before writing a query.
    """
    return [{"table": k, "description": v} for k, v in TABLE_DESCRIPTIONS.items()]


@mcp.tool()
def describe_table(table_name: str) -> dict:
    """
    Return columns, SQLite types and business descriptions for a specific
    table. Call this before writing a query if you are not 100% sure of the
    column names — do NOT guess.

    Args:
        table_name: Exact table name. See list_tables() for the full list.
    """
    if table_name not in TABLE_DESCRIPTIONS:
        raise ValueError(
            f"Unknown table '{table_name}'. "
            f"Available: {', '.join(TABLE_DESCRIPTIONS)}"
        )
    conn = _conn()
    try:
        rows = conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
    finally:
        conn.close()
    col_meta = COLUMN_DESCRIPTIONS.get(table_name, {})
    return {
        "table": table_name,
        "description": TABLE_DESCRIPTIONS[table_name],
        "columns": [
            {
                "column": r["name"],
                "type": r["type"],
                "not_null": bool(r["notnull"]),
                "primary_key": bool(r["pk"]),
                "description": col_meta.get(r["name"], ""),
            }
            for r in rows
        ],
    }


@mcp.tool()
def get_schema() -> dict:
    """
    Return all tables, their columns, key metrics and the event taxonomy in
    one call. Useful at the start of a session to load the full data model.
    """
    return {
        "tables": {
            name: {
                "description": desc,
                "columns": COLUMN_DESCRIPTIONS.get(name, {}),
            }
            for name, desc in TABLE_DESCRIPTIONS.items()
        },
        "events": EVENT_CATALOG,
        "key_metrics": KEY_METRICS,
    }


@mcp.tool()
def list_events() -> list[dict]:
    """
    Enumerate the product-event taxonomy stored in app_events.event_name.
    Always check this before writing a funnel or retention query — do NOT
    invent event names.
    """
    return [{"event_name": k, "purpose": v} for k, v in EVENT_CATALOG.items()]


@mcp.tool()
def list_experiments() -> list[dict]:
    """
    List active and completed experiments / feature flags from the
    `experiments` table, including descriptions and current rollout percentage.
    """
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id, key, name, description, started_at, ended_at, "
            "       rollout_pct, status "
            "  FROM experiments ORDER BY started_at"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


@mcp.tool()
def execute_query(query: str) -> dict:
    """
    Execute a read-only SQL SELECT query against the analytics database.
    Results are capped at 500 rows. For large outputs, aggregate or LIMIT
    in the query itself.

    Args:
        query: A valid SQLite SELECT (or WITH … SELECT …) statement.

    Returns:
        dict with keys: columns, rows (list of dicts), row_count, truncated.
    """
    _validate(query)
    conn = _conn()
    try:
        cur = conn.execute(query)
        cols = [d[0] for d in (cur.description or [])]
        raw = cur.fetchmany(501)
        truncated = len(raw) > 500
        rows = raw[:500]
        return {
            "columns": cols,
            "rows": [dict(zip(cols, r)) for r in rows],
            "row_count": len(rows),
            "truncated": truncated,
        }
    except sqlite3.Error as exc:
        raise ValueError(f"SQL error: {exc}") from exc
    finally:
        conn.close()


# ── Metabase integration ────────────────────────────────────────────────────
# Lets the PM ask Claude to create dashboard cards in plain language; Claude
# writes the SQL and dispatches it to Metabase via these tools — the PM never
# touches the Metabase UI.

import sys as _sys
import os as _os
_sys.path.insert(0, _os.path.dirname(__file__))
from metabase_client import get_client as _mb, MetabaseError as _MbErr  # noqa: E402


@mcp.tool()
def metabase_list_dashboards() -> list[dict]:
    """
    List the dashboards that currently exist in the local Metabase. Useful
    before `metabase_create_chart` if you need to confirm the exact name of
    a target dashboard (partial matches are accepted but precise is safer).
    """
    try:
        return _mb().list_dashboards()
    except _MbErr as e:
        raise ValueError(str(e))


@mcp.tool()
def metabase_create_chart(name: str,
                          sql: str,
                          chart_type: str = "line",
                          dashboard: str | None = None,
                          description: str = "") -> dict:
    """
    Create a Metabase question (card) from a SQL query, and optionally
    attach it to an existing dashboard. The user never needs to open the
    Metabase UI — describe what they want, build it here.

    Args:
        name:        Human-readable chart title shown in Metabase.
        sql:         A SELECT query against the analytics DB.
        chart_type:  One of: line, bar, table, funnel, scalar, pie, area, row.
                     Default 'line'. Pick what fits the data:
                       - time series           → line
                       - categorical breakdown → bar
                       - one headline number   → scalar
                       - funnel steps          → funnel
                       - everything else       → table
        dashboard:   Optional. Name (or unambiguous partial) of a dashboard
                     to append the chart to.
        description: Optional one-line description shown under the chart.

    Returns:
        dict with card_id, card_url, and (if attached) dashboard info.
    """
    try:
        client = _mb()
        card = client.create_card(
            name=name, sql=sql,
            display=chart_type, description=description,
        )
        result = {
            "card_id":  card["id"],
            "card_name": card["name"],
            "card_url": f"{client.host}/question/{card['id']}",
        }
        if dashboard:
            attached = client.add_card_to_dashboard(dashboard, card["id"])
            result["dashboard"] = attached
        return result
    except _MbErr as e:
        raise ValueError(str(e))


if __name__ == "__main__":
    mcp.run()
