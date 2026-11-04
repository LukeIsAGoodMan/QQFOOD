"""Demo seed. Business facts from PRD 附录A are seeded as *待核验 tasks*, never as completed facts.
All SKUs/quantities are 示例数据 and must be replaced by the verified opening count before go-live."""
import copy
import os
import uuid

from .core import hash_secret, encrypt_secret
from .db import SessionLocal
from .models import (AccountEntry, Contact, FieldDefinition, NotebookNote, Page, PageVersion, RecordType, SafetyStock,
                     Site, Sku, Task, Transfer, TransferLine, User, Warehouse, BusinessRecord, Lot, gid)
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
        db.add_all([wh597, ext])
        skus = {
            "RM-BEEF": Sku(code="RM-BEEF", name="示例 冷冻牛肉卷", category="原料", base_unit="箱", conversions={"磅": 20}),
            "RM-SPICE": Sku(code="RM-SPICE", name="示例 香料包", category="原料", base_unit="箱", conversions={"包": 50}),
            "PK-TRAY": Sku(code="PK-TRAY", name="示例 包装托盘", category="包材", base_unit="箱"),
            "FG-SOUP": Sku(code="FG-SOUP", name="示例 成品汤料", category="成品", base_unit="箱"),
        }
        db.add_all(skus.values())
        db.flush()
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
        db.add(BusinessRecord(record_type_id=po_rt.id, title="原料包材询价（需求版本 v2：15% 余量）", status="询价",
                              owner_id=users["buyer"].id,
                              data={po_f["类别"]: "原料包材", po_f["需求版本"]: "v2 (15%)；v1 询价曾用 5%"},
                              created_by=users["buyer"].id))

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
            m("metric", "", "third", metric="in_transit"),
            m("kanban", "待决策 / 受阻 / 待核验", source="tasks", task_filter={"status": "blocked,review"}),
        ]}, roles=mgmt, desc="每个指标都能下钻；没有数据显示未知，不显示正常")
        page("任务与异常", "list-checks", "总览", 2, {"modules": [
            m("metric", "", "third", metric="overdue_tasks"), m("metric", "", "third", metric="review_tasks"),
            m("metric", "", "third", metric="open_exceptions"),
            m("kanban", "全部任务", source="tasks", task_filter={}),
        ]})
        page("采购", "shopping-cart", "业务", 10, {"modules": [
            m("metric", "", "third", metric=f"records:{po_rt.id}:已下单|已发货"),
            m("metric", "", "third", metric=f"records:{po_rt.id}:已收货"),
            m("form", "新建采购单", "third", source="records", record_type_id=po_rt.id),
            m("table", "采购单", source="records", record_type_id=po_rt.id,
              columns=["title", "status", po_f["供应商"], po_f["类别"], po_f["金额"], po_f["预计到货"], "owner"]),
        ]})
        page("场地运营", "hammer", "业务", 11, {"modules": [
            m("kanban", "设备与建设进度", source="records", record_type_id=eq_rt.id),
            m("form", "新增设备/项目", "half", source="records", record_type_id=eq_rt.id),
            m("text", "说明", "half", body="建设验收与持续服务分开。设备「已验收」需要验收表与安装后照片，且需核验通过。"),
        ]})
        page("订单与配送", "truck", "业务", 12, {"modules": [
            m("table", "客户订单", source="records", record_type_id=od_rt.id,
              columns=["title", "status", od_f["客户"], od_f["PO号"], od_f["交货日期"], od_f["箱数"]]),
            m("form", "录入客户 PO", "half", source="records", record_type_id=od_rt.id),
        ]})
        page("库存与调拨", "boxes", "业务", 13, {"modules": [
            m("metric", "", "third", metric="below_safety"), m("metric", "", "third", metric="in_transit"),
            m("metric", "", "third", metric="quarantined_lots"),
            m("stock", "多仓库存"), m("transfers", "调拨"),
        ]})
        page("生产", "factory", "业务", 14, {"modules": [
            m("production", "生产批次"),
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
