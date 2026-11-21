# -*- coding: utf-8 -*-
"""并发自检（写锁臂）：候补降级不得双 open，水位与实槽一致。

关键构造：让 N 路批窗主槽全部冲突、执行器各不相同（只余算力槽这一个争用维度），
而空闲候补槽少于 N 路。并发开窗后：
  - 成功笔数 == 空闲候补数（每路落不同候补）；
  - 失败笔数 == N - 候补数（后到者拿不到候补，失败可查）；
  - 任何槽/窗都不出现双 open；水位 == 实槽。
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:11200/api"


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


def create_window(code, subject, slot, executor, day, s, e):
    st, out = call("POST", "/windows", {
        "code": code, "title": code, "subject": subject,
        "slot_code": slot, "executor_code": executor,
        "day": day, "start_min": s, "end_min": e,
    })
    assert st == 200, (code, st, out)
    return out


def race(codes):
    results, lock = [], threading.Lock()
    barrier = threading.Barrier(len(codes))

    def worker(code):
        barrier.wait()
        st, out = call("POST", "/leases/open", {"window_code": code})
        with lock:
            results.append((code, st, out))

    ts = [threading.Thread(target=worker, args=(c,)) for c in codes]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return results


def open_leases():
    st, rows = call("GET", "/leases")
    assert st == 200
    return [r for r in rows if r["status"] == "open"]


def free_standby_codes(subject):
    st, slots = call("GET", "/slots")
    assert st == 200
    return [s["code"] for s in slots if s["is_standby"] and s["subject"] == subject and not s["occupied"]]


def assert_no_double_open(label):
    rows = open_leases()
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = rows[i], rows[j]
            if a["slot_code"] == b["slot_code"] and a["day"] == b["day"]:
                if a["start_min"] < b["end_min"] and b["start_min"] < a["end_min"]:
                    raise AssertionError(
                        f"[{label}] 双开同槽: {a['lease_no']}@{a['slot_code']} 与 {b['lease_no']}@{b['slot_code']}"
                    )
            if a["window_code"] == b["window_code"]:
                raise AssertionError(f"[{label}] 同窗双开: {a['window_code']}")
    return rows


def assert_watermarks(label):
    st, d = call("GET", "/dashboard")
    assert st == 200
    rows = open_leases()
    assert d["open_leases"] == len(rows) == d["live_sessions"], (label, d, len(rows))
    assert d["watermarks_consistent"] is True, (label, d)
    return d


def close_all():
    for r in open_leases():
        call("POST", "/leases/close", {"lease_no": r["lease_no"]})


def main():
    day = "2026-11-20"
    S, E = 9 * 60, 10 * 60 + 30
    close_all()

    execs = [e["code"] for e in call("GET", "/executors")[1]]
    etl_execs = [e for e in execs if e.startswith("E-PIPE")]

    # 占住主槽 S-ETL-01
    create_window("T-BLK", "ETL", "S-ETL-01", etl_execs[0], day, S, E)
    st, blk = call("POST", "/leases/open", {"window_code": "T-BLK"})
    assert st == 200 and blk["ok"] and blk["slot_code"] == "S-ETL-01", blk

    free = free_standby_codes("ETL")
    spare_execs = [e for e in etl_execs if e != etl_execs[0]]  # 排除占位窗占用的执行器
    # 路数 = 空闲候补数 + 1（保证至少有一路拿不到候补，可查失败）
    n_ways = min(len(free) + 1, len(spare_execs))
    assert n_ways > len(free), f"执行器不足：spare={spare_execs}，无法构造失败路"

    codes = []
    for i in range(n_ways):
        code = f"T-DEG-{i}"
        create_window(code, "ETL", "S-ETL-01", spare_execs[i], day, S, E)
        codes.append(code)

    res = race(codes)
    print("free standbys:", free)
    print("race ->", [(c, st, (p.get('slot_code') or p.get('detail'))) for c, st, p in res])
    oks = [p for _, st, p in res if st == 200]
    fails = [(c, p) for c, st, p in res if st != 200]

    rows = assert_no_double_open("degrade-race")
    d = assert_watermarks("degrade-race")

    assert len(oks) == len(free), (f"成功数应等于空闲候补数 {len(free)}", res)
    assert len(fails) == n_ways - len(free), ("失败数不符", res)
    used = sorted(p["slot_code"] for p in oks)
    assert used == sorted(free), ("成功者应恰好用尽各空闲候补且互不相同", used, free)
    assert all(p.get("degraded") for p in oks), ("成功者都应为降级", oks)
    for c, p in fails:
        assert "候补" in p.get("detail", "") or "冲突" in p.get("detail", ""), ("失败应可查原因", c, p)
    print(f"PASS: {len(oks)} 路各落不同候补；{len(fails)} 路拿不到候补→可查失败；无双重占")

    # 同窗同刻并发：至多一笔 open
    bi_exec = [e for e in execs if e.startswith("E-BI")]
    create_window("T-SAME", "BI", "S-BI-01", bi_exec[0], "2026-11-21", 8 * 60, 9 * 60)
    res2 = race(["T-SAME"] * 6)
    oks2 = [r for r in res2 if r[1] == 200]
    assert len(oks2) == 1, ("同窗并发应至多一笔成功", res2)
    assert_no_double_open("same-window")
    assert_watermarks("same-window")
    print(f"PASS: 同窗 6 路同刻并发，仅 {len(oks2)} 笔 open")

    close_all()
    d = call("GET", "/dashboard")[1]
    assert d["open_leases"] == 0 and d["live_sessions"] == 0 and d["watermarks_consistent"], d
    print("PASS: 终态水位归零且一致", d)
    print("ALL CONCURRENCY CHECKS PASSED")


if __name__ == "__main__":
    main()
