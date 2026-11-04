from __future__ import annotations
import json
import random
import time
import uuid
from typing import Any

from db import connect, now_iso
from security import reveal_secret


class NoAvailableCredentials(RuntimeError):
    """The deployment lost its eligible credentials after candidate selection."""


def deployment_snapshot(alias=None):
    with connect() as db:
        deps = db.execute(
            "SELECT * FROM deployments"
            + (" WHERE alias=? AND enabled=1" if alias is not None else " ORDER BY alias,name"),
            (alias,) if alias is not None else (),
        ).fetchall()
        states = {r["deployment_id"]: dict(r) for r in db.execute("SELECT * FROM deployment_state")}
        credentials = {}
        for r in db.execute("SELECT * FROM credential_state"):
            credentials.setdefault(r["deployment_id"], {})[r["key_index"]] = dict(r)
        row = db.execute("SELECT value FROM settings WHERE key='rule_settings'").fetchone()
    rules = json.loads(row[0]) if row else {}
    rules["failure_threshold"] = max(3, int(rules.get("failure_threshold", 3)))
    return deps, states, credentials, rules


def snapshot_state(dep, states, credentials, rules, count):
    state = dict(
        states.get(
            dep["id"], dict(consecutive_failures=0, cooldown_until=0, successes=0, failures=0)
        )
    )
    allowed = cooldown_allowed(dep, state["consecutive_failures"], rules)
    if not allowed:
        state["cooldown_until"] = 0
    available = [
        i
        for i in range(count)
        if not allowed
        or i not in credentials.get(dep["id"], {})
        or credentials[dep["id"]][i]["consecutive_failures"] < rules["failure_threshold"]
        or credentials[dep["id"]][i]["cooldown_until"] <= time.time()
    ]
    state["health"] = (
        "cooldown"
        if state["cooldown_until"] > time.time() or (count and not available)
        else ("degraded" if state["consecutive_failures"] else "ready")
    )
    if not count:
        state["health"] = "no_credentials"
    return state, available


def safe_deployments(rows=None):
    snapshot, states, credentials, rules = deployment_snapshot()
    result = []
    for row in snapshot if rows is None else rows:
        item = dict(row)
        keys = [reveal_secret(key) for key in json.loads(item.pop("api_keys", "[]"))]
        item["key_count"] = len(keys)
        item["key_masked"] = [f"••••{key[-4:]}" for key in keys]
        for field in ("enabled", "weight_locked", "use_system_proxy", "thinking_mode", "full_url"):
            item[field] = bool(item[field])
        state, available = snapshot_state(item, states, credentials, rules, len(keys))
        item.update(state)
        item["available_keys"] = len(available)
        item["effective_weight"] = effective_weight(item)
        result.append(item)
    return result


def safe_deployment(row):
    return safe_deployments([row])[0]


def get_state(deployment_id: str) -> dict[str, Any]:
    with connect() as db:
        row = db.execute(
            "SELECT * FROM deployment_state WHERE deployment_id=?", (deployment_id,)
        ).fetchone()
        dep = db.execute("SELECT * FROM deployments WHERE id=?", (deployment_id,)).fetchone()
    if not row:
        return {
            "consecutive_failures": 0,
            "cooldown_until": 0,
            "successes": 0,
            "failures": 0,
            "health": "ready",
        }
    result = dict(row)
    if dep and not cooldown_allowed(dep, result["consecutive_failures"], get_rule_settings()):
        result["cooldown_until"] = 0
    result["health"] = (
        "cooldown"
        if result["cooldown_until"] > time.time()
        else ("degraded" if result["consecutive_failures"] else "ready")
    )
    return result


def get_rule_settings() -> dict[str, Any]:
    with connect() as db:
        row = db.execute("SELECT value FROM settings WHERE key='rule_settings'").fetchone()
    rules = json.loads(row[0]) if row else {}
    rules["failure_threshold"] = max(3, int(rules.get("failure_threshold", 3)))
    return rules


