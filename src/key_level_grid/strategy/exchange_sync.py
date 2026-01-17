"""
交易所数据同步模块

负责从交易所同步账户余额、持仓、挂单、成交记录等数据
"""

import asyncio
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from key_level_grid.utils.logger import get_logger


class ExchangeSyncManager:
    """交易所数据同步管理器"""
    
    def __init__(
        self,
        executor,
        config,
        position_manager,
        notifier=None,
    ):
        """
        初始化同步管理器
        
        Args:
            executor: GateExecutor 实例
            config: 策略配置 (KeyLevelGridConfig)
            position_manager: GridPositionManager 实例
            notifier: NotificationManager 实例 (可选)
        """
        self.executor = executor
        self.config = config
        self.position_manager = position_manager
        self.notifier = notifier
        self.logger = get_logger(__name__)
        
        # 账户余额缓存
        self.account_balance: Dict[str, float] = {"total": 0, "free": 0, "used": 0}
        self.balance_updated_at: float = 0
        
        # 挂单缓存
        self.open_orders: List[Dict] = []
        self.orders_updated_at: float = 0
        self.contract_size: float = 1.0
        
        # 持仓缓存
        self.position: Dict[str, Any] = {}
        self.position_updated_at: float = 0
        self._last_position_btc: Optional[float] = None
        self._last_position_avg_price: float = 0.0
        self._last_position_unrealized_pnl: float = 0.0
        self._last_position_contracts: Optional[int] = None
        
        # 成交记录缓存
        self.trades: List[Dict] = []
        self.trades_updated_at: float = 0
        
        # 当前市场状态
        self._current_state = None
    
    def set_current_state(self, state):
        """设置当前市场状态"""
        self._current_state = state
    
    def _convert_to_gate_symbol(self, binance_symbol: str) -> str:
        """将 Binance 符号转换为 Gate 格式"""
        symbol = binance_symbol.upper()
        if symbol.endswith("USDT"):
            base = symbol[:-4]
            return f"{base}/USDT:USDT"
        return symbol
    
    async def update_account_balance(self) -> Dict[str, float]:
        """从交易所更新账户余额"""
        if not self.executor:
            return self.account_balance
        
        try:
            balance = await self.executor.get_balance("USDT")
            self.account_balance = {
                "total": balance.get("total", 0),
                "free": balance.get("free", 0),
                "used": balance.get("used", 0),
            }
            self.balance_updated_at = time.time()
            
            self.logger.debug(
                f"💰 账户余额更新: total={self.account_balance['total']:.2f}, "
                f"free={self.account_balance['free']:.2f}"
            )
        except Exception as e:
            self.logger.error(f"获取账户余额失败: {e}")
        
        return self.account_balance
    
    async def update_open_orders(self) -> List[Dict]:
        """从交易所同步当前挂单"""
        if not self.executor or self.config.dry_run:
            return self.open_orders
        
        try:
            gate_symbol = self._convert_to_gate_symbol(self.config.symbol)
            orders = await self.executor.get_open_orders(gate_symbol)
            
            # 获取合约信息
            contract_size = await self._get_contract_size(gate_symbol)
            self.contract_size = contract_size
            
            self.open_orders = []
            for o in orders:
                price = float(o.get("price", 0) or 0)
                remaining_contracts = float(o.get("remaining", 0) or 0)
                real_btc = remaining_contracts * contract_size
                amount_usdt = real_btc * price
                
                self.open_orders.append({
                    "id": o.get("id", ""),
                    "side": o.get("side", ""),
                    "price": price,
                    "amount": amount_usdt,
                    "contracts": remaining_contracts,
                    "base_amount": real_btc,
                    "raw_contracts": remaining_contracts,
                    "filled": float(o.get("filled", 0) or 0),
                    "remaining": remaining_contracts,
                    "status": o.get("status", ""),
                    "type": o.get("type", ""),
                    "timestamp": o.get("timestamp", 0),
                    "contract_size": contract_size,
                })
            
            self.orders_updated_at = time.time()
            
            self.logger.debug(
                f"📋 挂单同步: {len(self.open_orders)} 个订单, "
                f"contractSize={contract_size}"
            )
        except Exception as e:
            self.logger.error(f"同步挂单失败: {e}")
        
        return self.open_orders
    
    async def update_position(self) -> Dict[str, Any]:
        """从交易所同步当前持仓"""
        if not self.executor or self.config.dry_run:
            return self.position
        
        try:
            gate_symbol = self._convert_to_gate_symbol(self.config.symbol)
            positions = await self.executor.get_positions(gate_symbol)
            
            contract_size = await self._get_contract_size(gate_symbol)
            self.contract_size = contract_size
            
            self.position = {}
            for pos in positions:
                pos_symbol = pos.get("symbol", "")
                symbol_match = (
                    pos_symbol == gate_symbol or
                    pos_symbol.replace("/", "_").replace(":USDT", "") == gate_symbol.replace("/", "_").replace(":USDT", "") or
                    gate_symbol.split("/")[0] in pos_symbol
                )
                
                if symbol_match:
                    raw_contracts = float(pos.get("contracts", 0) or 0)
                    notional = float(pos.get("notional", 0) or 0)
                    entry_price = float(pos.get("entryPrice", 0) or 0)
                    
                    real_btc = raw_contracts * contract_size
                    
                    if raw_contracts > 0:
                        self.position = {
                            "symbol": pos_symbol,
                            "contracts": real_btc,
                            "raw_contracts": raw_contracts,
                            "notional": abs(notional) if notional else real_btc * entry_price,
                            "entry_price": entry_price,
                            "side": "long",
                            "unrealized_pnl": float(pos.get("unrealizedPnl", 0) or 0),
                            "contract_size": contract_size,
                        }
                        self.logger.info(
                            f"📊 持仓同步: {real_btc:.6f} BTC ({raw_contracts:.0f}张) @ {entry_price:.2f}, "
                            f"价值={self.position['notional']:.2f} USDT"
                        )
                        
                        if self._last_position_contracts is None:
                            self._last_position_contracts = int(raw_contracts)
                        break
            
            if not self.position:
                self.logger.debug("📊 无持仓")
            
            # 检测持仓变动并通知
            await self._check_position_change()
            
            self.position_updated_at = time.time()
            
        except Exception as e:
            self.logger.error(f"同步持仓失败: {e}")
        
        return self.position
    
    async def _check_position_change(self) -> None:
        """检测持仓变动并发送通知"""
        new_qty = float(self.position.get("contracts", 0) or 0) if self.position else 0.0
        new_avg = float(self.position.get("entry_price", 0) or 0) if self.position else 0.0
        new_unreal = float(self.position.get("unrealized_pnl", 0) or 0) if self.position else 0.0
        
        if self._last_position_btc is None:
            self._last_position_btc = new_qty
            self._last_position_avg_price = new_avg
            self._last_position_unrealized_pnl = new_unreal
        elif new_qty != self._last_position_btc:
            action = "买入" if new_qty > self._last_position_btc else "卖出"
            if new_qty == 0 and self._last_position_btc > 0:
                action = "平仓"
            qty_delta = abs(new_qty - self._last_position_btc)
            
            price_hint = 0.0
            if self._current_state:
                price_hint = float(self._current_state.close or 0)
            if price_hint <= 0 and new_avg > 0:
                price_hint = new_avg
            
            if self.notifier:
                await self.notifier.notify_position_flux(
                    action=action,
                    price=price_hint,
                    qty=qty_delta,
                    total_qty=new_qty,
                    avg_price=new_avg,
                    pnl=new_unreal,
                )
            
            self._last_position_btc = new_qty
            self._last_position_avg_price = new_avg
            self._last_position_unrealized_pnl = new_unreal
    
    async def update_trades(self) -> List[Dict]:
        """从交易所获取成交记录"""
        if not self.executor or self.config.dry_run:
            return self.trades
        
        try:
            gate_symbol = self._convert_to_gate_symbol(self.config.symbol)
            
            # 获取最近 48 小时的成交记录
            since = int((time.time() - 172800) * 1000)
            
            trades = await self.executor.get_trade_history(
                symbol=gate_symbol,
                since=since,
                limit=50
            )
            
            self.trades = []
            for trade in trades:
                trade_time = trade.get("timestamp", 0)
                trade_datetime = datetime.fromtimestamp(trade_time / 1000) if trade_time else None

                amount_raw = float(trade.get("amount", 0) or 0)
                amount = amount_raw
                if self.config.market_type == "futures" and self.contract_size > 0:
                    amount = amount_raw * self.contract_size
                
                self.trades.append({
                    "id": trade.get("id", ""),
                    "order_id": trade.get("order") or trade.get("order_id") or trade.get("orderId", ""),
                    "time": trade_datetime.strftime("%Y-%m-%d %H:%M:%S") if trade_datetime else "",
                    "timestamp": trade_time,
                    "side": trade.get("side", ""),
                    "price": float(trade.get("price", 0) or 0),
                    "amount": amount,
                    "cost": float(trade.get("cost", 0) or 0),
                    "fee": float(trade.get("fee", {}).get("cost", 0) or 0),
                    "fee_currency": trade.get("fee", {}).get("currency", ""),
                })
            
            self.trades.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
            self.trades_updated_at = time.time()
            
            if self.trades:
                self.logger.debug(f"📜 成交记录同步: {len(self.trades)} 条")
            
        except Exception as e:
            self.logger.error(f"同步成交记录失败: {e}")
        
        return self.trades
    
    async def _get_contract_size(self, gate_symbol: str) -> float:
        """获取合约大小"""
        if self.contract_size > 0 and self.contract_size != 1.0:
            return self.contract_size
        
        try:
            markets = self.executor._exchange.markets
            if not markets:
                await asyncio.get_event_loop().run_in_executor(
                    None, self.executor._exchange.load_markets
                )
                markets = self.executor._exchange.markets
            market = markets.get(gate_symbol, {})
            contract_size = market.get('contractSize', 1.0) or 1.0
            return contract_size
        except Exception as e:
            default_size = getattr(self.config, 'default_contract_size', 1.0)
            self.logger.warning(f"获取 contractSize 失败，使用默认值 {default_size}: {e}")
            return default_size
    
    def get_exchange_min_contracts(self) -> float:
        """获取交易所最小下单张数"""
        try:
            gate_symbol = self._convert_to_gate_symbol(self.config.symbol)
            markets = self.executor._exchange.markets if self.executor else {}
            if not markets:
                return 1.0
            market = markets.get(gate_symbol, {})
            min_amount = market.get("limits", {}).get("amount", {}).get("min")
            return float(min_amount) if min_amount else 1.0
        except Exception:
            return 1.0
    
    def get_exchange_min_qty_btc(self) -> float:
        """获取交易所最小下单 BTC 数量"""
        min_contracts = self.get_exchange_min_contracts()
        return min_contracts * self.contract_size
    
    async def sync_all(self) -> Dict[str, Any]:
        """同步所有数据"""
        await self.update_account_balance()
        await self.update_open_orders()
        await self.update_position()
        await self.update_trades()
        
        return {
            "account_balance": self.account_balance,
            "open_orders": self.open_orders,
            "position": self.position,
            "trades": self.trades,
            "contract_size": self.contract_size,
        }
