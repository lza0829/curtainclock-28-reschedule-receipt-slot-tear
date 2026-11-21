from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from app.services.ready import (
    executor_blocker,
    free_standbys,
    main_slot_blocker,
    resource_busy,
)
from app.services.settings_util import put_json

# ============================================================================
# 并发口径（全局唯一）：**开窗写锁内一次扫落**
# ----------------------------------------------------------------------------
# 本模块所有写操作都必须由调用方包在 transaction() 里（BEGIN IMMEDIATE）。
# open_lease 在写锁内完成：读批窗 → 复检就绪 → 按优先级扫候补 → 直接插 open。
# 检查与插入同锁、同事务；不存在锁外先读后写，也不存在预占表。
# 后到者在自己的 BEGIN IMMEDIATE 拿到锁时，必然看到先到者已提交的 open，
# 于是 resource_busy / 候补扫描会把它排除，绝不会双 open。
# ============================================================================


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bump(conn: sqlite3.Connection, key: str, delta: int) -> int:
    row = conn.execute("SELECT value FROM watermarks WHERE key=?", (key,)).fetchone()
    cur = int(row["value"]) if row else 0
    nxt = cur + delta
    conn.execute(
        "INSERT INTO watermarks(key, value, updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, nxt, _now()),
    )
    return nxt


def _watermark(conn: sqlite3.Connection, key: str) -> int:
    row = conn.execute("SELECT value FROM watermarks WHERE key=?", (key,)).fetchone()
    return int(row["value"]) if row else 0


def _next_lease_no(conn: sqlite3.Connection, day: str) -> str:
    n = conn.execute("SELECT COUNT(*) c FROM leases").fetchone()["c"] + 1
    return f"L-{day.replace('-', '')}-{n:04d}"


def _record_conflict(
    conn: sqlite3.Connection,
    *,
    kind: str,
    session: sqlite3.Row,
    resource_code: str,
    reason: str,
) -> None:
    conn.execute(
        "INSERT INTO conflicts(kind, window_code, resource_code, day, start_min, end_min, reason, created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (
            kind,
            session["code"],
            resource_code,
            session["day"],
            session["start_min"],
            session["end_min"],
            reason,
            _now(),
        ),
    )
    _bump(conn, "conflict_count", 1)


def _insert_open_lease(
    conn: sqlite3.Connection,
    *,
    session: sqlite3.Row,
    slot_code: str,
    degraded: bool,
) -> dict:
    lease_no = _next_lease_no(conn, session["day"])
    try:
        conn.execute(
            "INSERT INTO leases(lease_no, window_code, slot_code, main_slot_code, executor_code, "
            "day, start_min, end_min, degraded, status, opened_at) "
            "VALUES(?,?,?,?,?,?,?,?,?, 'open', ?)",
            (
                lease_no,
                session["code"],
                slot_code,
                session["slot_code"],
                session["executor_code"],
                session["day"],
                session["start_min"],
                session["end_min"],
                1 if degraded else 0,
                _now(),
            ),
        )
    except sqlite3.IntegrityError as e:
        # 触发器/唯一索引兜底：写锁内理论上到不了这里，到了说明口径被破坏。
        msg = str(e)
        if "slot_overlap" in msg:
            raise ValueError("候选算力槽刚被占用，开窗失败") from e
        if "executor_overlap" in msg:
            raise ValueError("执行器刚被占用，开窗失败") from e
        if "ux_leases_one_open_per_window" in msg or "UNIQUE" in msg:
            raise ValueError("批窗已在占用中，勿重复开窗") from e
        raise

    conn.execute("UPDATE windows SET status='live' WHERE code=?", (session["code"],))
    open_n = _bump(conn, "open_leases", 1)
    return {
        "ok": True,
        "lease_no": lease_no,
        "window_code": session["code"],
        "slot_code": slot_code,
        "main_slot_code": session["slot_code"],
        "degraded": degraded,
        "status": "open",
        "open_leases": open_n,
    }


