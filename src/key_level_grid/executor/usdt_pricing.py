"""
通用 USDT 计价工具，供 Gate/Bitget 等执行器复用。
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import math
from typing import Optional, Tuple


def compute_usdt_quantity(
    *,
    value_usd: float,
    price: float,
    contract_size: float,
    min_qty: float,
    precision: Optional[int] = None,
    step_size: Optional[float] = None,
) -> Tuple[float, float]:
    """
    将 USDT 金额转换为合约张数。

    Args:
        value_usd: 下单金额（USDT）
        price: 参考价格
        contract_size: 每张合约对应的基础币数量
        min_qty: 交易所规定的最小数量
        precision: 数量小数位（可选）
        step_size: 步长（可选）
    """
    if price is None or contract_size is None:
        raise ValueError("price 和 contract_size 不能为 None")
        
    if price <= 0 or contract_size <= 0:
        raise ValueError("price 和 contract_size 必须大于 0")

    raw_qty = value_usd / (price * contract_size)
    adjusted_qty = max(raw_qty, min_qty)

    if step_size and step_size > 0:
        steps = math.floor(adjusted_qty / step_size)
        adjusted_qty = steps * step_size

    # ✅ 策略要求：如果计算结果太小导致为0，但原始意图是下单（raw_qty > 0），
    # 则强制使用最小单位（step_size 或 min_qty），避免“金额太小无法下单”
    if adjusted_qty == 0 and raw_qty > 0:
        if min_qty > 0:
            adjusted_qty = min_qty
        elif step_size and step_size > 0:
            adjusted_qty = step_size
        # 如果都没有，保持为0，让后续逻辑报错或忽略

    decimals = _normalize_precision(precision, step_size)
    if decimals is not None:
        adjusted_qty = round(adjusted_qty, decimals)

    return adjusted_qty, raw_qty


def _normalize_precision(precision: Optional[float], step_size: Optional[float]) -> Optional[int]:
    """
    将交易所给出的 precision/step 信息转换为 round() 需要的小数位数。
    """
    if precision is None:
        return _decimals_from_step(step_size)

    # precision 可能是 4、4.0 这类“小数位数”，也可能是 0.001 这类步长
    try:
        value = float(precision)
    except (TypeError, ValueError):
        return _decimals_from_step(step_size)

    if value <= 0:
        return _decimals_from_step(step_size)

    if value.is_integer():
        return int(value)

    if value < 1:
        return _decimals_from_step(value)

    return int(value)


def _decimals_from_step(step: Optional[float]) -> Optional[int]:
    if not step or step <= 0:
        return None
    try:
        decimal_value = Decimal(str(step)).normalize()
    except (InvalidOperation, ValueError):
        return None
    return max(0, -decimal_value.as_tuple().exponent)

