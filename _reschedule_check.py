# -*- coding: utf-8 -*-
"""live 改挂夹具自检（索引架构：以占用索引现行键为准）。

口径：改挂 = 写锁内一次原子迁移占用索引键；回执/批窗展示/总览必须能由索引重放；
水位按净变迁；失败回滚不留双键；并发改挂串行收敛。

夹具：
  F1 回执新槽 = 索引现行 = 批窗展示（改挂成功三面互证）
  F2 旧槽可开性恢复、新槽挡后来者、水位不双计
  F3 失败回滚不留双键（忙槽/不存在槽）；交错半截（关窗后改挂、草稿窗改挂）
  F4 并发双改挂收敛（双目标 / 同目标 / 改挂 vs 关窗）
  F5 撕裂注入：回执新槽≠索引现行时，总览必须亮撕裂，不得漂白

用法：
  python _reschedule_check.py [base_url] [db_path]
  base_url 默认 http://127.0.0.1:11100/api
  db_path  默认 $CURTAINCLOCK_DB 或 <repo>/data/curtainclock.db（F5 撕裂注入用；
           文件不存在则跳过 F5）
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:11100/api"
DB_PATH = (
    sys.argv[2]
    if len(sys.argv) > 2
    else os.environ.get(
        "CURTAINCLOCK_DB",
        str(Path(__file__).resolve().parent / "data" / "curtainclock.db"),
    )
)

DAY = "2026-11-23"  # 独立夹具日，避开 seed 与其他自检脚本
S, E = 9 * 60, 10 * 60 + 30


def call(method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"detail": raw}
        return e.code, payload


def create_window(code, subject, slot, executor, day=DAY, s=S, e=E):
    st, out = call("POST", "/windows", {
        "code": code, "title": code, "subject": subject,
        "slot_code": slot, "executor_code": executor,
        "day": day, "start_min": s, "end_min": e,
    })
    assert st == 200, (code, st, out)
    return out


def open_leases():
    st, rows = call("GET", "/leases")
    assert st == 200
    return [r for r in rows if r["status"] == "open"]


def close_all():
    for r in open_leases():
        call("POST", "/leases/close", {"lease_no": r["lease_no"]})


def dashboard():
    st, d = call("GET", "/dashboard")
    assert st == 200, d
    return d


def assert_consistent(label):
    d = dashboard()
    rows = open_leases()
    assert d["open_leases"] == len(rows) == d["live_sessions"], (label, d, len(rows))
    assert d["watermarks_consistent"] is True, (label, d)
    return d


def window_of(code):
    st, rows = call("GET", "/windows")
    assert st == 200
    for r in rows:
        if r["code"] == code:
            return r
    raise AssertionError(f"批窗不存在：{code}")


def lease_of(window_code):
    for r in open_leases():
        if r["window_code"] == window_code:
            return r
    return None


def slot_occupants():
    st, slots = call("GET", "/slots")
    assert st == 200
    return {s["code"]: [l["lease_no"] for l in s["open_leases"]] for s in slots}


def race(tasks):
    """tasks: list of (method, path, body)。并发发令，按任务下标对齐返回 (status, payload)。"""
    results = [None] * len(tasks)
    barrier = threading.Barrier(len(tasks))

    def worker(i, t):
        barrier.wait()
        results[i] = call(t[0], t[1], t[2])

    ts = [threading.Thread(target=worker, args=(i, t)) for i, t in enumerate(tasks)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return results


def main():
    close_all()

    # ---------- F1：回执新槽 = 索引现行 = 批窗展示 ----------
    create_window("RS-F1", "ETL", "S-ETL-01", "E-PIPE-01")
    st, opened = call("POST", "/leases/open", {"window_code": "RS-F1"})
    assert st == 200 and opened["ok"] and opened["slot_code"] == "S-ETL-01", opened
    lease_no = opened["lease_no"]

    st, r1 = call("POST", "/reschedule", {"window_code": "RS-F1", "new_slot": "S-ETL-02"})
    assert st == 200 and r1["ok"], (st, r1)
    assert r1["old_slot"] == "S-ETL-01" and r1["new_slot"] == "S-ETL-02", r1
    assert r1["open_leases"] == 1, r1  # 水位净变迁：一键换一键，总数不变

    lease = lease_of("RS-F1")
    assert lease and lease["slot_code"] == "S-ETL-02", ("索引现行键应为新槽", lease)
    assert lease["lease_no"] == lease_no and lease["degraded"] in (0, False), lease
    win = window_of("RS-F1")
    assert win["slot_code"] == "S-ETL-02" and win["status"] == "live", ("批窗展示应跟随索引", win)
    occ = slot_occupants()
    assert occ["S-ETL-02"] == [lease_no] and not occ["S-ETL-01"], ("槽位页互证", occ)
    d = assert_consistent("F1")
    assert d["reschedule_state"] == "replayed" and d["reschedule_ok"] is True, d
    assert d["display_slot"] == "S-ETL-02" and d["reschedule_last"]["new_slot"] == "S-ETL-02", d
    print("F1 PASS: 回执新槽 = 索引现行 = 批窗展示 = 槽位页，水位净零")

    # ---------- F2：旧槽可开、新槽挡、不双计 ----------
    # 新槽 S-ETL-02 已被 RS-F1 租约占用：同槽后来窗预检应挡（可降级）。
    create_window("RS-F2-NEW", "ETL", "S-ETL-02", "E-PIPE-02")
    rd_new = window_of("RS-F2-NEW")["readiness"]
    assert not rd_new["ready"] and any("S-ETL-02" in x for x in rd_new["reasons"]), rd_new
    assert rd_new["degradable"], ("候补空闲应可降级", rd_new)
    # 旧槽 S-ETL-01 已释放：预检应放行，且能直接主槽开窗。
    create_window("RS-F2-OLD", "ETL", "S-ETL-01", "E-PIPE-03")
    rd_old = window_of("RS-F2-OLD")["readiness"]
    assert rd_old["ready"], ("旧槽可开性应恢复", rd_old)
    st, o2 = call("POST", "/leases/open", {"window_code": "RS-F2-OLD"})
    assert st == 200 and o2["ok"] and o2["slot_code"] == "S-ETL-01" and not o2["degraded"], o2
    d = assert_consistent("F2")
    assert d["open_leases"] == 2 == d["open_leases_actual"], ("水位不双计", d)
    print("F2 PASS: 旧槽可开、新槽预检挡后来者、水位净变迁不双计")

    # ---------- F3：失败回滚不留双键；交错半截 ----------
    c0 = dashboard()["conflict_count"]
    # 3a 目标槽被占 → 409 + 落冲突行；索引/展示/水位一概不动。
    st, f1 = call("POST", "/reschedule", {"window_code": "RS-F1", "new_slot": "S-ETL-01"})
    assert st == 409 and "占用" in str(f1.get("detail")), (st, f1)
    lease = lease_of("RS-F1")
    assert lease and lease["slot_code"] == "S-ETL-02", ("失败不得迁键", lease)
    assert window_of("RS-F1")["slot_code"] == "S-ETL-02", "失败不得动展示字段"
    assert len(open_leases()) == 2, "失败不得留双键"
    d = assert_consistent("F3a")
    assert d["conflict_count"] == c0 + 1, ("预检冲突应落行并净 +1", d)
    # 3b 目标槽不存在 → 409 + 落冲突行；状态不动。
    st, f2 = call("POST", "/reschedule", {"window_code": "RS-F1", "new_slot": "S-NOPE"})
    assert st == 409 and "不存在" in str(f2.get("detail")), (st, f2)
    assert lease_of("RS-F1")["slot_code"] == "S-ETL-02"
    d = assert_consistent("F3b")
    assert d["conflict_count"] == c0 + 2, d
    st, c2 = call("POST", "/leases/close", {"lease_no": o2["lease_no"]})
    assert st == 200, c2
    # 3c 交错半截：关窗先提交 → 改挂必须整体拒绝，不留任何痕迹。
    st, c1 = call("POST", "/leases/close", {"lease_no": lease_no})
    assert st == 200, c1
    st, f3 = call("POST", "/reschedule", {"window_code": "RS-F1", "new_slot": "S-ETL-01"})
    assert st == 409 and "占用态" in str(f3.get("detail")), (st, f3)
    assert lease_of("RS-F1") is None, "关窗后不得有 open 租约"
    assert window_of("RS-F1")["slot_code"] == "S-ETL-02", "关窗后展示冻结在最后索引态"
    d = assert_consistent("F3c")
    assert d["conflict_count"] == c0 + 2, ("状态错误不落冲突行", d)
    assert d["reschedule_state"] == "settled" and d["reschedule_ok"] is False, (
        "租约已关，回执应落 settled 而非宣称成功", d)
    # 3d 草稿窗（从未开窗）改挂 → 409。
    create_window("RS-F3-DRAFT", "ETL", "S-ETL-01", "E-PIPE-03")
    st, f4 = call("POST", "/reschedule", {"window_code": "RS-F3-DRAFT", "new_slot": "S-ETL-02"})
    assert st == 409, (st, f4)
    assert_consistent("F3d")
    print("F3 PASS: 忙槽/不存在槽 409 不留双键；关窗交错整体回滚；草稿窗拒绝")

    # ---------- F4：并发双改挂收敛 ----------
    create_window("RS-F4", "BI", "S-BI-01", "E-BI-01", s=13 * 60, e=14 * 60 + 30)
    st, o4 = call("POST", "/leases/open", {"window_code": "RS-F4"})
    assert st == 200 and o4["ok"] and o4["slot_code"] == "S-BI-01", o4
    no4 = o4["lease_no"]

    # 4a 双目标并发：S-BI-02 vs S-BI-SB1 —— 串行后先到者先落，后到者从现行键再迁。
    res = race([
        ("POST", "/reschedule", {"window_code": "RS-F4", "new_slot": "S-BI-02"}),
        ("POST", "/reschedule", {"window_code": "RS-F4", "new_slot": "S-BI-SB1"}),
    ])
    assert all(st == 200 for st, _ in res), ("双目标并发应都成功（串行迁移）", res)
    lease = lease_of("RS-F4")
    final = lease["slot_code"]
    assert final in ("S-BI-02", "S-BI-SB1"), lease
    assert window_of("RS-F4")["slot_code"] == final, "展示应收敛到最终索引键"
    occ = slot_occupants()
    assert occ[final] == [no4], occ
    other = "S-BI-SB1" if final == "S-BI-02" else "S-BI-02"
    assert not occ[other] and not occ["S-BI-01"], ("另一目标与旧键都必须已释放", occ)
    d = assert_consistent("F4a")
    assert d["reschedule_state"] == "replayed" and d["display_slot"] == final, d
    assert d["reschedule_last"]["new_slot"] == final, "回执必须收敛到索引现行键"
    print(f"F4a PASS: 双目标并发收敛到单一现行键 {final}，无双键无 5xx")

    # 4b 同目标并发：第二路幂等重放。
    res = race([
        ("POST", "/reschedule", {"window_code": "RS-F4", "new_slot": "S-BI-01"}),
        ("POST", "/reschedule", {"window_code": "RS-F4", "new_slot": "S-BI-01"}),
    ])
    assert all(st == 200 for st, _ in res), ("同目标并发应都成功", res)
    assert lease_of("RS-F4")["slot_code"] == "S-BI-01"
    assert window_of("RS-F4")["slot_code"] == "S-BI-01"
    d = assert_consistent("F4b")
    assert d["reschedule_state"] == "replayed" and d["display_slot"] == "S-BI-01", d
    print("F4b PASS: 同目标并发幂等收敛到 S-BI-01")

    # 4c 改挂 vs 关窗并发：两种串行序都合法，终态都必须一致。
    res = race([
        ("POST", "/reschedule", {"window_code": "RS-F4", "new_slot": "S-BI-02"}),
        ("POST", "/leases/close", {"lease_no": no4}),
    ])
    assert all(st in (200, 409) for st, _ in res), ("不得 5xx", res)
    r_resched, r_close = res[0], res[1]
    assert r_close[0] == 200, ("关窗必然成功（租约本在占用态）", res)
    closed = [r for r in call("GET", "/leases")[1] if r["lease_no"] == no4][0]
    assert closed["status"] == "closed", closed
    if r_resched[0] == 200:
        # 改挂先提交：关窗落在迁移后的键上。
        assert closed["slot_code"] == "S-BI-02", ("先迁后关，闭租约应在新键", closed)
        assert window_of("RS-F4")["slot_code"] == "S-BI-02"
        print("F4c PASS: 改挂先提交→关窗落新键，终态一致")
    else:
        # 关窗先提交：改挂整体拒绝，租约留在原键关闭。
        assert "占用态" in str(r_resched[1].get("detail")), res
        assert closed["slot_code"] == "S-BI-01", ("先关后迁被拒，闭租约应在旧键", closed)
        assert window_of("RS-F4")["slot_code"] == "S-BI-01"
        print("F4c PASS: 关窗先提交→改挂 409 整体回滚，终态一致")
    assert lease_of("RS-F4") is None
    assert_consistent("F4c")

    # ---------- F5：撕裂注入——总览必须亮撕裂，不得漂白 ----------
    if Path(DB_PATH).exists():
        create_window("RS-F5", "ETL", "S-ETL-01", "E-PIPE-04", day="2026-11-24")
        st, o5 = call("POST", "/leases/open", {"window_code": "RS-F5"})
        assert st == 200 and o5["ok"], o5
        st, r5 = call("POST", "/reschedule", {"window_code": "RS-F5", "new_slot": "S-ETL-02"})
        assert st == 200 and r5["ok"], r5
        true_receipt = json.dumps(dashboard()["reschedule_last"], ensure_ascii=False)

        # 注入假回执：宣称新槽 S-ETL-SB1，索引现行仍是 S-ETL-02。
        fake = dict(r5, new_slot="S-ETL-SB1")
        conn = sqlite3.connect(DB_PATH, timeout=15)
        try:
            conn.execute(
                "UPDATE settings SET value=? WHERE key='reschedule_last'",
                (json.dumps(fake, ensure_ascii=False),),
            )
            conn.commit()
        finally:
            conn.close()
        d = assert_consistent("F5-torn")
        assert d["reschedule_state"] == "torn" and d["reschedule_ok"] is False, (
            "回执新槽≠索引现行，总览必须亮撕裂", d)
        assert d["display_slot"] == "S-ETL-02", ("展示槽必须取索引现行键", d)
        print("F5 PASS: 撕裂注入下总览亮 torn、展示取索引现行键、水位互证不被漂白")

        # 还原真回执 → replayed；关窗 → settled。
        conn = sqlite3.connect(DB_PATH, timeout=15)
        try:
            conn.execute(
                "UPDATE settings SET value=? WHERE key='reschedule_last'", (true_receipt,)
            )
            conn.commit()
        finally:
            conn.close()
        d = dashboard()
        assert d["reschedule_state"] == "replayed" and d["reschedule_ok"] is True, d
        st, _ = call("POST", "/leases/close", {"lease_no": o5["lease_no"]})
        assert st == 200
        d = assert_consistent("F5-settled")
        assert d["reschedule_state"] == "settled" and d["reschedule_ok"] is False, d
        print("F5 PASS: 真回执重放一致；关窗后落 settled")
    else:
        print(f"F5 SKIP: 库文件不存在（{DB_PATH}），跳过撕裂注入夹具")

    close_all()
    d = assert_consistent("final")
    assert d["open_leases"] == 0, d
    print("ALL RESCHEDULE CHECKS PASSED")


if __name__ == "__main__":
    main()
