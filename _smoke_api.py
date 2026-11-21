# -*- coding: utf-8 -*-
"""0-1 API smoke against running server on 11100.

口径（写锁臂）：主槽冲突时按优先级在写锁内扫候补降级，直至候补耗尽才 409。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:11100/api"


def call(method: str, path: str, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read().decode()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"detail": raw}
        return e.code, payload


def main() -> None:
    st, dash = call("GET", "/dashboard")
    assert st == 200, dash
    print("dashboard", dash)
    assert dash["watermarks_consistent"] is True, dash

    st, open1 = call("POST", "/leases/open", {"window_code": "W-DIM-01"})
    assert st == 200 and open1["ok"], open1
    assert open1["slot_code"] == "S-ETL-01" and open1["degraded"] is False, open1
    print("open1(主槽)", open1)

    # 主槽冲突的批窗：默认允许候补降级，应成功并落候补槽。
    st, created = call(
        "POST",
        "/windows",
        {
            "code": "W-DIM-DUP",
            "title": "冲突试窗",
            "subject": "ETL",
            "slot_code": "S-ETL-01",
            "executor_code": "E-PIPE-02",
            "day": "2026-11-20",
            "start_min": 540,
            "end_min": 630,
        },
    )
    assert st == 200, created
    st, degraded = call("POST", "/leases/open", {"window_code": "W-DIM-DUP"})
    assert st == 200 and degraded["ok"] and degraded["degraded"] is True, degraded
    assert degraded["slot_code"].startswith("S-ETL-SB"), degraded
    assert degraded["main_slot_code"] == "S-ETL-01", degraded
    print("degraded(候补)", degraded)

    st, dash2 = call("GET", "/dashboard")
    assert dash2["open_leases"] == 2 and dash2["watermarks_consistent"] is True, dash2
    print("dashboard-after", dash2)

    st, closed1 = call("POST", "/leases/close", {"lease_no": open1["lease_no"]})
    assert st == 200, closed1
    st, closed2 = call("POST", "/leases/close", {"lease_no": degraded["lease_no"]})
    assert st == 200, closed2
    print("closed", closed1, closed2)

    st, dash3 = call("GET", "/dashboard")
    assert dash3["open_leases"] == 0 and dash3["watermarks_consistent"] is True, dash3
    print("SMOKE OK")


if __name__ == "__main__":
    main()
