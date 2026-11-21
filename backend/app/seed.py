from __future__ import annotations

from datetime import datetime, timezone

from app.db import connect, init_db


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def seed_if_empty() -> None:
    init_db()
    conn = connect()
    try:
        n = conn.execute("SELECT COUNT(*) c FROM slots").fetchone()["c"]
        if n > 0:
            return
        # slots 表 = 算力槽；executors 表 = 执行器（库表名兼容底座，界面/题面用语为数仓皮）
        # 前四行是主槽；后两行是同主题候补槽（is_standby=1），standby_priority 小者优先。
        slots = [
            # code, name, subject, capacity, is_standby, standby_priority
            ("S-ETL-01", "ETL 主槽 A", "ETL", 80, 0, 0),
            ("S-ETL-02", "ETL 主槽 B", "ETL", 30, 0, 0),
            ("S-BI-01", "报表槽甲", "BI", 40, 0, 0),
            ("S-BI-02", "报表槽乙", "BI", 40, 0, 0),
            ("S-ETL-SB1", "ETL 候补槽 1", "ETL", 60, 1, 10),
            ("S-ETL-SB2", "ETL 候补槽 2", "ETL", 60, 1, 20),
            ("S-BI-SB1", "报表候补槽 1", "BI", 30, 1, 10),
        ]
        for code, name, subject, cap, is_sb, prio in slots:
            conn.execute(
                "INSERT INTO slots(code, name, subject, capacity, is_standby, standby_priority, active) "
                "VALUES(?,?,?,?,?,?,1)",
                (code, name, subject, cap, is_sb, prio),
            )
        executors = [
            ("E-PIPE-01", "管道执行器甲", "ETL"),
            ("E-PIPE-02", "管道执行器乙", "ETL"),
            ("E-PIPE-03", "管道执行器丙", "ETL"),
            ("E-PIPE-04", "管道执行器丁", "ETL"),
            ("E-BI-01", "报表执行器甲", "BI"),
            ("E-BI-02", "报表执行器乙", "BI"),
        ]
        for code, name, subject in executors:
            conn.execute(
                "INSERT INTO executors(code, name, subject, active) VALUES(?,?,?,1)",
                (code, name, subject),
            )
        day = "2026-11-20"
        windows = [
            ("W-DIM-01", "维表刷新窗", "ETL", "S-ETL-01", "E-PIPE-01", day, 9 * 60, 10 * 60 + 30),
            ("W-FACT-01", "事实入湖窗", "ETL", "S-ETL-02", "E-PIPE-02", day, 9 * 60, 10 * 60 + 30),
            ("W-RPT-01", "日报物化窗", "BI", "S-BI-01", "E-BI-01", day, 13 * 60, 14 * 60 + 30),
            ("W-RPT-02", "看板刷新窗", "BI", "S-BI-02", "E-BI-02", day, 13 * 60, 14 * 60 + 30),
        ]
        for row in windows:
            conn.execute(
                "INSERT INTO windows(code, title, subject, slot_code, executor_code, day, start_min, end_min, status) "
                "VALUES(?,?,?,?,?,?,?,?, 'draft')",
                row,
            )
        conn.execute(
            "INSERT INTO watermarks(key, value, updated_at) VALUES('open_leases', 0, ?)",
            (_now(),),
        )
        conn.execute(
            "INSERT INTO watermarks(key, value, updated_at) VALUES('conflict_count', 0, ?)",
            (_now(),),
        )
        # 本题默认允许候补降级占用，不落 conflict_mode 总开关。
        conn.execute(
            "INSERT INTO settings(key, value) VALUES('fixture_day', ?)",
            (day,),
        )
        conn.commit()
    finally:
        conn.close()
