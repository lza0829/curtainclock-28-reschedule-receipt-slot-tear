from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from app.config import DB_PATH

# 架构口径（本仓库全局唯一贯彻）：**开窗写锁内一次扫落** —— 不设预占表。
# 所有占用类写入都走 transaction() 的 BEGIN IMMEDIATE 写锁；候补扫描、就绪复检、
# 正式 open 落库三件事发生在同一把写锁、同一个连接、同一个事务内。
# 因此并发两路同时抢同一候补时，后到者一定能看到先到者已提交的 open 租约，
# 只能改选下一个候补或失败，不会出现「双候补 open」。
SCHEMA = """
CREATE TABLE IF NOT EXISTS slots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  subject TEXT NOT NULL DEFAULT '',
  capacity INTEGER NOT NULL,
  is_standby INTEGER NOT NULL DEFAULT 0,
  standby_priority INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS executors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  subject TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS windows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  subject TEXT NOT NULL,
  slot_code TEXT NOT NULL,
  executor_code TEXT NOT NULL,
  day TEXT NOT NULL,
  start_min INTEGER NOT NULL,
  end_min INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft'
);
CREATE TABLE IF NOT EXISTS leases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lease_no TEXT NOT NULL UNIQUE,
  window_code TEXT NOT NULL,
  slot_code TEXT NOT NULL,
  main_slot_code TEXT,
  executor_code TEXT NOT NULL,
  day TEXT NOT NULL,
  start_min INTEGER NOT NULL,
  end_min INTEGER NOT NULL,
  degraded INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  opened_at TEXT NOT NULL,
  closed_at TEXT
);
CREATE TABLE IF NOT EXISTS conflicts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  window_code TEXT NOT NULL,
  resource_code TEXT NOT NULL,
  day TEXT NOT NULL,
  start_min INTEGER NOT NULL,
  end_min INTEGER NOT NULL,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watermarks (
  key TEXT PRIMARY KEY,
  value INTEGER NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_leases_status_day_slot
  ON leases(status, day, slot_code);
CREATE INDEX IF NOT EXISTS ix_leases_status_day_executor
  ON leases(status, day, executor_code);
-- 同一批窗至多一笔 open 租约（数据库层兜底，防止并发双开同窗）。
CREATE UNIQUE INDEX IF NOT EXISTS ux_leases_one_open_per_window
  ON leases(window_code) WHERE status = 'open';
-- 同一算力槽、同一天、时段重叠的 open 租约在插入时直接 ABORT。
-- 这是写锁臂的第二道防线：即使应用层扫描逻辑退化，数据库也不会留下双开。
CREATE TRIGGER IF NOT EXISTS trg_lease_slot_overlap
BEFORE INSERT ON leases
WHEN NEW.status = 'open'
BEGIN
  SELECT RAISE(ABORT, 'slot_overlap')
  WHERE EXISTS (
    SELECT 1 FROM leases
    WHERE status = 'open'
      AND slot_code = NEW.slot_code
      AND day = NEW.day
      AND NEW.start_min < end_min
      AND start_min < NEW.end_min
  );
END;
CREATE TRIGGER IF NOT EXISTS trg_lease_executor_overlap
BEFORE INSERT ON leases
WHEN NEW.status = 'open'
BEGIN
  SELECT RAISE(ABORT, 'executor_overlap')
  WHERE EXISTS (
    SELECT 1 FROM leases
    WHERE status = 'open'
      AND executor_code = NEW.executor_code
      AND day = NEW.day
      AND NEW.start_min < end_min
      AND start_min < NEW.end_min
  );
END;
"""

# 旧库升级：CREATE TABLE IF NOT EXISTS 不会补列，这里按需 ALTER。
MIGRATIONS = {
    "slots": [
        ("subject", "TEXT NOT NULL DEFAULT ''"),
        ("is_standby", "INTEGER NOT NULL DEFAULT 0"),
        ("standby_priority", "INTEGER NOT NULL DEFAULT 0"),
    ],
    "leases": [
        ("main_slot_code", "TEXT"),
        ("degraded", "INTEGER NOT NULL DEFAULT 0"),
    ],
}


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    # 写锁臂靠 BEGIN IMMEDIATE 串行化；busy_timeout 让后到者排队而不是立刻报错。
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


@contextmanager
def transaction():
    """写事务：BEGIN IMMEDIATE 在事务开始即拿到全局写锁。

    候补扫描与正式 open 落库必须都在这个上下文内（见 app.services.lease）。
    """
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, cols in MIGRATIONS.items():
        existing = _table_columns(conn, table)
        for name, decl in cols:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def init_db() -> None:
    conn = connect()
    try:
        # 先建表/索引/触发器，再补旧库缺列。
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()
