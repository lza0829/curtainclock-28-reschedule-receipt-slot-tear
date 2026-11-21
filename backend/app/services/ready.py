from __future__ import annotations
from app.services import reschedule_panel

import sqlite3

# 就绪与候补扫描的唯一实现。所有“某槽是否空闲/某候补能否用”的判断都走这里，
# 开窗写锁内与只读页面共用同一口径，避免两套判断撕裂。


def room_active(conn: sqlite3.Connection, slot_code: str) -> bool:
    row = conn.execute(
        "SELECT active FROM slots WHERE code=?", (slot_code,)
    ).fetchone()
    return bool(row and row["active"] == 1)


def teacher_active(conn: sqlite3.Connection, executor_code: str) -> bool:
    row = conn.execute(
        "SELECT active FROM executors WHERE code=?", (executor_code,)
    ).fetchone()
    return bool(row and row["active"] == 1)


def overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def resource_busy(
    conn: sqlite3.Connection,
    *,
    kind: str,
    resource_code: str,
    day: str,
    start_min: int,
    end_min: int,
    exclude_session: str | None = None,
) -> sqlite3.Row | None:
    col = "slot_code" if kind == "room" else "executor_code"
    rows = conn.execute(
        f"SELECT * FROM leases WHERE {col}=? AND day=? AND status='open'",
        (resource_code, day),
    ).fetchall()
    for r in rows:
        if exclude_session and r["window_code"] == exclude_session:
            continue
        if overlap(start_min, end_min, r["start_min"], r["end_min"]):
            return r
    return None


def standby_candidates(
    conn: sqlite3.Connection, session: sqlite3.Row
) -> list[sqlite3.Row]:
    """按优先级返回同主题的候补槽（含是否空闲的判定在调用方按租约再算）。

    排序：standby_priority 升序（数字小=优先）→ capacity 降序 → code 升序。
    必须是启用态候补槽，且排除批窗自己的主槽。
    """
    rows = conn.execute(
        "SELECT * FROM slots "
        "WHERE active=1 AND is_standby=1 AND subject=? AND code<>? "
        "ORDER BY standby_priority ASC, capacity DESC, code ASC",
        (session["subject"], session["slot_code"]),
    ).fetchall()
    return list(rows)


def free_standbys(
    conn: sqlite3.Connection, session: sqlite3.Row
) -> list[sqlite3.Row]:
    """候补槽里时段真正空闲的那些，保持优先级顺序。"""
    out: list[sqlite3.Row] = []
    for slot in standby_candidates(conn, session):
        busy = resource_busy(
            conn,
            kind="room",
            resource_code=slot["code"],
            day=session["day"],
            start_min=session["start_min"],
            end_min=session["end_min"],
            exclude_session=session["code"],
        )
        if busy is None:
            out.append(slot)
    return out


def main_slot_blocker(conn: sqlite3.Connection, session: sqlite3.Row) -> str | None:
    """主槽不可用的原因（None 表示主槽可用）。只看主槽，不含执行器。"""
    if not room_active(conn, session["slot_code"]):
        return f"算力槽 {session['slot_code']} 未启用"
    busy = resource_busy(
        conn,
        kind="room",
        resource_code=session["slot_code"],
        day=session["day"],
        start_min=session["start_min"],
        end_min=session["end_min"],
        exclude_session=session["code"],
    )
    if busy is not None:
        return f"算力槽已被租约 {busy['lease_no']} 占用"
    return None


def executor_blocker(conn: sqlite3.Connection, session: sqlite3.Row) -> str | None:
    if not teacher_active(conn, session["executor_code"]):
        return f"执行器 {session['executor_code']} 未启用"
    busy = resource_busy(
        conn,
        kind="teacher",
        resource_code=session["executor_code"],
        day=session["day"],
        start_min=session["start_min"],
        end_min=session["end_min"],
        exclude_session=session["code"],
    )
    if busy is not None:
        return f"执行器已被租约 {busy['lease_no']} 占用"
    return None


def readiness(conn: sqlite3.Connection, session: sqlite3.Row) -> dict:
    """只读态就绪判断。

    ready：主槽 + 执行器都可用。
    degradable：主槽不可用、但执行器可用且存在空闲候补（可降级）。
    页面据此展示“就绪 / 可降级 / 冲突”。
    """
    reasons: list[str] = []
    slot_block = main_slot_blocker(conn, session)
    if slot_block:
        reasons.append(slot_block)
    exec_block = executor_blocker(conn, session)
    if exec_block:
        reasons.append(exec_block)

    ready = not reasons
    degradable = False
    free = free_standbys(conn, session)
    if not ready and slot_block and not exec_block and free:
        degradable = True

    return {
        "ready": ready,
        "degradable": degradable,
        "reasons": reasons,
        "standby_free": [s["code"] for s in free],
    }


def reschedule_surface(conn, window_code, new_slot):
    return reschedule_panel.paint_reschedule(conn, window_code, new_slot)
