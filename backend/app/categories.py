"""商品分类（库存按这个分）。

七类是老板定的；“未分类”只是兜底（旧数据猜不出来、ChatGPT 没给），在库存页一点就能补。
旧版的 原料 / 包材 / 成品 / 耗材 仍然接受：按品名猜到新分类，猜不出来就是“未分类”。
"""
import re

SKU_CATEGORIES = ["干货", "冻货", "原料肉", "原料菜", "生产用品", "公司用品", "生产机器"]
UNCATEGORIZED = "未分类"
ALL = SKU_CATEGORIES + [UNCATEGORIZED]
FOOD = {"干货", "冻货", "原料肉", "原料菜"}  # 食品：收货 / 盘点要 Lot（追溯）

_SYN = {
    "干货": "干货", "dry": "干货", "drygoods": "干货", "dry goods": "干货", "常温": "干货",
    "冻货": "冻货", "冷冻": "冻货", "冻品": "冻货", "frozen": "冻货",
    "原料肉": "原料肉", "肉": "原料肉", "肉类": "原料肉", "meat": "原料肉", "rawmeat": "原料肉", "raw meat": "原料肉",
    "原料菜": "原料菜", "菜": "原料菜", "蔬菜": "原料菜", "vegetable": "原料菜", "vegetables": "原料菜", "produce": "原料菜", "veg": "原料菜",
    "生产用品": "生产用品", "包材": "生产用品", "耗材": "生产用品", "packaging": "生产用品", "supplies": "生产用品", "production supplies": "生产用品",
    "公司用品": "公司用品", "办公用品": "公司用品", "日常用品": "公司用品", "office": "公司用品", "office supplies": "公司用品",
    "生产机器": "生产机器", "机器": "生产机器", "设备": "生产机器", "equipment": "生产机器", "machine": "生产机器", "machinery": "生产机器",
    "未分类": UNCATEGORIZED, "": UNCATEGORIZED,
}

_KW = {
    "原料肉": r"肉|鸡|猪|牛|羊|鸭|鹅|排骨|鸡翅|鸡腿|五花|梅头|里脊|腩|虾|鱼|meat|chicken|pork|beef|lamb|duck|rib|wing|thigh|breast|belly|butt|shrimp|fish",
    "原料菜": r"菜|葱|姜|蒜|椒|萝卜|瓜|茄|菇|笋|芹|韭|薯|豆芽|番茄|vegetable|onion|garlic|ginger|cabbage|pepper|carrot|potato|mushroom|scallion|celery",
    "冻货": r"冷冻|速冻|冻|饺|包子|丸|汤圆|馄饨|油条|芝麻球|烧卖|frozen|dumpling|meatball|bun",
    "干货": r"汤料|底料|火锅|调料|调味|香料|酱|粉|面|米|干|油|盐|糖|醋|sauce|paste|soup ?base|spice|season|noodle|rice|flour|dried|oil",
    "公司用品": r"办公|文具|打印|墨盒|纸巾|office|printer|stationery",
    "生产机器": r"机|设备|秤|slicer|machine|scale|equipment",
}


def _hit(kind: str, name: str) -> bool:
    return bool(re.search(_KW[kind], (name or "").lower()))


def guess(name: str, old: str = "") -> str:
    """按品名（和旧分类）猜新分类；猜不出就是未分类，宁可让人点一下也不乱归。"""
    if old == "包材":
        return "生产用品"
    if old == "设备":
        return "生产机器"
    if old == "耗材":
        return "公司用品" if _hit("公司用品", name) else "生产用品"
    if old == "原料":
        for k in ("原料肉", "干货", "原料菜", "冻货"):
            if _hit(k, name):
                return k
        return UNCATEGORIZED
    if old == "成品":  # 成品要看是冷冻还是常温；切好的肉到底冻不冻说不准 → 让人选
        for k in ("冻货", "干货"):
            if _hit(k, name):
                return k
        return UNCATEGORIZED
    return UNCATEGORIZED


def normalize(cat, name: str = "") -> str | None:
    """把输入的分类转成七类之一（或未分类）。认不出来返回 None（调用方报错）。"""
    c = str(cat or "").strip()
    if c in ("原料", "成品"):
        return guess(name, c)
    return _SYN.get(c) or _SYN.get(c.lower())
