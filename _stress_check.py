# -*- coding: utf-8 -*-
"""写锁臂压力自检：多轮高并发抢同一候补池，不得双开、不得 500、水位始终一致。"""
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


def close_all():
    _, rows = call("GET", "/leases")
    for r in rows:
        if r["status"] == "open":
            call("POST", "/leases/close", {"lease_no": r["lease_no"]})


def free_standbys(subject):
    _, slots = call("GET", "/slots")
    return [s["code"] for s in slots if s["is_standby"] and s["subject"] == subject and not s["occupied"]]


def main():
    day = "2026-11-22"
    S, E = 9 * 60, 10 * 60 + 30
    close_all()
    execs = [e["code"] for e in call("GET", "/executors")[1]]
    etl = [e for e in execs if e.startswith("E-PIPE")]
    bi = [e for e in execs if e.startswith("E-BI")]

    rounds = 12
    total_500 = 0
    for rnd in range(rounds):
        close_all()
        # 每轮占用主槽 S-ETL-02（用 E-PIPE-01），ETL 执行器 -1
        blk = f"STRESS-BLK-{rnd}"
        call("POST", "/windows", {"code": blk, "title": blk, "subject": "ETL",
                                  "slot_code": "S-ETL-02", "executor_code": "E-PIPE-01",
                                  "day": day, "start_min": S, "end_min": E})
        st, b = call("POST", "/leases/open", {"window_code": blk})
        assert st == 200 and b["ok"], (rnd, b)

        free = free_standbys("ETL")
        n = min(len(etl) - 1, len(free) + 3)  # 抢手比候补多
        codes = [f"STRESS-{rnd}-{i}" for i in range(n)]
        for i, c in enumerate(codes):
            call("POST", "/windows", {"code": c, "title": c, "subject": "ETL",
                                      "slot_code": "S-ETL-02", "executor_code": etl[i + 1],
                                      "day": day, "start_min": S, "end_min": E})

        results = []
        lock = threading.Lock()
        barrier = threading.Barrier(n)

        def worker(c):
            barrier.wait()
            st, p = call("POST", "/leases/open", {"window_code": c})
            with lock:
                results.append((c, st, p))

        ts = [threading.Thread(target=worker, args=(c,)) for c in codes]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

        oks = [p for _, st, p in results if st == 200]
        errs500 = [p for _, st, p in results if st >= 500 or st == 0]
        total_500 += len(errs500)
        # 成功数应等于空闲候补数（执行器各不相同，只有槽一个争用维度）
        assert len(oks) == len(free), (rnd, "成功数", len(oks), "free", len(free), results)
        used = sorted(p["slot_code"] for p in oks)
        assert used == sorted(free), (rnd, "候选使用", used, free)
        assert not errs500, (rnd, "出现 5xx", errs500)

        _, open_rows = call("GET", "/leases")
        opens = [r for r in open_rows if r["status"] == "open"]
        for i in range(len(opens)):
            for j in range(i + 1, len(opens)):
                a, b = opens[i], opens[j]
                if a["slot_code"] == b["slot_code"] and a["day"] == b["day"]:
                    assert not (a["start_min"] < b["end_min"] and b["start_min"] < a["end_min"]), \
                        (rnd, "双开同槽", a["lease_no"], b["lease_no"])
        d = call("GET", "/dashboard")[1]
        assert d["watermarks_consistent"] and d["open_leases"] == len(opens) == d["live_sessions"], (rnd, d)
        print(f"round {rnd:02d}: free={free} ways={n} ok={len(oks)} fail={n-len(oks)} -> consistent")

    close_all()
    d = call("GET", "/dashboard")[1]
    assert d["open_leases"] == 0 and d["watermarks_consistent"], d
    print(f"STRESS PASSED ({rounds} rounds, 5xx={total_500})")


if __name__ == "__main__":
    main()
