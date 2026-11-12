"""Demo seed. Business facts from PRD 附录A are seeded as *待核验 tasks*, never as completed facts.
All SKUs/quantities are 示例数据 and must be replaced by the verified opening count before go-live."""
import copy
import os
import uuid

from .core import hash_secret, encrypt_secret
from .db import SessionLocal
from .models import (AccountEntry, Contact, FieldDefinition, NotebookNote, Page, PageVersion, RecordType, SafetyStock,
                     Site, Sku, Task, Transfer, TransferLine, User, Warehouse, BusinessRecord, Lot, AppSetting, POLine, PurchaseOrder,
                     Quote, Requisition, RequisitionVersion, Customer, CustomerSite, gid)
from .routers.pages import normalize_layout

DEMO_PASSWORD = os.environ.get("QQ_DEMO_PASSWORD", "qq597demo")
DEMO_PIN = os.environ.get("QQ_DEMO_PIN", "1234")

USERS = [
    ("boss", "老板", "boss"), ("ops", "运营主管 Amy", "ops_manager"), ("buyer", "采购 Ben", "purchaser"),
    ("wh", "仓管 Carlos", "warehouse"), ("prod", "生产 David", "production"), ("qa", "质量 Elena", "quality"),
    ("hrfin", "人事财务 Fiona", "hr_finance"), ("staff1", "员工 Gabriel", "staff"), ("staff2", "员工 Hana", "staff"),
    ("pageadmin", "页面管理员", "page_admin"), ("admin", "系统管理员", "sys_admin"),
]


def m(type_, title, width="full", **config):
    return {"id": gid("mod"), "type": type_, "title": title, "width": width, "hidden": False, "version": 1,
            "config": config}


def PROCUREMENT_MODULES():
    return [
        m("metric", "", "third", metric="po_pending_approval"), m("metric", "", "third", metric="po_overdue"),
        m("metric", "", "third", metric="receipts_pending"),
        m("purchasing", "采购单与收货"),
        m("requisitions", "需求与报价对比"),
    ]


def ORDERS_MODULES():
    return [
        m("metric", "", "third", metric="orders_due_soon"), m("metric", "", "third", metric="orders_overdue"),
        m("metric", "", "third", metric="orders_short"),
        m("orders", "客户订单与发货"),
        m("boxes", "称重打标签"),
    ]


def ensure_tnt(db):
    """大统华（T&T Supermarket US Inc.）与 Chino Hills 送货点。资料来自用户提供的 PO。"""
    c = db.query(Customer).filter_by(code="TNT").first()
    if c:
        return c
    c = Customer(code="TNT", name="大统华 T&T Supermarket US Inc.", vendor_no="10325", invoice_email="wfa.invoice@tntsupermarket.us",
                 notes="系统 PO（9 位数字单号，按箱，有 Item#）与中央厨房手写 PO（单号如 CH-CK，按磅）两种格式。超订量、质量不良或过期送货会被拒收；送货必须出示 PO。")
    db.add(c)
    db.flush()
    db.add(CustomerSite(customer_id=c.id, name="Chino Hills Store - Kitchen Deli / Central Kitchen", store_no="006",
                        address="13021 Peyton Dr, Chino Hills, CA 91709", dept="Kitchen Deli / Central Kitchen Department"))
    return c


def upgrade_aliases():
    """一次性：加 Supreme Logistics 外库（在本系统管账）。已有名字含 Supreme 的仓库则不重复加。"""
    db = SessionLocal()
    try:
        if not db.query(User).count() or db.get(AppSetting, "migration:aliases_v1"):
            return
        if not any("supreme" in (w.code + w.name).lower() for w in db.query(Warehouse)):
            db.add(Warehouse(code="SUPREME", name="Supreme 外库", kind="external", tracked=True))
        db.add(AppSetting(key="migration:aliases_v1", value={"done": True}))
        db.commit()
    finally:
        db.close()


def upgrade_categories():
    """一次性：库存分类换成 干货/冻货/原料肉/原料菜/生产用品/公司用品/生产机器。旧分类按品名猜，猜不出来 → 未分类（库存页一点补上）。"""
    from .categories import ALL, guess
    db = SessionLocal()
    try:
        if not db.query(User).count() or db.get(AppSetting, "migration:categories_v1"):
            return
        moved = {}
        for s in db.query(Sku):
            if s.category not in ALL:
                new = guess(s.name, s.category)
                moved[s.code] = [s.category, new]
                s.category = new
        db.add(AppSetting(key="migration:categories_v1", value={"done": True, "moved": moved}))
        db.commit()
    finally:
        db.close()


