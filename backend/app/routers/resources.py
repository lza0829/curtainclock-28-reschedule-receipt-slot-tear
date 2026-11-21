from __future__ import annotations

from fastapi import APIRouter

from app.db import connect

router = APIRouter(tags=["resources"])


@router.get("/slots")
def list_slots():
    """槽位页：每个槽加实时占用（来自 open 租约，与水位同源）。"""
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT code, name, subject, capacity, is_standby, standby_priority, active "
            "FROM slots ORDER BY is_standby, standby_priority, code"
        ).fetchall()
        out = []
        for r in rows:
            leases = conn.execute(
                "SELECT lease_no, window_code, day, start_min, end_min "
                "FROM leases WHERE status='open' AND slot_code=? ORDER BY day, start_min",
                (r["code"],),
            ).fetchall()
            item = dict(r)
            item["open_leases"] = [dict(x) for x in leases]
            item["occupied"] = len(leases) > 0
            out.append(item)
        return out
    finally:
        conn.close()


@router.get("/executors")
def list_executors():
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT code, name, subject, active FROM executors ORDER BY code"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
