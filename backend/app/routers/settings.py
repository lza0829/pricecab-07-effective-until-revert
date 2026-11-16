from fastapi import APIRouter
from pydantic import BaseModel

from app.db import connect
from app.repositories import settings as settings_repo
from app.services import apply_service

router = APIRouter(prefix="/settings", tags=["settings"])


class RevertStrategyIn(BaseModel):
    strategy: str


@router.get("")
def list_settings():
    conn = connect()
    try:
        return settings_repo.list_all(conn)
    finally:
        conn.close()


@router.put("/revert-strategy")
def set_revert_strategy(body: RevertStrategyIn):
    return apply_service.set_revert_strategy(body.strategy)