def effective_weight(dep) -> float:
    return dep["base_weight"] if dep["weight_locked"] else dep["base_weight"] * dep["auto_weight"]


def cooldown_allowed(dep, consecutive: int, rules: dict[str, Any]) -> bool:
    return bool(
        rules.get("enabled", True)
        and consecutive >= max(3, rules.get("failure_threshold", 3))
        and effective_weight(dep) < 50
    )


def record_result(
    deployment_id: str,
    success: bool,
    status_code: int | None,
    retry_after: float | None = None,
    *,
    key_index: int | None = None,
    config_revision: int | None = None,
) -> None:
    rules = get_rule_settings()
    with connect() as db:
        # Serialize results with saves so a response from an old configuration cannot
        # reapply penalties after the user has reset the deployment.
        db.execute("BEGIN IMMEDIATE")
        dep = db.execute("SELECT * FROM deployments WHERE id=?", (deployment_id,)).fetchone()
        if not dep or (config_revision is not None and dep["config_revision"] != config_revision):
            return
        db.execute(
            "INSERT OR IGNORE INTO deployment_state(deployment_id,updated_at) VALUES(?,?)",
            (deployment_id, now_iso()),
        )
        row = db.execute(
            "SELECT * FROM deployment_state WHERE deployment_id=?", (deployment_id,)
        ).fetchone()
        failures = row["failures"] + (0 if success else 1)
        successes = row["successes"] + (1 if success else 0)
        consecutive = 0 if success else row["consecutive_failures"] + 1
        # Decide protection using the weight BEFORE this result reduces it. Crossing
        # below 50 permits cooling on the next failed request, never on this one.
        allow_cooldown = not success and cooldown_allowed(dep, consecutive, rules)
        factor = dep["auto_weight"]
        if rules.get("enabled", True) and not dep["weight_locked"]:
            if success and successes > 0 and successes % 20 == 0:
                factor = max(factor, min(rules.get("max_auto_factor", 1.5), factor + 0.03))
            elif not success:
                factor = min(
                    factor,
                    max(
                        rules.get("min_auto_factor", 0.25),
                        factor * (0.8 if status_code == 429 else 0.9),
                    ),
                )
            if factor != dep["auto_weight"]:
                db.execute(
                    "UPDATE deployments SET auto_weight=? WHERE id=?", (factor, deployment_id)
                )
                reason = (
                    "累计成功请求达到 20 的倍数，逐步提高自动权重"
                    if success
                    else f"连续失败 {consecutive} 次，优先降低自动权重"
                )
                db.execute(
                    "INSERT INTO weight_events VALUES(?,?,?,?,?,?,?)",
                    (
                        str(uuid.uuid4()),
                        deployment_id,
                        "rule",
                        dep["auto_weight"],
                        factor,
                        reason,
                        now_iso(),
                    ),
                )
        cooldown_until = (
            time.time() + max(rules.get("cooldown_seconds", 90), retry_after or 0)
            if allow_cooldown
            else 0
        )
        db.execute(
            "UPDATE deployment_state SET consecutive_failures=?,cooldown_until=?,successes=?,failures=?,updated_at=? WHERE deployment_id=?",
            (consecutive, cooldown_until, successes, failures, now_iso(), deployment_id),
        )
        if success or effective_weight(dep) >= 50 or not rules.get("enabled", True):
            db.execute(
                "UPDATE credential_state SET cooldown_until=0 WHERE deployment_id=?",
                (deployment_id,),
            )
        if key_index is not None:
            key_row = db.execute(
                "SELECT consecutive_failures FROM credential_state WHERE deployment_id=? AND key_index=?",
                (deployment_id, key_index),
            ).fetchone()
            key_failures = 0 if success else (key_row[0] if key_row else 0) + 1
            key_cooldown = 0
            if (
                allow_cooldown
                and key_failures >= rules["failure_threshold"]
                and status_code in (401, 403, 429)
            ):
                key_cooldown = time.time() + max(
                    retry_after or 0,
                    3600 if status_code in (401, 403) else rules.get("cooldown_seconds", 90),
                )
            db.execute(
                "INSERT INTO credential_state(deployment_id,key_index,cooldown_until,consecutive_failures,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(deployment_id,key_index) DO UPDATE SET cooldown_until=excluded.cooldown_until,consecutive_failures=excluded.consecutive_failures,updated_at=excluded.updated_at",
                (deployment_id, key_index, key_cooldown, key_failures, now_iso()),
            )


