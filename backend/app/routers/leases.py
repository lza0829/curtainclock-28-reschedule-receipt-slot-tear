from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import connect, transaction
from app.services import lease as lease_svc

router = APIRouter(tags=["leases"])


class OpenBody(BaseModel):
    window_code: str


class CloseBody(BaseModel):
    lease_no: str


@router.get("/leases")
def list_leases():
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM leases ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/conflicts")
def list_conflicts():
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM conflicts ORDER BY id DESC LIMIT 100"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/leases/open")
def open_lease(body: OpenBody):
    # open_lease 必须在 transaction()（BEGIN IMMEDIATE 写锁）内执行：
    # 就绪复检、候补扫描、正式 open 落库同锁同事务，锁外不先读后写。
    try:
        with transaction() as conn:
            result = lease_svc.open_lease(conn, body.window_code)
        if result.get("ok") is False:
            raise HTTPException(409, result.get("error") or "开窗冲突")
        return result
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/leases/close")
def close_lease(body: CloseBody):
    try:
        with transaction() as conn:
            return lease_svc.close_lease(conn, body.lease_no)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e


class RescheduleBody(BaseModel):
    window_code: str
    new_slot: str


@router.post("/reschedule")
def reschedule(body: RescheduleBody):
    # 与 open_lease 同一口径：预检→迁移→展示字段→回执都在 transaction()
    # （BEGIN IMMEDIATE 写锁）内一次落；冲突失败落冲突行后整体提交，其余失败回滚。
    try:
        with transaction() as conn:
            result = lease_svc.reschedule_lease(conn, body.window_code, body.new_slot)
        if result.get("ok") is False:
            raise HTTPException(409, result.get("error") or "改挂冲突")
        return result
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
