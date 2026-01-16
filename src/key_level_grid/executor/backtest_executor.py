"""
回测执行器（离线撮合）
"""

import time
from typing import Dict, List, Optional

from key_level_grid.executor.base import Order, OrderSide, OrderStatus


class _DummyExchange:
    def __init__(self, markets: Dict):
        self.markets = markets

    def load_markets(self):
        return self.markets


class BacktestExecutor:
    def __init__(
        self,
        symbol: str,
        initial_balance: float,
        contract_size: float,
        leverage: float = 1.0,
        min_contracts: float = 1.0,
    ):
        self.symbol = symbol
        self._balance_total = float(initial_balance)
        self._balance_free = float(initial_balance)
        self._balance_used = 0.0
        self._contract_size = float(contract_size) if contract_size > 0 else 1.0
        self._leverage = float(leverage) if leverage > 0 else 1.0
        self._min_contracts = float(min_contracts) if min_contracts > 0 else 1.0

        self._open_orders: List[Dict] = []
        self._trades: List[Dict] = []
        self._position_contracts = 0.0
        self._position_entry_price = 0.0
        self._last_price = 0.0

        self._exchange = _DummyExchange(
            {
                symbol: {
                    "contractSize": self._contract_size,
                    "limits": {"amount": {"min": self._min_contracts, "step": 1}},
                    "precision": {"amount": 0},
                }
            }
        )

    async def submit_order(self, order: Order) -> bool:
        qty = float(order.quantity or 0)
        price = float(order.price or 0)
        is_trigger = order.metadata.get("order_mode") == "trigger"
        trigger_price = float(order.metadata.get("triggerPrice", 0) or 0)
        if qty <= 0 or (price <= 0 and not is_trigger):
            order.status = OrderStatus.REJECTED
            order.reject_reason = "Invalid quantity or price"
            return False
        if is_trigger and trigger_price <= 0:
            order.status = OrderStatus.REJECTED
            order.reject_reason = "Invalid trigger price"
            return False

        if qty < self._min_contracts:
            order.status = OrderStatus.REJECTED
            order.reject_reason = "Below min contracts"
            return False

        effective_price = trigger_price if is_trigger else price
        notional = qty * self._contract_size * effective_price
        required_margin = notional / self._leverage
        if order.side == OrderSide.BUY and self._balance_free < required_margin:
            order.status = OrderStatus.REJECTED
            order.reject_reason = "Insufficient balance"
            return False

        order.exchange_order_id = f"bt_{int(time.time() * 1000)}"
        order.status = OrderStatus.SUBMITTED

        self._open_orders.append(
            {
                "id": order.exchange_order_id,
                "side": order.side.value,
                "price": effective_price,
                "trigger_price": trigger_price if is_trigger else 0.0,
                "amount": qty,
                "remaining": qty,
                "filled": 0.0,
                "status": "open",
                "timestamp": int(time.time() * 1000),
            }
        )
        return True

    async def cancel_order(self, order: Order) -> bool:
        order_id = getattr(order, "exchange_order_id", "") or ""
        if not order_id:
            return False
        for idx, o in enumerate(self._open_orders):
            if o.get("id") == order_id:
                self._open_orders.pop(idx)
                return True
        return False

    async def cancel_all_orders(self, symbol: str) -> bool:
        self._open_orders = [o for o in self._open_orders if o.get("trigger_price", 0)]
        return True

    async def cancel_all_plan_orders(self, symbol: str) -> bool:
        self._open_orders = [o for o in self._open_orders if not o.get("trigger_price", 0)]
        return True

    def match_with_kline(self, kline) -> None:
        self._last_price = float(kline.close or 0)
        high = float(kline.high or 0)
        low = float(kline.low or 0)
        if high <= 0 or low <= 0:
            return

        filled_orders = []
        for o in self._open_orders:
            side = o.get("side")
            price = float(o.get("price", 0) or 0)
            trigger_price = float(o.get("trigger_price", 0) or 0)
            qty = float(o.get("remaining", 0) or 0)
            if qty <= 0 or price <= 0:
                continue

            if trigger_price:
                if side == "sell" and low <= trigger_price:
                    if not self._fill_order(o, qty, trigger_price, "sell", kline.timestamp):
                        continue
                    filled_orders.append(o)
                elif side == "buy" and high >= trigger_price:
                    if not self._fill_order(o, qty, trigger_price, "buy", kline.timestamp):
                        continue
                    filled_orders.append(o)
                continue

            if side == "buy" and low <= price <= high:
                if not self._fill_order(o, qty, price, "buy", kline.timestamp):
                    continue
                filled_orders.append(o)
            elif side == "sell" and low <= price <= high:
                if not self._fill_order(o, qty, price, "sell", kline.timestamp):
                    continue
                filled_orders.append(o)

        if filled_orders:
            self._open_orders = [o for o in self._open_orders if o not in filled_orders]

    def _fill_order(self, order: Dict, qty: float, price: float, side: str, timestamp_ms: int) -> bool:
        notional = qty * self._contract_size * price
        required_margin = notional / self._leverage

        if side == "buy":
            if self._balance_free < required_margin:
                return False
            self._balance_free -= required_margin
            self._balance_used += required_margin

            new_total = self._position_contracts + qty
            if new_total > 0:
                self._position_entry_price = (
                    (self._position_entry_price * self._position_contracts) + price * qty
                ) / new_total
            self._position_contracts = new_total
        else:
            if self._position_contracts < qty:
                return False
            entry_price = self._position_entry_price
            # 释放保证金并结算盈亏
            margin_release = (entry_price * self._contract_size * qty) / self._leverage
            realized_pnl = (price - entry_price) * self._contract_size * qty
            self._balance_used = max(0.0, self._balance_used - margin_release)
            self._balance_free += margin_release + realized_pnl
            self._position_contracts = max(0.0, self._position_contracts - qty)
            if self._position_contracts == 0:
                self._position_entry_price = 0.0

        trade = {
            "id": f"bt_trade_{int(time.time() * 1000)}",
            "timestamp": int(timestamp_ms),
            "side": side,
            "price": price,
            "amount": qty,
            "cost": notional,
            "fee": 0.0,
            "fee_currency": "USDT",
        }
        if side == "sell":
            trade["realized_pnl"] = realized_pnl
        self._trades.append(trade)
        self._balance_total = self._balance_free + self._balance_used
        return True

    async def get_open_orders(self, symbol: str) -> List[Dict]:
        return list(self._open_orders)

    async def get_positions(self, symbol: str) -> List[Dict]:
        if self._position_contracts <= 0:
            return []
        notional = self._position_contracts * self._contract_size * self._last_price
        return [
            {
                "symbol": symbol,
                "contracts": self._position_contracts,
                "side": "long",
                "notional": notional,
                "entryPrice": self._position_entry_price,
                "unrealizedPnl": (self._last_price - self._position_entry_price)
                * self._contract_size
                * self._position_contracts,
            }
        ]

    async def get_balance(self, asset: str) -> Dict[str, float]:
        self._balance_total = self._balance_free + self._balance_used
        return {
            "total": self._balance_total,
            "free": self._balance_free,
            "used": self._balance_used,
        }

    async def get_trade_history(self, symbol: str, since: int, limit: int = 50) -> List[Dict]:
        trades = [t for t in self._trades if t.get("timestamp", 0) >= since]
        trades.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        return trades[:limit]

    async def get_plan_orders(self, symbol: str, status: str = "open", limit: int = 100) -> List[Dict]:
        if status == "open":
            return [o for o in self._open_orders if o.get("trigger_price", 0)]
        if status == "finished":
            return []
        return []

    async def cancel_plan_order(self, symbol: str, order_id: str) -> bool:
        if not order_id:
            return True
        for idx, o in enumerate(self._open_orders):
            if o.get("id") == order_id:
                self._open_orders.pop(idx)
                return True
        return False

    def get_equity(self) -> float:
        position_value = self._position_contracts * self._contract_size * self._last_price
        return self._balance_free + self._balance_used + position_value
