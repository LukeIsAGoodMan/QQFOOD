"""今日摘要接口（首页卡片用）。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import brief as br
from ..core import current_user
from ..db import get_db
from ..models import User

router = APIRouter(prefix="/api", tags=["brief"])


@router.get("/brief")
def get_brief(u: User = Depends(current_user), db: Session = Depends(get_db)):
    return br.build(db, u)
