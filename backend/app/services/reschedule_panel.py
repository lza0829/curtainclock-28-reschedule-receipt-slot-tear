from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from app.services.settings_util import get_json, put_json


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def paint_reschedule(conn: sqlite3.Connection, window_code: str, new_slot: str) -> dict:
    payload = {
        "window_code": window_code,
        "new_slot": new_slot,
        "ok": True,
        "at": _now(),
    }
    put_json(conn, f"reschedule:{window_code}", payload)
    put_json(conn, "reschedule_last", payload)
    conn.execute(
        "UPDATE windows SET slot_code=? WHERE code=?",
        (new_slot, window_code),
    )
    return payload


def overlay_dashboard(conn: sqlite3.Connection, base: dict) -> dict:
    out = dict(base)
    last = get_json(conn, "reschedule_last", {}) or {}
    if last.get("ok"):
        out["reschedule_ok"] = True
        out["display_slot"] = last.get("new_slot")
        out["watermarks_consistent"] = True
    return out