def upgrade_orders():
    """一次性：已上线库加大统华客户、订单页换成订单模块（旧模块隐藏）、老板总览加订单指标、生产页加称重打标签。"""
    db = SessionLocal()
    try:
        if not db.query(User).count() or db.get(AppSetting, "migration:orders_v1"):
            return
        ensure_tnt(db)
        actor = db.query(User).filter_by(role="sys_admin").first() or db.query(User).first()

        def republish(p, modules, note):
            layout = normalize_layout({"modules": modules})
            n = (max([v.version_no for v in db.query(PageVersion).filter_by(page_id=p.id)] or [0])) + 1
            v = PageVersion(page_id=p.id, version_no=n, layout=layout, note=note, published_by=actor.id)
            db.add(v)
            db.flush()
            p.published_version_id, p.draft, p.draft_rev = v.id, copy.deepcopy(layout), p.draft_rev + 1

        def current(p):
            v = db.get(PageVersion, p.published_version_id) if p.published_version_id else None
            return list(v.layout["modules"]) if v else []

        for p in db.query(Page).filter_by(name="订单与配送"):
            republish(p, ORDERS_MODULES() + [{**x, "hidden": True} for x in current(p)], "系统升级：客户订单与发货（原通用订单台账已隐藏，可恢复或回滚）")
        for p in db.query(Page).filter_by(name="老板总览"):
            mods = current(p)
            have = {x.get("config", {}).get("metric") for x in mods}
            add = [m("metric", "", "third", metric=k) for k in ("orders_due_soon", "orders_overdue", "orders_short") if k not in have]
            last = max([i for i, x in enumerate(mods) if x["type"] == "metric"] or [-1])
            republish(p, mods[:last + 1] + add + mods[last + 1:], "系统升级：加入订单指标")
        for p in db.query(Page).filter_by(name="生产"):
            mods = current(p)
            if not any(x["type"] == "boxes" for x in mods):
                i = next((k for k, x in enumerate(mods) if x["type"] == "production"), -1)
                mods = [{**x, "title": "切配生产"} if x["type"] == "production" else x for x in mods]
                republish(p, mods[:i + 1] + [m("boxes", "补打标签 / 单独称重")] + mods[i + 1:], "系统升级：切配生产（备料、损耗）与称重打标签")
        db.add(AppSetting(key="migration:orders_v1", value={"done": True}))
        db.commit()
    finally:
        db.close()


def canada_sources():
    return [Warehouse(code="TOR", name="多伦多（公司）", kind="company", tracked=False),
            Warehouse(code="VAN", name="温哥华（公司）", kind="company", tracked=False)]


def upgrade_sources():
    """一次性：已上线的库补上多伦多、温哥华两个来源地（不在本系统管账）。"""
    db = SessionLocal()
    try:
        if not db.query(User).count() or db.get(AppSetting, "migration:canada_sources_v1"):
            return
        have = {w.code for w in db.query(Warehouse)}
        db.add_all([w for w in canada_sources() if w.code not in have])
        db.add(AppSetting(key="migration:canada_sources_v1", value={"done": True}))
        db.add(AppSetting(key="migration:orders_v1", value={"done": True}))
        ensure_tnt(db)
        db.commit()
    finally:
        db.close()


