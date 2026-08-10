"""
工號→姓名/部門(MFG產線/EE設備)對照表。

資料來源：使用者提供的「DA EE Maintenance Record.xlsx」EE ID分頁(APG Dashboard
專案用build_ee_master.py整理成ee_master.json)，這裡只取用/保留id/name/dept
三個欄位存成engineer_master.json，不搬到職日/離職日/職務這些跟這支查詢
機器人無關的HR資料，減少不必要的人事資料進版控。

原始dept欄位值是OP(產線技術員)/EE(設備工程師)/PE(製程工程師)，使用者慣稱
OP為"MFG"(2026/08/10確認：OP底下職稱都是技術員/領班，對應「產線」；EE底下
職稱都是工程師，對應「設備」)，這裡轉存成MFG/EE兩種顯示標籤，PE跟其他值
不硬塞進MFG/EE二分類，算進dept_breakdown()的"未知"。

人員異動後，比照APG Dashboard的流程重新產生ee_master.json，再用同樣的
id/name/dept欄位蓋掉這支engineer_master.json即可，不用改程式碼。
"""
import json
import os
import re

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engineer_master.json")

# OP=產線技術員(使用者慣稱MFG)，EE=設備工程師；PE(製程工程師)跟查無資料的
# 工號一律歸類到dept_breakdown()的"未知"，不勉強塞進MFG/EE二分類。
_DEPT_DISPLAY = {"OP": "MFG", "EE": "EE"}

_cache = None


def _normalize_id(raw):
    """跟APG Dashboard build_ee_master.py的_norm()同一套正規化規則：轉大寫、
    去掉非英數字元、去掉開頭的S、去掉開頭的0，這樣不管CPIS吐出來的工號是
    "S10435"還是"10435"還是"s010435"都能對到同一筆資料。"""
    if raw is None:
        return ""
    s = re.sub(r"[^0-9A-Za-z]", "", str(raw)).upper()
    s = s.lstrip("S")
    s = s.lstrip("0")
    return s


def _load():
    global _cache
    if _cache is not None:
        return _cache
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            _cache = json.load(f)
    except (OSError, json.JSONDecodeError):
        _cache = {}
    return _cache


def get_engineer(engineer_id):
    """查工號對應的{"name":..., "dept":...}，查不到回傳None。"""
    key = _normalize_id(engineer_id)
    if not key:
        return None
    return _load().get(key)


def get_engineer_name(engineer_id):
    """查工號對應的姓名，查不到回傳None。"""
    info = get_engineer(engineer_id)
    name = info.get("name") if info else None
    return name or None


def get_engineer_dept_label(engineer_id):
    """回傳MFG/EE顯示標籤；查無此工號、或部門不是OP/EE(例如PE或空白)都回傳
    None，呼叫端要自己決定怎麼處理查不到的情況(通常歸類成"未知")。"""
    info = get_engineer(engineer_id)
    if not info:
        return None
    return _DEPT_DISPLAY.get(info.get("dept"))


def format_engineer(engineer_id):
    """工號+姓名的顯示字串，例如"26671(廖小明)"；查不到姓名就只顯示工號
    本身，不要讓查無資料的工號在推播上顯示成奇怪的樣子。"""
    if not engineer_id:
        return engineer_id or ""
    name = get_engineer_name(engineer_id)
    return f"{engineer_id}({name})" if name else str(engineer_id)


def dept_breakdown(engineer_ids):
    """依engineer_ids清單算MFG/EE/未知的紀錄數，回傳{"MFG": n, "EE": n,
    "未知": n}("未知"包含查無資料的工號，以及部門不是OP/EE的工號)。"""
    counts = {"MFG": 0, "EE": 0, "未知": 0}
    for eid in engineer_ids:
        label = get_engineer_dept_label(eid)
        if label in counts:
            counts[label] += 1
        else:
            counts["未知"] += 1
    return counts