def open_lease(conn: sqlite3.Connection, window_code: str) -> dict:
    """在写锁内（调用方持有 BEGIN IMMEDIATE）完成一次开窗。

    返回值约定：
      ok=True  已开；含 degraded / slot_code。
      ok=False 冲突且不可降级（已落冲突行，由调用方提交）。
    """
    session = conn.execute(
        "SELECT * FROM windows WHERE code=?", (window_code,)
    ).fetchone()
    if session is None:
        raise ValueError(f"批窗不存在：{window_code}")
    if session["status"] == "live":
        raise ValueError("批窗已在占用中，勿重复开窗")
    if session["status"] == "done":
        raise ValueError("批窗已结束")

    # 本题默认允许候补降级占用：不设 reject/standby 总开关，
    # 主槽冲突一律先尝试候补，候补耗尽才判冲突。

    # ---- 1. 执行器是硬约束：不可用直接冲突，不考虑替代 ----
    exec_block = executor_blocker(conn, session)
    if exec_block:
        _record_conflict(
            conn,
            kind="teacher",
            session=session,
            resource_code=session["executor_code"],
            reason=exec_block,
        )
        return {"ok": False, "error": exec_block}

    # ---- 2. 主槽可用：直接落 open（非降级） ----
    slot_block = main_slot_blocker(conn, session)
    if slot_block is None:
        return _insert_open_lease(
            conn, session=session, slot_code=session["slot_code"], degraded=False
        )

    # ---- 3. 主槽冲突：按优先级在写锁内扫候补并直接落 open ----
    # 允许降级（本题默认允许候补降级占用）。扫描与插入同锁，后到者看不到已占候补。
    for slot in free_standbys(conn, session):
        return _insert_open_lease(
            conn, session=session, slot_code=slot["code"], degraded=True
        )

    # ---- 4. 无空闲候补：落主槽冲突，拒绝 ----
    _record_conflict(
        conn,
        kind="room",
        session=session,
        resource_code=session["slot_code"],
        reason=slot_block,
    )
    return {
        "ok": False,
        "error": slot_block + "；且无空闲候补",
    }


def close_lease(conn: sqlite3.Connection, lease_no: str) -> dict:
    lease = conn.execute(
        "SELECT * FROM leases WHERE lease_no=?", (lease_no,)
    ).fetchone()
    if lease is None:
        raise ValueError(f"租约不存在：{lease_no}")
    if lease["status"] != "open":
        raise ValueError("租约不在占用态")
    conn.execute(
        "UPDATE leases SET status='closed', closed_at=? WHERE lease_no=?",
        (_now(), lease_no),
    )
    conn.execute(
        "UPDATE windows SET status='done' WHERE code=?",
        (lease["window_code"],),
    )
    open_n = _bump(conn, "open_leases", -1)
    return {
        "lease_no": lease_no,
        "status": "closed",
        "open_leases": open_n,
    }


