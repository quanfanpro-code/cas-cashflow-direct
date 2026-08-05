from __future__ import annotations

import hashlib
import math
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


CENT = Decimal("0.01")


def yuan_to_cent(value: object) -> int:
    """把人民币元严格转换为整数分。"""
    if value is None or isinstance(value, bool):
        raise ValueError(f"金额无效：{value!r}")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"金额无效：{value!r}")
    text = str(value).strip().replace("，", ",").replace("－", "-").replace("−", "-")
    negative = (text.startswith("(") and text.endswith(")")) or (
        text.startswith("（") and text.endswith("）")
    )
    if negative:
        text = "-" + text[1:-1]
    text = text.replace(",", "").replace("￥", "").replace("¥", "").replace("人民币", "").strip()
    if not text:
        raise ValueError(f"金额无效：{value!r}")
    try:
        amount = Decimal(text)
        if not amount.is_finite():
            raise ValueError
        return int(amount.quantize(CENT, rounding=ROUND_HALF_UP) * 100)
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise ValueError(f"金额无效：{value!r}") from exc


def statement_amount_cent(cash_delta_cent: int, normal_direction: str) -> int:
    """按正表项目正常方向转换现金净变动金额。"""
    if normal_direction == "inflow":
        return cash_delta_cent
    if normal_direction == "outflow":
        return -cash_delta_cent
    raise ValueError(f"方向无效：{normal_direction!r}，只允许 inflow 或 outflow")


def stable_id(prefix: str, *parts: object) -> str:
    """根据稳定业务组成生成短编号。"""
    namespace = prefix.strip().upper()
    if not namespace:
        raise ValueError("编号前缀不能为空")
    normalized = "\x1f".join("" if part is None else str(part) for part in parts)
    digest = hashlib.sha256(f"{namespace}\x1e{normalized}".encode("utf-8")).hexdigest()[:20]
    return f"{namespace}_{digest}"