def candidates(alias: str) -> list[dict[str, Any]]:
    rows, states, credentials, rules = deployment_snapshot(alias)
    result = []
    for row in rows:
        dep = dict(row)
        dep["api_keys"] = [reveal_secret(k) for k in json.loads(dep["api_keys"])]
        state, available = snapshot_state(dep, states, credentials, rules, len(dep["api_keys"]))
        if state["cooldown_until"] > time.time() or not available:
            continue
        dep["weight"] = effective_weight(dep)
        result.append(dep)
    return result


def available_key_indices(deployment_id: str, count: int) -> list[int]:
    with connect() as db:
        available, _ = credential_selection(db, deployment_id, count)
    return available


def credential_selection(db, deployment_id: str, count: int):
    # Read current eligibility and the cursor together. Selection calls this
    # after BEGIN IMMEDIATE, so saves/results cannot race the rotation write.
    # A single indexed join also avoids separate settings/state connections.
    rows = db.execute(
        """SELECT d.base_weight,d.auto_weight,d.weight_locked,
                  coalesce(s.consecutive_failures,0) AS deployment_failures,
                  c.key_index,c.consecutive_failures,c.cooldown_until,
                  r.value AS rule_settings,k.value AS key_cursor
           FROM (SELECT ? AS id) target
           LEFT JOIN deployments d ON d.id=target.id
           LEFT JOIN deployment_state s ON s.deployment_id=target.id
           LEFT JOIN credential_state c ON c.deployment_id=target.id
           LEFT JOIN settings r ON r.key='rule_settings'
           LEFT JOIN settings k ON k.key=?""",
        (deployment_id, f"key_cursor:{deployment_id}"),
    ).fetchall()
    dep = rows[0]
    rules = json.loads(dep["rule_settings"]) if dep["rule_settings"] else {}
    rules["failure_threshold"] = max(3, int(rules.get("failure_threshold", 3)))
    if dep["base_weight"] is None or not cooldown_allowed(dep, dep["deployment_failures"], rules):
        return list(range(count)), dep["key_cursor"]
    states = {row["key_index"]: row for row in rows if row["key_index"] is not None}
    now = time.time()
    available = [
        index
        for index in range(count)
        if index not in states
        or states[index]["consecutive_failures"] < rules["failure_threshold"]
        or states[index]["cooldown_until"] <= now
    ]
    return available, dep["key_cursor"]


def weighted_order(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pool = list(items)
    ordered = []
    while pool:
        weights = [max(0.01, float(item.get("weight", 1))) for item in pool]
        selected = random.choices(pool, weights=weights, k=1)[0]
        ordered.append(selected)
        pool.remove(selected)
    return ordered


def choose_credential(dep: dict[str, Any]) -> tuple[str, int]:
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        available, saved_cursor = credential_selection(db, dep["id"], len(dep["api_keys"]))
        if not available:
            raise NoAvailableCredentials("No available credentials for deployment")
        cursor = int(saved_cursor) if saved_cursor is not None else random.randrange(len(available))
        next_cursor = (cursor + 1) % len(available)
        db.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (f"key_cursor:{dep['id']}", str(next_cursor)),
        )
    key_index = available[cursor % len(available)]
    return dep["api_keys"][key_index], key_index
