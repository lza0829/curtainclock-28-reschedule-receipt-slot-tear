from __future__ import annotations

from fastapi import APIRouter

from app.db import connect
from app.services.settings_util import get_json

router = APIRouter(tags=["dashboard"])


def _wm(conn, key: str) -> int:
    row = conn.execute("SELECT value FROM watermarks WHERE key=?", (key,)).fetchone()
    return int(row["value"]) if row else 0


def _overlay_reschedule(conn, base: dict) -> dict:
    """总览的改挂区块——索引架构：回执只是占用索引的投影，必须能由索引重放。

    - 索引现行键 == 回执新槽        → replayed：展示成功与现行槽；
    - 租约仍 open 但索引键 != 新槽  → torn：回执与索引撕裂，必须亮红，不得宣称成功；
    - 租约已关窗                    → settled：历史回执，中性展示。
    任何情况下都不许用回执漂白 watermarks_consistent——它只由实槽计数算出。
    """
    out = dict(base)
    last = get_json(conn, "reschedule_last", None)
    if not last or not last.get("ok"):
        return out
    lease = conn.execute(
        "SELECT slot_code FROM leases WHERE window_code=? AND status='open'",
        (last.get("window_code"),),
    ).fetchone()
    if lease is None:
        state = "settled"
    elif lease["slot_code"] == last.get("new_slot"):
        state = "replayed"
    else:
        state = "torn"
    out["reschedule_last"] = last
    out["reschedule_state"] = state
    out["reschedule_ok"] = state == "replayed"
    out["display_slot"] = lease["slot_code"] if lease else None
    return out


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
        return _overlay_reschedule(conn, base)
    finally:
        conn.close()
