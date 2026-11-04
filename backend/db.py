from __future__ import annotations
import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from provider_presets import DEFAULT_PROVIDER_PRESETS


APP_DIR = Path(os.environ.get("LOCAL_MODEL_ROUTER_HOME", Path.home() / ".local-model-router"))
APP_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = APP_DIR / "router.db"
BOOTSTRAP_KEY_PATH = APP_DIR / "client.key"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH, timeout=15, factory=ClosingConnection)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def init_db() -> None:
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS deployments (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            alias TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT 'openai',
            base_url TEXT NOT NULL,
            full_url INTEGER NOT NULL DEFAULT 0,
            thinking_mode INTEGER NOT NULL DEFAULT 0,
            upstream_model TEXT NOT NULL,
            api_keys TEXT NOT NULL DEFAULT '[]',
            enabled INTEGER NOT NULL DEFAULT 1,
            base_weight REAL NOT NULL DEFAULT 1,
            auto_weight REAL NOT NULL DEFAULT 1,
            weight_locked INTEGER NOT NULL DEFAULT 0,
            proxy_url TEXT NOT NULL DEFAULT '',
            use_system_proxy INTEGER NOT NULL DEFAULT 1,
            rpm INTEGER NOT NULL DEFAULT 0,
            max_input_tokens INTEGER NOT NULL DEFAULT 0,
            max_output_tokens INTEGER NOT NULL DEFAULT 0,
            timeout_seconds REAL NOT NULL DEFAULT 90,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS request_events (
            id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            protocol TEXT NOT NULL,
            alias TEXT NOT NULL,
            deployment_id TEXT,
            deployment_name TEXT,
            status TEXT NOT NULL,
            status_code INTEGER,
            latency_ms INTEGER,
            error_kind TEXT,
            error_message TEXT,
            attempts TEXT NOT NULL DEFAULT '[]',
            total_tokens INTEGER,
            input_tokens INTEGER,
            output_tokens INTEGER,
            usage TEXT,
            usage_events TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_request_events_created_at ON request_events(created_at);
        CREATE TABLE IF NOT EXISTS deployment_state (
            deployment_id TEXT PRIMARY KEY REFERENCES deployments(id) ON DELETE CASCADE,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            cooldown_until REAL NOT NULL DEFAULT 0,
            successes INTEGER NOT NULL DEFAULT 0,
            failures INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS credential_state (
            deployment_id TEXT NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
            key_index INTEGER NOT NULL,
            cooldown_until REAL NOT NULL DEFAULT 0,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(deployment_id,key_index)
        );
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ai_audits (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            report TEXT NOT NULL,
            changes TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS weight_events (
            id TEXT PRIMARY KEY,
            deployment_id TEXT NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
            source TEXT NOT NULL,
            before_weight REAL NOT NULL,
            after_weight REAL NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """)
        if db.execute("PRAGMA user_version").fetchone()[0] < 1:
            event_columns = {
                row[1] for row in db.execute("PRAGMA table_info(request_events)").fetchall()
            }
            deployment_columns = {
                row[1] for row in db.execute("PRAGMA table_info(deployments)").fetchall()
            }
            if "full_url" not in deployment_columns:
                db.execute("ALTER TABLE deployments ADD COLUMN full_url INTEGER NOT NULL DEFAULT 0")
            if "thinking_mode" not in deployment_columns:
                db.execute(
                    "ALTER TABLE deployments ADD COLUMN thinking_mode INTEGER NOT NULL DEFAULT 0"
                )
            if "config_revision" not in deployment_columns:
                db.execute(
                    "ALTER TABLE deployments ADD COLUMN config_revision INTEGER NOT NULL DEFAULT 0"
                )
            if "total_tokens" not in event_columns:
                db.execute("ALTER TABLE request_events ADD COLUMN total_tokens INTEGER")
            if "input_tokens" not in event_columns:
                db.execute("ALTER TABLE request_events ADD COLUMN input_tokens INTEGER")
            if "output_tokens" not in event_columns:
                db.execute("ALTER TABLE request_events ADD COLUMN output_tokens INTEGER")
            if "usage" not in event_columns:
                db.execute("ALTER TABLE request_events ADD COLUMN usage TEXT")
            if "usage_events" not in event_columns:
                db.execute("ALTER TABLE request_events ADD COLUMN usage_events TEXT")
            for column in ("supplier_id", "supplier_name"):
                if column not in event_columns:
                    db.execute(f"ALTER TABLE request_events ADD COLUMN {column} TEXT")
                if column not in deployment_columns:
                    db.execute(
                        f"ALTER TABLE deployments ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                    )
        if not db.execute("SELECT 1 FROM settings WHERE key='rule_settings'").fetchone():
            db.execute(
                "INSERT INTO settings(key,value) VALUES('rule_settings',?)",
                (
                    json.dumps(
                        {
                            "enabled": True,
                            "cooldown_seconds": 90,
                            "failure_threshold": 3,
                            "max_auto_factor": 1.5,
                            "min_auto_factor": 0.25,
                        }
                    ),
                ),
            )
        # Seed both fresh databases and older installations that never saved presets.
        # An existing entry (even {}) is authoritative, including user deletions.
        db.execute(
            "INSERT OR IGNORE INTO settings(key,value) VALUES('provider_presets',?)",
            (json.dumps(DEFAULT_PROVIDER_PRESETS, ensure_ascii=False),),
        )
        if not BOOTSTRAP_KEY_PATH.exists():
            BOOTSTRAP_KEY_PATH.write_text("sk-local-" + secrets.token_urlsafe(28), encoding="utf-8")
    migrate_indexes()


def migrate_indexes():
    with connect() as db:
        for name, table, columns in (
            ("idx_events_time_id", "request_events", "created_at DESC,id DESC"),
            ("idx_events_alias_time", "request_events", "alias,created_at DESC,id DESC"),
            ("idx_events_status_time", "request_events", "status,created_at DESC,id DESC"),
            ("idx_weights_time", "weight_events", "created_at"),
            ("idx_audits_time", "ai_audits", "created_at"),
        ):
            db.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table}({columns})")
        if db.execute("PRAGMA user_version").fetchone()[0] < 1:
            db.execute("PRAGMA user_version=1")
