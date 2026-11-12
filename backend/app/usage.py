"""商品用途：转卖 / 自用（腌制、切配用料）。

同一种货两种用途都有（如五香粉：一部分卖给大统华，一部分腌肉用）时，建成两个关联商品：
「五香粉」（转卖，挂大统华 Item#）和「五香粉（自用）」，库存分开记。
- 买的时候：小票一行可以拆成“转卖多少 + 自用多少”；
- 买回来再决定：库存里任何一个 Lot 都可以“转用途”（记一笔过账，Lot 跟过去，留审计）。
"""
import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .categories import FOOD
from .core import audit
from .models import Lot, Sku, User

LABEL = {"resale": "转卖", "production": "自用"}
SUFFIX = "（自用）"


def default_usage(category: str) -> str:
    if category in ("原料肉", "原料菜"):
        return "production"
    if category in ("干货", "冻货"):
        return "resale"
    return ""


def normalize(v) -> str | None:
    v = str(v or "").strip().lower()
    m = {"resale": "resale", "转卖": "resale", "卖": "resale", "sell": "resale", "production": "production", "自用": "production",
         "生产": "production", "生产用": "production", "腌制": "production", "own": "production", "": ""}
    return m.get(v)


def twin(db: Session, u: User, sku: Sku, create: bool = True) -> Sku | None:
    """另一种用途的对应商品；没有就按本商品建一个（同单位、分类、换算），两边互相关联。"""
    if sku.twin_id:
        t = db.get(Sku, sku.twin_id)
        if t and t.active is not False:
            return t
    if not create:
        return None
    if sku.category not in FOOD:
        raise HTTPException(400, f"「{sku.name}」不是食品，没有转卖 / 自用之分")
    from .routers.batches import next_sku_code
    mine = sku.usage or "resale"
    other = "production" if mine == "resale" else "resale"
    base = sku.name[:-len(SUFFIX)] if sku.name.endswith(SUFFIX) else sku.name
    name = base + SUFFIX if other == "production" else base
    exist = db.query(Sku).filter(Sku.name == name, Sku.active.isnot(False), Sku.id != sku.id).first()
    if exist:
        t = exist
    else:
        if other == "resale" and name == sku.name:
            name = base + "（转卖）"
        t = Sku(code=next_sku_code(db), name=name, category=sku.category, base_unit=sku.base_unit, conversions=dict(sku.conversions or {}),
                catch_weight=sku.catch_weight, purchase_pack=sku.purchase_pack, usage=other)
        db.add(t)
        db.flush()
        audit(db, u, "sku.created", "sku", t.id, code=t.code, name=t.name, twin_of=sku.id)
    sku.usage = mine
    t.usage, t.twin_id, sku.twin_id = other, sku.id, t.id
    return t


def convert(db: Session, u: User, sku: Sku, wh_id: str, lot_id: str | None, qty: float, reason: str = "") -> dict:
    """把一部分库存转成另一种用途（转卖 ↔ 自用）。只能转可用的实物。"""
    from .routers.inventory import assert_usable, balance, begin_posting, reserved_on, txn
    if qty <= 0:
        raise HTTPException(400, "数量需大于 0")
    have = balance(db, sku.id, wh_id, "on_hand", lot_id) - reserved_on(db, sku.id, wh_id, lot_id)
    if qty > have + 1e-9:
        raise HTTPException(409, f"可转的只有 {have:g} {sku.base_unit}（已扣预留）")
    assert_usable(db, lot_id, "转用途")
    t = twin(db, u, sku)
    lot = db.get(Lot, lot_id) if lot_id else None
    tlot = None
    if lot:
        tlot = db.query(Lot).filter_by(sku_id=t.id, lot_code=lot.lot_code).first()
        if not tlot:
            tlot = Lot(sku_id=t.id, lot_code=lot.lot_code, expiry=lot.expiry, quality_status=lot.quality_status)
            db.add(tlot)
            db.flush()
    p = begin_posting(db, u, f"usage-{uuid.uuid4().hex}", "usage_convert", "sku", sku.id,
                      note=f"转用途：{sku.name} → {t.name} {qty:g} {sku.base_unit}" + (f"（{reason}）" if reason else ""))
    txn(db, p, sku.id, wh_id, "on_hand", -qty, lot_id)
    txn(db, p, t.id, wh_id, "on_hand", qty, tlot.id if tlot else None)
    audit(db, u, "sku.usage_convert", "sku", sku.id, to=t.id, qty=qty, lot=lot.lot_code if lot else None, reason=reason)
    return {"from": sku.name, "to": t.name, "to_id": t.id, "qty": qty, "posting_id": p.id}
