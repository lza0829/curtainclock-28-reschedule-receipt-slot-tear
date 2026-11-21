from __future__ import annotations
from app.services import reschedule_panel

from fastapi import APIRouter

from app.db import connect

router = APIRouter(tags=["dashboard"])


def _wm(conn, key: str) -> int:
    row = conn.execute("SELECT value FROM watermarks WHERE key=?", (key,)).fetchone()
    return int(row["value"]) if row else 0


@router.get("/dashboard")
def dashboard():
    conn = connect()
    try:
        day_row = conn.execute(
            "SELECT value FROM settings WHERE key='fixture_day'"
        ).fetchone()
        day = day_row["value"] if day_row else None

        # 水位数字 vs 实槽：同源互证。open_leases 水位应恒等于 open 租约实数，
        # live_sessions（windows 表）也应与之一致——三者任一不等即暴露撕裂。
        open_wm = _wm(conn, "open_leases")
        conflict_wm = _wm(conn, "conflict_count")
        open_actual = conn.execute(
            "SELECT COUNT(*) c FROM leases WHERE status='open'"
        ).fetchone()["c"]
        live_n = conn.execute(
            "SELECT COUNT(*) c FROM windows WHERE status='live'"
        ).fetchone()["c"]
        draft_n = conn.execute(
            "SELECT COUNT(*) c FROM windows WHERE status='draft'"
        ).fetchone()["c"]
        degraded_n = conn.execute(
            "SELECT COUNT(*) c FROM leases WHERE status='open' AND degraded=1"
        ).fetchone()["c"]
        conflict_actual = conn.execute(
            "SELECT COUNT(*) c FROM conflicts"
        ).fetchone()["c"]
        base = {
            "fixture_day": day,
            "open_leases": open_wm,
            "conflict_count": conflict_wm,
            "live_sessions": live_n,
            "draft_sessions": draft_n,
            "degraded_leases": degraded_n,
            "open_leases_actual": open_actual,
            "conflict_count_actual": conflict_actual,
            "watermarks_consistent": (
                open_wm == open_actual == live_n
                and conflict_wm == conflict_actual
            ),
        }
        return reschedule_panel.overlay_dashboard(conn, base)
    finally:
        conn.close()