def upgrade_pages():
    """一次性升级已上线库的页面：采购页换成采购闭环模块（旧模块隐藏保留），老板总览加采购指标。以新页面版本发布，可回滚。"""
    db = SessionLocal()
    try:
        if not db.query(User).count() or db.get(AppSetting, "migration:procurement_v1"):
            return
        actor = db.query(User).filter_by(role="ops_manager").first() or db.query(User).first()

        def republish(p, modules, note):
            cur = db.get(PageVersion, p.published_version_id) if p.published_version_id else None
            layout = normalize_layout({"modules": modules})
            n = (max([v.version_no for v in db.query(PageVersion).filter_by(page_id=p.id)] or [0])) + 1
            v = PageVersion(page_id=p.id, version_no=n, layout=layout, note=note, published_by=actor.id)
            db.add(v)
            db.flush()
            p.published_version_id, p.draft, p.draft_rev = v.id, copy.deepcopy(layout), p.draft_rev + 1
            return cur

        for p in db.query(Page).filter_by(name="采购"):
            cur = db.get(PageVersion, p.published_version_id) if p.published_version_id else None
            old = [{**x, "hidden": True} for x in (cur.layout["modules"] if cur else [])]
            republish(p, PROCUREMENT_MODULES() + old, "系统升级：采购到收货闭环（原通用采购台账已隐藏，可在编辑器中重新显示或回滚）")
        for p in db.query(Page).filter_by(name="老板总览"):
            cur = db.get(PageVersion, p.published_version_id) if p.published_version_id else None
            mods = list(cur.layout["modules"]) if cur else []
            have = {x.get("config", {}).get("metric") for x in mods}
            add = [m("metric", "", "third", metric=k) for k in ("po_pending_approval", "po_overdue", "receipts_pending") if k not in have]
            last_metric = max([i for i, x in enumerate(mods) if x["type"] == "metric"] or [-1])
            republish(p, mods[:last_metric + 1] + add + mods[last_metric + 1:], "系统升级：加入采购指标")
        if not db.get(AppSetting, "purchasing"):
            db.add(AppSetting(key="purchasing", value={"approval_limit": 2000}))
        db.add(AppSetting(key="migration:procurement_v1", value={"done": True}))
        db.commit()
    finally:
        db.close()