def reschedule_lease(conn: sqlite3.Connection, window_code: str, new_slot: str) -> dict:
    """live 改挂：把占用中租约从现行槽迁到 new_slot。

    索引架构（全局唯一口径）：占用索引（leases 的 open 行）现行键是唯一事实源。
    预检 → 迁移 → 批窗展示字段 → 回执，全部在调用方持有的写锁、同一事务内完成：
      - 旧键释放与新键占用是同一条 UPDATE，原子，不存在半截；
      - 水位按净变迁：open_leases 一键换一键，净零，不 bump；
      - 回执只是索引现行态的投影，必须能由索引重放（见 routers/dashboard）；
      - 任一步失败整个事务回滚：不留双键、不留假回执、不动展示字段。
    并发改挂由 BEGIN IMMEDIATE 串行：后到者看到先到者已提交的现行键，
    从现行键再迁，双目标并发最终只落一键。
    """
    window = conn.execute(
        "SELECT * FROM windows WHERE code=?", (window_code,)
    ).fetchone()
    if window is None:
        raise ValueError(f"批窗不存在：{window_code}")
    lease = conn.execute(
        "SELECT * FROM leases WHERE window_code=? AND status='open'",
        (window_code,),
    ).fetchone()
    if lease is None:
        # 与关窗交错：关窗先提交则这里必然看不到 open 租约——
        # 改挂整体拒绝并回滚，不得留下「窗已关、键已迁」的半截。
        raise ValueError("批窗不在占用态，无可改挂的租约")
    old_slot = lease["slot_code"]

    if new_slot == old_slot:
        # 目标即现行键：幂等重放，由索引直接回执，不落任何写。
        return {
            "ok": True,
            "lease_no": lease["lease_no"],
            "window_code": window_code,
            "old_slot": old_slot,
            "new_slot": new_slot,
            "slot_code": lease["slot_code"],
            "status": "open",
            "open_leases": _watermark(conn, "open_leases"),
            "at": _now(),
        }

    # ---- 冲突预检（与开窗同一套 ready 口径，排除自己）----
    # 目标槽须存在、启用、时段空闲；预检失败落冲突行（由调用方提交），
    # 租约/批窗/水位一概不动——失败回滚不留双键。
    slot = conn.execute(
        "SELECT * FROM slots WHERE code=?", (new_slot,)
    ).fetchone()
    if slot is None:
        reason = f"算力槽 {new_slot} 不存在"
        _record_conflict(
            conn, kind="room", session=window, resource_code=new_slot, reason=reason
        )
        return {"ok": False, "error": reason}
    if slot["active"] != 1:
        reason = f"算力槽 {new_slot} 未启用"
        _record_conflict(
            conn, kind="room", session=window, resource_code=new_slot, reason=reason
        )
        return {"ok": False, "error": reason}
    busy = resource_busy(
        conn,
        kind="room",
        resource_code=new_slot,
        day=lease["day"],
        start_min=lease["start_min"],
        end_min=lease["end_min"],
        exclude_session=window_code,
    )
    if busy is not None:
        reason = f"算力槽 {new_slot} 已被租约 {busy['lease_no']} 占用"
        _record_conflict(
            conn, kind="room", session=window, resource_code=new_slot, reason=reason
        )
        return {"ok": False, "error": reason}

    # ---- 原子迁移：旧键释放 + 新键占用 = 同一条 UPDATE ----
    # 改挂后目标槽即批窗新主槽：main_slot_code 跟随、degraded 归零。
    # 更新触发器（trg_lease_slot_overlap_upd 等）是第二道防线。
    try:
        conn.execute(
            "UPDATE leases SET slot_code=?, main_slot_code=?, degraded=0 "
            "WHERE lease_no=? AND status='open'",
            (new_slot, new_slot, lease["lease_no"]),
        )
    except sqlite3.IntegrityError as e:
        # 写锁内理论上到不了这里；到了说明口径被破坏，抛出让整个事务回滚。
        raise ValueError(f"改挂被占用索引拒绝：{e}") from e
    # 批窗展示字段跟随索引现行键（同事务，不外溢）。
    conn.execute(
        "UPDATE windows SET slot_code=? WHERE code=?",
        (new_slot, window_code),
    )

    # 回执 = 迁移后索引现行态的投影（可重放）；settings 仅作审计留痕。
    payload = {
        "ok": True,
        "lease_no": lease["lease_no"],
        "window_code": window_code,
        "old_slot": old_slot,
        "new_slot": new_slot,
        "slot_code": new_slot,
        "status": "open",
        "open_leases": _watermark(conn, "open_leases"),
        "at": _now(),
    }
    put_json(conn, f"reschedule:{window_code}", payload)
    put_json(conn, "reschedule_last", payload)
    return payload
