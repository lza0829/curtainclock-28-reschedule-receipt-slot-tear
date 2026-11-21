from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import connect, transaction
from app.services.ready import readiness

router = APIRouter(tags=["windows"])


class SessionIn(BaseModel):
    code: str = Field(min_length=2)
    title: str
    subject: str
    slot_code: str
    executor_code: str
    day: str
    start_min: int
    end_min: int


@router.get("/windows")
def list_windows():
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM windows ORDER BY day, start_min, code"
        ).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            item["readiness"] = readiness(conn, r)
            out.append(item)
        return out
    finally:
        conn.close()


@router.post("/windows")
def create_session(body: SessionIn):
    if body.end_min <= body.start_min:
        raise HTTPException(400, "结束时刻必须晚于开始时刻")
    try:
        with transaction() as conn:
            conn.execute(
                "INSERT INTO windows(code, title, subject, slot_code, executor_code, day, start_min, end_min, status) "
                "VALUES(?,?,?,?,?,?,?,?, 'draft')",
                (
                    body.code,
                    body.title,
                    body.subject,
                    body.slot_code,
                    body.executor_code,
                    body.day,
                    body.start_min,
                    body.end_min,
                ),
            )
            row = conn.execute(
                "SELECT * FROM windows WHERE code=?", (body.code,)
            ).fetchone()
            return {**dict(row), "readiness": readiness(conn, row)}
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@router.get("/windows/{code}/ready")
def session_ready(code: str):
    conn = connect()
    try:
        row = conn.execute(
            "SELECT * FROM windows WHERE code=?", (code,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, "批窗不存在")
        return readiness(conn, row)
    finally:
        conn.close()