def seed_if_empty():
    db = SessionLocal()
    try:
        if db.query(User).count():
            return
        site = Site(code="597", name="597 Monterey Pass Rd", tz="America/Los_Angeles")
        db.add(site)
        db.flush()
        users = {}
        for un, name, role in USERS:
            lang = "es" if un == "staff1" else "zh"
            u = User(username=un, display_name=name, role=role, password_hash=hash_secret(DEMO_PASSWORD), lang=lang,
                     pin_hash=hash_secret(DEMO_PIN) if role in ("staff", "warehouse", "production") else None)
            db.add(u)
            db.flush()
            users[un] = u
        boss, ops = users["boss"], users["ops"]

        # ---- inventory master (示例) ----
        wh597 = Warehouse(code="WH597", name="597 主场地", kind="main", site_id=site.id)
        ext = Warehouse(code="EXT1", name="外库 1（示例）", kind="external")
        db.add_all([wh597, ext] + canada_sources())
        skus = {
            "RM-BEEF": Sku(code="RM-BEEF", name="示例 冷冻牛肉卷", category="原料肉", base_unit="箱", conversions={"磅": 20}),
            "RM-SPICE": Sku(code="RM-SPICE", name="示例 香料包", category="干货", base_unit="箱", conversions={"包": 50}),
            "PK-TRAY": Sku(code="PK-TRAY", name="示例 包装托盘", category="生产用品", base_unit="箱"),
            "FG-SOUP": Sku(code="FG-SOUP", name="示例 成品汤料", category="干货", base_unit="箱"),
            "FG-CHK-DICE": Sku(code="FG-CHK-DICE", name="示例 切粒鸡丁", category="冻货", base_unit="磅", catch_weight=True, ship_pack=50),
            "FG-PORK-DICE": Sku(code="FG-PORK-DICE", name="示例 切粒咕咾肉", category="冻货", base_unit="磅", catch_weight=True, ship_pack=50),
            "RM-CHK-LEG": Sku(code="RM-CHK-LEG", name="示例 鸡腿肉（原料）", category="原料肉", base_unit="磅", catch_weight=True, purchase_pack=40),
            "RM-PORK-BUTT": Sku(code="RM-PORK-BUTT", name="示例 梅头肉（原料）", category="原料肉", base_unit="磅", catch_weight=True, purchase_pack=65),
        }
        db.add_all(skus.values())
        db.flush()
        from .models import Recipe
        db.add(Recipe(output_sku_id=skus["FG-CHK-DICE"].id, lines=[{"sku_id": skus["RM-CHK-LEG"].id, "per_output": round(1 / 0.95, 6)}], note="示例：出成率 95%"))
        db.add(Recipe(output_sku_id=skus["FG-PORK-DICE"].id, lines=[{"sku_id": skus["RM-PORK-BUTT"].id, "per_output": round(1 / 0.95, 6)}], note="示例：出成率 95%"))
        db.add(SafetyStock(sku_id=skus["RM-BEEF"].id, warehouse_id=wh597.id, qty=30))
        db.add(SafetyStock(sku_id=skus["FG-SOUP"].id, warehouse_id=wh597.id, qty=30))
        db.flush()

        from .routers.inventory import ReceiveIn, receive
        for body in [
            ReceiveIn(idempotency_key=str(uuid.uuid4()), sku_id=skus["RM-BEEF"].id, warehouse_id=ext.id, qty=100,
                      lot_code="B2410", counted=True, note="示例期初（外库报表，需核验）"),
            ReceiveIn(idempotency_key=str(uuid.uuid4()), sku_id=skus["RM-BEEF"].id, warehouse_id=wh597.id, qty=20,
                      lot_code="B2410", counted=False, note="示例：到货未核数"),
            ReceiveIn(idempotency_key=str(uuid.uuid4()), sku_id=skus["RM-SPICE"].id, warehouse_id=wh597.id, qty=40,
                      lot_code="S001", counted=True, note="示例期初"),
        ]:
            db.commit()
            receive(body, users["wh"], db)
        tr = Transfer(code="TR-DEMO-001", from_wh=ext.id, to_wh=wh597.id, expected_date=None,
                      note="示例：外库调拨到 597", created_by=users["wh"].id)
        db.add(tr)
        db.flush()
        db.add(TransferLine(transfer_id=tr.id, sku_id=skus["RM-BEEF"].id,
                            lot_id=db.query(Lot).filter_by(lot_code="B2410").first().id,
                            qty_requested=50))

        # ---- record types ----
        def rtype(name, statuses, fields, rules=None, desc=""):
            rt = RecordType(name=name, statuses=statuses, evidence_rules=rules or {}, description=desc)
            db.add(rt)
            db.flush()
            ids = {}
            for i, (label, ftype, *opt) in enumerate(fields):
                f = FieldDefinition(record_type_id=rt.id, label=label, ftype=ftype, options=opt[0] if opt else [],
                                    sort_order=i * 10)
                db.add(f)
                db.flush()
                ids[label] = f.id
            return rt, ids

        po_rt, po_f = rtype("采购单", ["询价", "已下单", "已发货", "已收货", "已验收"], [
            ("供应商", "text"), ("类别", "select", ["设备", "耗材", "原料包材"]), ("PO号", "text"),
            ("金额", "money"), ("预计到货", "date"), ("需求版本", "text")],
            {"已收货": {"purposes": ["签收单"]}, "已验收": {"purposes": ["验收表", "安装后照片"]}},
            "设备/耗材/原料包材采购；报价版本与需求版本关联")
        eq_rt, eq_f = rtype("设备与建设", ["待发货", "已发货", "已收货", "已安装", "试用中", "已验收"], [
            ("设备/项目", "text"), ("位置", "text"), ("供应商", "text"), ("安装人", "text"), ("预计日期", "date")],
            {"已收货": {"purposes": ["签收依据"]}, "已安装": {"purposes": ["安装后照片"]},
             "已验收": {"purposes": ["验收表", "安装后照片"], "need_approved": True}},
            "发货、收货、安装、验收分别留证")
        od_rt, od_f = rtype("客户订单", ["已接收", "拣货中", "已发运", "已签收", "有差异"], [
            ("客户", "text"), ("PO号", "text"), ("交货日期", "date"), ("箱数", "number"), ("补货PO", "text")],
            {"已签收": {"purposes": ["POD"]}}, "原PO与补货PO关联；部分配送与差异单列")

        db.add(BusinessRecord(record_type_id=eq_rt.id, title="研磨机", status="已发货", owner_id=ops.id,
                              data={eq_f["设备/项目"]: "研磨机", eq_f["位置"]: "597 生产区"}, created_by=ops.id))
        # ---- 采购示例：需求 v1(5%) → v2(15%)，v2 两家报价；另一张已下单采购单（示例数据）----
        buyer = users["buyer"]
        rq = Requisition(code="RQ-DEMO-001", title="原料包材询价（示例）", category="原料包材", owner_id=buyer.id, created_by=buyer.id)
        db.add(rq)
        db.flush()
        base = [{"key": "L1", "sku_id": skus["PK-TRAY"].id, "item_name": "示例 包装托盘", "base_qty": 100, "unit": "箱"},
                {"key": "L2", "sku_id": skus["RM-SPICE"].id, "item_name": "示例 香料包", "base_qty": 40, "unit": "箱"}]
        v1 = RequisitionVersion(requisition_id=rq.id, version_no=1, margin_pct=5, lines=base, note="首次询价按 5% 余量", created_by=buyer.id)
        db.add(v1)
        db.flush()
        v2 = RequisitionVersion(requisition_id=rq.id, version_no=2, margin_pct=15, lines=base, note="后续计划按 15% 余量", created_by=buyer.id)
        db.add(v2)
        db.flush()
        rq.current_version_id = v2.id
        db.add(Quote(requisition_id=rq.id, version_id=v1.id, supplier="示例供应商 A", prices={"L1": 11.5, "L2": 38}, freight=60,
                     lead_time_days=10, note="按 v1 报价", created_by=buyer.id))
        db.add(Quote(requisition_id=rq.id, version_id=v2.id, supplier="示例供应商 A", prices={"L1": 11.5, "L2": 38}, freight=60,
                     tax=0, lead_time_days=10, created_by=buyer.id))
        db.add(Quote(requisition_id=rq.id, version_id=v2.id, supplier="示例供应商 B", prices={"L1": 10.8, "L2": 41}, freight=120,
                     lead_time_days=6, created_by=buyer.id))
        po = PurchaseOrder(code="PO-DEMO-001", supplier="示例供应商 C", category="原料包材", warehouse_id=wh597.id, status="ordered",
                           expected_date=None, freight=45, owner_id=buyer.id, created_by=buyer.id, note="示例：已下单待到货",
                           approval={"required": False, "limit": 2000, "total": 1045})
        db.add(po)
        db.flush()
        db.add(POLine(po_id=po.id, sku_id=skus["RM-BEEF"].id, item_name="示例 冷冻牛肉卷", unit="箱", qty_ordered=20, unit_price=50, sort=0))
        db.add(AppSetting(key="purchasing", value={"approval_limit": 2000}))
        db.add(AppSetting(key="migration:procurement_v1", value={"done": True}))
        db.add(AppSetting(key="migration:canada_sources_v1", value={"done": True}))

        # ---- 初始化待核验事项（PRD 附录A）----
        init_tasks = [
            ("研磨机：现场收货、安装、试用分别留证", "已有发货信息；收货/安装/试用需各自凭证", True, "todo"),
            ("CA Number：补交 EIN 文件", "", True, "todo"),
            ("Penske：账户已批准 ≠ 车辆已落实，确认车源", "", False, "todo"),
            ("Velocity 信用申请：确认状态", "", False, "todo"),
            ("10 板到货：核验 524 箱与 Lot", "用户已报告到货；箱数与 Lot 待核验；外库计划日期与到货记录差异保留对账", True, "review"),
            ("干柜装货清单 vs 597 实物：核实 2603 地点、单位与数量差异", "装货清单不等于实物库存", True, "todo"),
            ("采购需求版本：询价 5% 余量 → 计划 15%，保存需求版本", "", False, "doing"),
            ("Ware 清运：确认月费 184.64 美元、现场清运、旧桶移除、自动扣款", "早期简报 175 美元为历史口径", True, "todo"),
        ]
        for title, desc, ev, st in init_tasks:
            db.add(Task(title=title, description=desc + ("\n（初始化事项：需业务负责人核验）" if desc else "（初始化事项：需业务负责人核验）"),
                        owner_id=ops.id, requires_evidence=ev, status=st, source="init", created_by=boss.id,
                        site_id=site.id))

        # ---- notebook ----
        for name, company, cat, note in [
            ("Wayne", "", "外库物流", "调拨/交期沟通（联系方式待核验后补充）"),
            ("Ware", "", "垃圾/pest control", "清运服务；月费以最终确认 184.64 为准"),
            ("Velocity", "", "人事财务", "信用申请相关（待核验）"),
        ]:
            db.add(Contact(name=name, company=company, category=cat, notes=note, internal_owner_id=ops.id))
        db.add(AccountEntry(service="示例：租车供应商门户", category="租车", url="https://example.com", username="ops@example.com",
                            purpose="演示共享账号；正式密码请在上线后录入", owner_id=ops.id, mfa_info="验证码发到运营主管手机",
                            password_enc=encrypt_secret("demo-only-123"), share_roles=["boss", "ops_manager", "purchaser"],
                            created_by=ops.id))
        db.add(NotebookNote(title="597 上线前确认事项", category="初始化", created_by=ops.id,
                            body="- 每个领域负责人及替补\n- 商品与单位主档、期初实盘\n- 订单欠货、有效配方与 SOP\n- 质量放行权限、凭证必填清单\n"
                                 "- 资料保留政策、员工访问范围\n- 会计系统（QuickBooks Online 还是 Desktop）、打卡设备\n- 现场员工语言构成（中/英/西）"))

        # ---- pages ----
        def page(name, icon, group, order, layout, roles=None, desc=""):
            p = Page(name=name, icon=icon, nav_group=group, sort_order=order, description=desc,
                     visible_roles=roles or [], owner_id=ops.id, site_id=site.id, created_by=ops.id)
            p.draft = normalize_layout(layout)
            db.add(p)
            db.flush()
            v = PageVersion(page_id=p.id, version_no=1, layout=copy.deepcopy(p.draft), note="初始模板",
                            published_by=ops.id)
            db.add(v)
            db.flush()
            p.published_version_id = v.id
            return p

        mgmt = ["boss", "ops_manager", "sys_admin", "page_admin", "quality", "purchaser", "hr_finance"]
        page("老板总览", "gauge", "总览", 1, {"modules": [
            m("metric", "", "third", metric="overdue_tasks"), m("metric", "", "third", metric="blocked_tasks"),
            m("metric", "", "third", metric="open_exceptions"), m("metric", "", "third", metric="below_safety"),
            m("metric", "", "third", metric="quarantined_lots"), m("metric", "", "third", metric="missing_checklists"),
            m("metric", "", "third", metric="unverified_evidence"), m("metric", "", "third", metric="pending_ingest"),
            m("metric", "", "third", metric="in_transit"), m("metric", "", "third", metric="po_pending_approval"),
            m("metric", "", "third", metric="po_overdue"), m("metric", "", "third", metric="receipts_pending"),
            m("metric", "", "third", metric="orders_due_soon"), m("metric", "", "third", metric="orders_overdue"),
            m("metric", "", "third", metric="orders_short"),
            m("kanban", "待决策 / 受阻 / 待核验", source="tasks", task_filter={"status": "blocked,review"}),
        ]}, roles=mgmt, desc="每个指标都能下钻；没有数据显示未知，不显示正常")
        page("任务与异常", "list-checks", "总览", 2, {"modules": [
            m("metric", "", "third", metric="overdue_tasks"), m("metric", "", "third", metric="review_tasks"),
            m("metric", "", "third", metric="open_exceptions"),
            m("kanban", "全部任务", source="tasks", task_filter={}),
        ]})
        page("采购", "shopping-cart", "业务", 10, {"modules": PROCUREMENT_MODULES()})
        page("场地运营", "hammer", "业务", 11, {"modules": [
            m("kanban", "设备与建设进度", source="records", record_type_id=eq_rt.id),
            m("form", "新增设备/项目", "half", source="records", record_type_id=eq_rt.id),
            m("text", "说明", "half", body="建设验收与持续服务分开。设备「已验收」需要验收表与安装后照片，且需核验通过。"),
        ]})
        page("订单与配送", "truck", "业务", 12, {"modules": ORDERS_MODULES()})
        page("库存与调拨", "boxes", "业务", 13, {"modules": [
            m("metric", "", "third", metric="below_safety"), m("metric", "", "third", metric="in_transit"),
            m("metric", "", "third", metric="quarantined_lots"),
            m("stock", "多仓库存"), m("transfers", "调拨"),
        ]})
        page("生产", "factory", "业务", 14, {"modules": [
            m("production", "切配生产"),
            m("boxes", "补打标签 / 单独称重"),
            m("lots", "批次质量状态", "full", category=None),
        ]})
        sanit = m("checklist", "开工卫生检查", daily_required=True, create_task_on_fail=True, items=[
            {"key": "hands", "label": "洗手消毒设施可用", "kind": "pass_fail"},
            {"key": "surface", "label": "接触面已清洁（拍照）", "kind": "pass_fail", "requires_evidence": True},
            {"key": "cooler", "label": "冷藏温度 (°F)", "kind": "number", "min": 33, "max": 41},
        ])
        page("质量与食品安全", "shield-check", "业务", 15, {"modules": [
            m("metric", "", "third", metric="quarantined_lots"), m("metric", "", "third", metric="open_exceptions"),
            m("metric", "", "third", metric="missing_checklists"),
            sanit,
            m("kanban", "不合格 → 整改 → 复查", source="tasks", task_filter={"kind": "exception", "source_module": sanit["id"]}),
            m("lots", "批次隔离与放行"),
            m("trace", "追溯查询"),
            m("text", "参数说明", body="具体清洁浓度、接触时间、温度等参数由现场食品安全负责人依据实际条件制定并批准；本页参数为示例。"),
        ]})
        db.commit()
    finally:
        db.close()
