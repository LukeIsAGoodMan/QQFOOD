"""今日摘要：每天早上一眼看完要处理的事。首页卡片、ChatGPT（get_daily_brief）、早上 7 点站内通知共用。

只读汇总，不改任何数据；每一项都带链接，点进去处理。
"""
import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from .core import MANAGER_ROLES, notify
from .models import (AppSetting, BatchProposal, Customer, CustomerSite, InventoryTxn, Lot, PurchaseOrder, SalesOrder, Site, Sku, Task,
                     User)

OPEN_SO = ("confirmed", "partial")
OPEN_PO = ("ordered", "partial")


def site_tz(db: Session) -> ZoneInfo:
    s = db.query(Site).first()
    return ZoneInfo(s.tz if s and s.tz else "America/Los_Angeles")


def build(db: Session, u: User) -> dict:
    from .agent import proof
    from .routers.inventory import stock
    tz = site_tz(db)
    now = datetime.now(tz)
    today, tomorrow = now.date().isoformat(), (now.date() + timedelta(days=1)).isoformat()
    custs = {c.id: c for c in db.query(Customer)}
    sites = {s.id: s for s in db.query(CustomerSite)}

    def so_row(o):
        return {"code": o.code, "po": o.po_no or "PO 待补", "customer": custs[o.customer_id].name if o.customer_id in custs else "",
                "site": sites[o.site_id].name if o.site_id in sites else "", "required_date": o.required_date,
                "status": {"confirmed": "待发货", "partial": "部分履约"}.get(o.status, o.status), "link": f"/orders?so={o.id}"}

    from . import fulfillment as ff
    from .models import SOLine
    open_so = [o for o in db.query(SalesOrder).filter(SalesOrder.status.in_(OPEN_SO)).order_by(SalesOrder.required_date)
               if any(ff.owes(l) and l.balance_status != "backordered" for l in db.query(SOLine).filter_by(order_id=o.id))]
    due = [so_row(o) for o in open_so if o.required_date in (today, tomorrow)]
    late = [so_row(o) for o in open_so if o.required_date and o.required_date < today]
    po_late = [{"code": p.code, "supplier": p.supplier, "expected_date": p.expected_date, "link": f"/purchasing?po={p.id}"}
               for p in db.query(PurchaseOrder).filter(PurchaseOrder.status.in_(OPEN_PO)) if p.expected_date and p.expected_date < today]
    low = [{"sku": r["sku_name"], "warehouse": r["warehouse"], "available": r["available"], "safety": r["safety_stock"], "unit": r["unit"],
            "link": f"/inventory?sku={r['sku_id']}"} for r in stock(None, u, db)["rows"] if r["below_safety"]]
    # 14 天内到期、还有实物的 Lot
    soon = (now.date() + timedelta(days=14)).isoformat()
    exp = []
    skus = {s.id: s for s in db.query(Sku)}
    for lot in db.query(Lot).filter(Lot.expiry.isnot(None), Lot.expiry <= soon):
        qty = sum(t.qty for t in db.query(InventoryTxn).filter_by(lot_id=lot.id, bucket="on_hand"))
        if qty > 1e-9:
            exp.append({"sku": skus[lot.sku_id].name if lot.sku_id in skus else "", "lot": lot.lot_code, "expiry": lot.expiry,
                        "qty": round(qty, 3), "expired": lot.expiry < today, "link": "/inventory"})
    exp.sort(key=lambda x: x["expiry"])
    pend = [{"code": b.code, "kind": b.kind, "source": b.source_note[:60], "rows": len(b.rows), "link": f"/ingest?batch={b.id}"}
            for b in db.query(BatchProposal).filter_by(status="proposed").order_by(BatchProposal.created_at)]
    miss = proof.missing(db, 30, 50)
    my_late = [{"title": t.title, "due_at": t.due_at, "link": f"/t/{t.id}"} for t in db.query(Task).filter(
        Task.owner_id == u.id, Task.status.in_(["todo", "doing", "blocked"]), Task.due_at.isnot(None)) if _aware(t.due_at) < now]

    sections = [
        {"key": "orders_due", "title": "今天 / 明天要交的单", "tone": "warn", "items": due},
        {"key": "orders_late", "title": "过了交货日、还有没处理的剩余", "tone": "bad", "items": late},
        {"key": "batches", "title": "ChatGPT 提交、等你确认", "tone": "warn", "items": pend},
        {"key": "low_stock", "title": "低于安全库存", "tone": "bad", "items": low},
        {"key": "expiring", "title": "14 天内到期的 Lot", "tone": "warn", "items": exp},
        {"key": "po_late", "title": "采购逾期未到", "tone": "warn", "items": po_late},
        {"key": "missing_proof", "title": "还缺原件（近 30 天）", "tone": "info",
         "items": [{"ref": m["ref"], "what": m["what"], "note": m["proof_note"], "link": m["url"]} for m in miss]},
        {"key": "my_tasks", "title": "我逾期的任务", "tone": "bad", "items": my_late},
    ]
    for s in sections:
        s["count"] = len(s["items"])
    return {"date": today, "generated_at": datetime.now(timezone.utc), "sections": sections,
            "headline": headline(sections)}


def headline(sections) -> str:
    c = {s["key"]: s["count"] for s in sections}
    parts = [f"{c['orders_due']} 张单今明要交" if c["orders_due"] else "", f"{c['orders_late']} 张单逾期" if c["orders_late"] else "",
             f"{c['batches']} 批待确认" if c["batches"] else "", f"{c['low_stock']} 项低库存" if c["low_stock"] else "",
             f"{c['expiring']} 个 Lot 临期" if c["expiring"] else "", f"{c['missing_proof']} 张缺原件" if c["missing_proof"] else ""]
    parts = [p for p in parts if p]
    return "；".join(parts) if parts else "今天没有需要特别处理的事"


def _aware(t):
    return t if t is None or t.tzinfo else t.replace(tzinfo=timezone.utc)


def send_morning(db: Session) -> int:
    """每天一次：给老板 / 主管 / 管理员发站内通知（以后接邮件、手机推送）。返回发了几个人。"""
    tz = site_tz(db)
    today = datetime.now(tz).date().isoformat()
    key = f"brief:{today}"
    if db.get(AppSetting, key):
        return 0
    n = 0
    for u in db.query(User).filter(User.active.is_(True), User.role.in_(MANAGER_ROLES)):
        b = build(db, u)
        notify(db, u.id, f"今日摘要 {today[5:]}：{b['headline']}", "/?brief=1", dedup_key=key)
        n += 1
    db.add(AppSetting(key=key, value={"sent": n}))
    db.commit()
    return n


async def morning_loop(hour: int = 7):
    """后台：每 10 分钟看一次，场地时间过了早上 7 点且今天还没发就发。"""
    from .db import SessionLocal
    while True:
        try:
            db = SessionLocal()
            try:
                if datetime.now(site_tz(db)).hour >= hour and db.query(User).count():
                    send_morning(db)
            finally:
                db.close()
        except Exception as e:  # 摘要失败不能影响系统
            print("morning brief failed:", e)
        await asyncio.sleep(600)
