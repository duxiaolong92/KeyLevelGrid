"""
仓位管理模块 (V2.3 简化版)

基于支撑/阻力位的网格仓位管理

注意: 配置类、类型、状态类已迁移到 core/ 模块
"""

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from key_level_grid.utils.logger import get_logger

# 从 core 模块导入（新路径）
from key_level_grid.core.config import (
    GridConfig,
    PositionConfig,
    StopLossConfig,
    TakeProfitConfig,
    ResistanceConfig,
)
from key_level_grid.core.types import (
    LevelStatus,
    LevelLifecycleStatus,
)
from key_level_grid.core.state import (
    GridLevelState,
    GridOrder,
    GridState,
    ActiveFill,
    STATE_VERSION,
)

# 从 analysis 模块导入
from key_level_grid.analysis.resistance import PriceLevel


# 价格容差常量（0.01%）- 默认值，可被配置覆盖
# 遵循 CONSTITUTION.md C1: 参数解耦
DEFAULT_PRICE_TOLERANCE = 0.0001
PRICE_TOLERANCE = DEFAULT_PRICE_TOLERANCE  # 向后兼容


class GridPositionManager:
    """
    网格仓位管理器 (V3.0 升级版)
    
    核心逻辑:
    1. 根据支撑位生成买入挂单
    2. 根据阻力位生成卖出挂单 (止盈)
    3. 统一止损 (跌破网格底线)
    
    V3.0 新增:
    - 支持 LevelCalculator MTF 水位生成
    - 支持 AtomicRebuildExecutor 原子性重构
    - 支持 MTFKlineFeed 一致性锁
    """
    
    def __init__(
        self,
        grid_config: Optional[GridConfig] = None,
        position_config: Optional[PositionConfig] = None,
        stop_loss_config: Optional[StopLossConfig] = None,
        take_profit_config: Optional[TakeProfitConfig] = None,
        resistance_config: Optional[ResistanceConfig] = None,
        symbol: str = "",
        exchange: str = "",
        full_config: Optional[Dict] = None,  # 🆕 V3.0: 完整配置字典
    ):
        self.grid_config = grid_config or GridConfig()
        self.position_config = position_config or PositionConfig()
        self.stop_loss_config = stop_loss_config or StopLossConfig()
        self.take_profit_config = take_profit_config or TakeProfitConfig()
        self.resistance_config = resistance_config or ResistanceConfig()
        self.symbol = symbol
        self.exchange = exchange
        self.logger = get_logger(__name__)
        self.full_config = full_config or {}  # 🆕 V3.0
        
        # 当前网格状态
        self.state: Optional[GridState] = None
        
        # 交易历史记录
        self.trade_history: List[Dict] = []
        
        # 持久化
        base_dir = Path(__file__).resolve().parents[2]  # 项目根目录
        self.state_dir = base_dir / "state" / "key_level_grid"
        if self.exchange:
            self.state_dir = self.state_dir / self.exchange.lower()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.state_dir / f"{self.symbol.lower()}_state.json"
        
        # 🆕 V3.0: 延迟初始化组件
        self._level_calculator = None
        self._mtf_feed = None
        self._atomic_executor = None
        
        # 🆕 V3.0: 从配置读取精度参数 (消除硬编码)
        precision_config = self.full_config.get("grid", {}).get("precision", {})
        self.price_tolerance = float(precision_config.get("price_tolerance", DEFAULT_PRICE_TOLERANCE))
        self.qty_tolerance = float(precision_config.get("qty_tolerance", 0.05))
        self.merge_tolerance = float(precision_config.get("merge_tolerance", 0.005))
    
    # ============================================
    # 网格创建
    # ============================================
    
    def create_grid(
        self,
        current_price: float,
        support_levels: List[PriceLevel],
        resistance_levels: List[PriceLevel]
    ) -> GridState:
        """
        创建网格
        
        Args:
            current_price: 当前价格
            support_levels: 支撑位列表 (已按强度排序)
            resistance_levels: 阻力位列表 (已按强度排序)
            
        Returns:
            GridState
        """
        # 1. 汇总所有原始价位，统一进行全局去重
        all_raw_levels = support_levels + resistance_levels
        
        # 过滤强度
        min_strength = self.resistance_config.min_strength
        qualified_levels = [l for l in all_raw_levels if l.strength >= min_strength]
        
        # 全局去重：相近价位保留强度更高者
        def _deduplicate_all(levels: List[PriceLevel]) -> List[PriceLevel]:
            if not levels:
                return []
            sorted_lvls = sorted(levels, key=lambda x: x.price)
            deduped: List[PriceLevel] = []
            tolerance = self.resistance_config.merge_tolerance or 0.005
            for lvl in sorted_lvls:
                if not deduped:
                    deduped.append(lvl)
                    continue
                last = deduped[-1]
                if last.price > 0 and abs(lvl.price - last.price) / last.price <= tolerance:
                    if lvl.strength > last.strength:
                        deduped[-1] = lvl
                else:
                    deduped.append(lvl)
            return deduped

        final_pool = _deduplicate_all(qualified_levels)
        
        # 2. 根据现价将去重后的池子划分为支撑和阻力
        strong_supports = [l for l in final_pool if l.price < current_price]
        strong_resistances = [l for l in final_pool if l.price > current_price]

        # 排序：支撑从高到低（近到远），阻力从低到高（近到远）
        strong_supports = sorted(strong_supports, key=lambda x: x.price, reverse=True)
        strong_resistances = sorted(strong_resistances, key=lambda x: x.price)
        
        # 限制网格数量
        max_grids = self.grid_config.max_grids
        strong_supports = strong_supports[:max_grids]
        strong_resistances = strong_resistances[:max_grids]
        
        if not strong_supports:
            self.logger.warning(f"没有找到 >= {min_strength} 分的支撑位")
            return None
        
        # 计算网格区间
        if self.grid_config.range_mode == "manual":
            upper_price = self.grid_config.manual_upper
            lower_price = self.grid_config.manual_lower
        else:
            upper_price = strong_resistances[0].price if strong_resistances else current_price * 1.1
            lower_price = strong_supports[-1].price

        # 手动区间过滤
        if self.grid_config.range_mode == "manual" and upper_price > 0 and lower_price > 0:
            strong_supports = [
                s for s in strong_supports if lower_price <= s.price <= upper_price
            ]
            strong_resistances = [
                r for r in strong_resistances if lower_price <= r.price <= upper_price
            ]
        
        # 网格底线
        grid_floor = lower_price * (1 - self.grid_config.floor_buffer)
        
        # 生成买入订单
        num_grids = len(strong_supports)
        max_position_usdt = self.position_config.max_position_usdt

        if self.position_config.allocation_mode == "weighted":
            total_strength = sum(max(s.strength, 0) for s in strong_supports)
            buy_orders = []
            for i, s in enumerate(strong_supports):
                if total_strength > 0:
                    amount_usdt = max_position_usdt * (s.strength / total_strength)
                else:
                    amount_usdt = max_position_usdt / num_grids
                amount_btc = amount_usdt / s.price
                buy_orders.append(
                    GridOrder(
                        grid_id=i + 1,
                        price=s.price,
                        amount_usdt=amount_usdt,
                        amount_btc=amount_btc,
                        strength=s.strength,
                        source=getattr(s, 'source', 'unknown'),
                    )
                )
        else:
            per_grid_usdt = max_position_usdt / num_grids
            buy_orders = []
            for i, s in enumerate(strong_supports):
                amount_usdt = per_grid_usdt
                amount_btc = amount_usdt / s.price
                buy_orders.append(
                    GridOrder(
                        grid_id=i + 1,
                        price=s.price,
                        amount_usdt=amount_usdt,
                        amount_btc=amount_btc,
                        strength=s.strength,
                        source=getattr(s, 'source', 'unknown'),
                    )
                )
        
        # 生成卖出订单
        sell_orders = []
        if strong_resistances:
            for i, r in enumerate(strong_resistances):
                sell_orders.append(
                    GridOrder(
                        grid_id=i + 1,
                        price=r.price,
                        amount_usdt=0,
                        amount_btc=0,
                        strength=r.strength,
                        source=getattr(r, 'source', 'unknown'),
                    )
                )
        
        # 创建网格状态
        self.state = GridState(
            symbol=self.symbol,
            direction="long",
            upper_price=upper_price,
            lower_price=lower_price,
            grid_floor=grid_floor,
            buy_orders=buy_orders,
            sell_orders=sell_orders,
            sell_quota_ratio=self.grid_config.sell_quota_ratio,
            min_profit_pct=self.grid_config.min_profit_pct,
            buy_price_buffer_pct=self.grid_config.buy_price_buffer_pct,
            sell_price_buffer_pct=self.grid_config.sell_price_buffer_pct,
            base_amount_per_grid=self.grid_config.base_amount_per_grid,
            base_position_locked=self.grid_config.base_position_locked,
            max_fill_per_level=self.grid_config.max_fill_per_level,
            recon_interval_sec=self.grid_config.recon_interval_sec,
            order_action_timeout_sec=self.grid_config.order_action_timeout_sec,
            anchor_price=current_price,
            anchor_ts=int(time.time()),
            resistance_levels=[
                {
                    "price": r.price,
                    "strength": r.strength,
                    "source": getattr(r, "source", ""),
                    "timeframe": getattr(r, "timeframe", ""),
                } for r in strong_resistances
            ],
            support_levels=[
                {
                    "price": s.price,
                    "strength": s.strength,
                    "source": getattr(s, "source", ""),
                    "timeframe": getattr(s, "timeframe", ""),
                } for s in strong_supports
            ],
        )

        # 初始化水位状态机
        RESISTANCE_ID_OFFSET = 1000
        
        self.state.support_levels_state = [
            GridLevelState(
                level_id=i + 1,
                price=s.price,
                side="buy",
                role="support",
                status=LevelStatus.IDLE,
            )
            for i, s in enumerate(strong_supports)
        ]
        self.state.resistance_levels_state = [
            GridLevelState(
                level_id=RESISTANCE_ID_OFFSET + i + 1,
                price=r.price,
                side="sell",
                role="resistance",
                status=LevelStatus.IDLE,
            )
            for i, r in enumerate(strong_resistances)
        ]
        
        # 构建逐级邻位映射
        self.state.level_mapping = self.build_level_mapping()
        
        self._save_state()
        
        self.logger.info(
            f"创建网格: {self.symbol}, "
            f"区间=[{lower_price:.2f}, {upper_price:.2f}], "
            f"底线={grid_floor:.2f}, "
            f"买单={len(buy_orders)}档, "
            f"卖单={len(sell_orders)}档"
        )
        
        return self.state

    # ============================================
    # 订单触发与执行
    # ============================================

    def get_base_amount_contracts(self, exchange_min_qty: float = 0.0) -> float:
        """将 base_amount_per_grid (BTC) 转为合约张数"""
        if not self.state:
            return 0.0
        base_btc = float(self.state.base_amount_per_grid or 0)
        return self._btc_to_contracts(base_btc, exchange_min_qty)
    
    def check_buy_trigger(self, current_price: float) -> Optional[GridOrder]:
        """检查是否触发买入"""
        if self.state is None:
            return None
        
        for order in self.state.buy_orders:
            if order.is_filled:
                continue
            
            tolerance = order.price * 0.003
            if current_price <= order.price + tolerance:
                return order
        
        return None
    
    def execute_buy(self, order: GridOrder, fill_price: float, fill_time: int = None) -> dict:
        """执行买入"""
        order.is_filled = True
        order.fill_price = fill_price
        order.fill_time = fill_time
        
        old_position = self.state.total_position_usdt
        old_avg = self.state.avg_entry_price
        
        new_position = old_position + order.amount_usdt
        if new_position > 0:
            self.state.avg_entry_price = (
                old_avg * old_position + fill_price * order.amount_usdt
            ) / new_position
        self.state.total_position_usdt = new_position
        
        if self.state.sell_orders:
            per_tp = new_position / len(self.state.sell_orders)
            for sell_order in self.state.sell_orders:
                sell_order.amount_usdt = per_tp
        
        self.logger.info(
            f"网格买入: #{order.grid_id} @ {fill_price:.2f}, "
            f"金额={order.amount_usdt:.2f} USDT"
        )
        
        trade_record = {
            "time": fill_time or int(time.time() * 1000),
            "side": "buy",
            "grid_id": order.grid_id,
            "price": fill_price,
            "amount_usdt": order.amount_usdt,
            "source": order.source,
            "pnl_usdt": 0,
            "pnl_pct": 0,
        }
        self.trade_history.append(trade_record)
        if len(self.trade_history) > 50:
            self.trade_history = self.trade_history[-50:]
        
        self._save_state()
        
        return {
            "action": "buy",
            "grid_id": order.grid_id,
            "price": fill_price,
            "amount_usdt": order.amount_usdt,
            "total_position": new_position,
            "avg_entry": self.state.avg_entry_price,
        }
    
    def check_sell_trigger(self, current_price: float) -> Optional[GridOrder]:
        """检查是否触发卖出"""
        if self.state is None or self.state.total_position_usdt <= 0:
            return None
        
        for order in self.state.sell_orders:
            if order.is_filled:
                continue
            if current_price >= order.price:
                return order
        
        return None
    
    def execute_sell(self, order: GridOrder, fill_price: float, fill_time: int = None) -> dict:
        """执行卖出"""
        order.is_filled = True
        order.fill_price = fill_price
        order.fill_time = fill_time
        
        pnl_pct = (fill_price - self.state.avg_entry_price) / self.state.avg_entry_price
        pnl_usdt = order.amount_usdt * pnl_pct
        
        self.state.total_position_usdt -= order.amount_usdt
        
        self.logger.info(
            f"网格止盈: #{order.grid_id} @ {fill_price:.2f}, "
            f"盈亏={pnl_usdt:.2f} USDT ({pnl_pct:.2%})"
        )
                
        trade_record = {
            "time": fill_time or int(time.time() * 1000),
            "side": "sell",
            "grid_id": order.grid_id,
            "price": fill_price,
            "amount_usdt": order.amount_usdt,
            "source": order.source,
            "pnl_usdt": pnl_usdt,
            "pnl_pct": pnl_pct * 100,
        }
        self.trade_history.append(trade_record)
        if len(self.trade_history) > 50:
            self.trade_history = self.trade_history[-50:]
        
        self._save_state()
        
        return {
            "action": "sell",
            "grid_id": order.grid_id,
            "price": fill_price,
            "amount_usdt": order.amount_usdt,
            "pnl_usdt": pnl_usdt,
            "pnl_pct": pnl_pct,
            "remaining_position": self.state.total_position_usdt,
        }

    # ============================================
    # 水位映射管理
    # ============================================

    def update_position_snapshot(self, holdings_contracts: float, avg_entry_price: float) -> None:
        """更新持仓快照"""
        if not self.state:
            return
        self.state.total_position_contracts = max(holdings_contracts, 0.0)
        self.state.avg_entry_price = max(avg_entry_price, 0.0)
    
    def build_level_mapping(self) -> Dict[int, int]:
        """
        构建逐级邻位映射表
        
        规则：每个支撑位映射到其上方第一个价格更高的水位（支撑位或阻力位均可）
        注意：不依赖 role 字段，直接使用 support_levels_state 判断支撑位身份
        """
        if not self.state:
            return {}
        
        # 获取支撑位 ID 集合（用于判断身份，不依赖 role 字段）
        support_level_ids = {lvl.level_id for lvl in self.state.support_levels_state}
        
        # 合并并按价格排序所有水位
        all_levels: List[GridLevelState] = (
            self.state.support_levels_state + self.state.resistance_levels_state
        )
        sorted_levels = sorted(all_levels, key=lambda x: x.price)
        
        mapping: Dict[int, int] = {}
        min_profit_pct = float(self.state.min_profit_pct or 0)
        missing_adjacent_levels: List[float] = []
        
        for i, level in enumerate(sorted_levels):
            # 使用 ID 集合判断是否为支撑位，而非 role 字段
            if level.level_id not in support_level_ids:
                continue
            
            min_sell_price = level.price * (1 + min_profit_pct)
            
            # 查找上方第一个价格满足最小利润要求的水位
            target_level = None
            for j in range(i + 1, len(sorted_levels)):
                candidate = sorted_levels[j]
                if candidate.price > min_sell_price:
                    target_level = candidate
                    break
            
            if target_level:
                mapping[level.level_id] = target_level.level_id
                self.logger.debug(
                    f"📍 映射: L_{level.level_id}({level.price:.2f}) → "
                    f"L_{target_level.level_id}({target_level.price:.2f})"
                )
            else:
                missing_adjacent_levels.append(level.price)
        
        if missing_adjacent_levels:
            self.logger.warning(
                f"⚠️ [Mapping] 以下支撑位无上方邻位: {missing_adjacent_levels}"
            )
        
        self.logger.info(
            f"📍 [Mapping] 构建完成: {len(mapping)} 个映射, "
            f"{len(missing_adjacent_levels)} 个无邻位"
        )
        
        return mapping
    
    def rebuild_level_mapping(self) -> None:
        """重建邻位映射"""
        if not self.state:
            return
        self.state.level_mapping = self.build_level_mapping()
        self._save_state()
    
    def _normalize_level_ids_and_rebuild_mapping(self) -> None:
        """规范化 level_id 并重建映射"""
        if not self.state:
            return
        
        RESISTANCE_ID_OFFSET = 1000
        needs_rebuild = False
        
        support_ids = {lvl.level_id for lvl in self.state.support_levels_state}
        resistance_ids = {lvl.level_id for lvl in self.state.resistance_levels_state}
        
        if self.state.resistance_levels_state:
            max_resistance_id = max(lvl.level_id for lvl in self.state.resistance_levels_state)
            if max_resistance_id < RESISTANCE_ID_OFFSET:
                for i, lvl in enumerate(self.state.resistance_levels_state):
                    lvl.level_id = RESISTANCE_ID_OFFSET + i + 1
                needs_rebuild = True
        
        overlap = support_ids & resistance_ids
        if overlap:
            for i, lvl in enumerate(self.state.resistance_levels_state):
                lvl.level_id = RESISTANCE_ID_OFFSET + i + 1
            needs_rebuild = True
        
        if needs_rebuild or not self.state.level_mapping:
            self.state.level_mapping = self.build_level_mapping()

    # ============================================
    # 逐级邻位同步
    # ============================================
    
    @staticmethod
    def price_matches(p1: float, p2: float, tolerance: float = PRICE_TOLERANCE) -> bool:
        """判断两个价格是否匹配"""
        if p2 == 0:
            return False
        return abs(p1 - p2) / p2 < tolerance
    
    def _get_level_by_id(self, level_id: int) -> Optional[GridLevelState]:
        """通过 level_id 查找水位"""
        if not self.state:
            return None
        for lvl in self.state.support_levels_state:
            if lvl.level_id == level_id:
                return lvl
        for lvl in self.state.resistance_levels_state:
            if lvl.level_id == level_id:
                return lvl
        return None
    
    def _index_orders_by_level(
        self,
        open_orders: List[Dict],
        side: str = "sell",
    ) -> Dict[int, List[Dict]]:
        """按水位索引交易所挂单"""
        if not self.state:
            return {}
        
        all_levels = self.state.support_levels_state + self.state.resistance_levels_state
        result: Dict[int, List[Dict]] = {}
        
        for order in open_orders:
            if order.get("side", "") != side:
                continue
            
            order_price = float(order.get("price", 0) or 0)
            if order_price <= 0:
                continue
            
            matched_level = None
            for lvl in all_levels:
                if self.price_matches(order_price, lvl.price):
                    matched_level = lvl
                    break
            
            if matched_level:
                result.setdefault(matched_level.level_id, []).append(order)
        
        return result
    
    def sync_mapping(
        self,
        current_price: float,
        open_orders: List[Dict],
        exchange_min_qty: float,
    ) -> List[Dict[str, Any]]:
        """逐级邻位映射同步"""
        if not self.state:
            return []
        
        actions: List[Dict[str, Any]] = []
        base_qty = float(self.state.base_amount_per_grid or 0)
        sell_quota_ratio = float(self.state.sell_quota_ratio or 0.7)
        
        sell_orders_by_level = self._index_orders_by_level(open_orders, side="sell")
        expected_sell_by_level: Dict[int, float] = {}
        
        for support_lvl in self.state.support_levels_state:
            fill_count = int(support_lvl.fill_counter or 0)
            if fill_count <= 0:
                continue
            
            target_level_id = self.state.level_mapping.get(support_lvl.level_id)
            if not target_level_id:
                continue
            
            contrib_qty = fill_count * base_qty * sell_quota_ratio
            expected_sell_by_level[target_level_id] = (
                expected_sell_by_level.get(target_level_id, 0) + contrib_qty
            )
        
        all_levels = self.state.support_levels_state + self.state.resistance_levels_state
        level_by_id = {lvl.level_id: lvl for lvl in all_levels}
        all_target_level_ids = set(expected_sell_by_level.keys()) | set(sell_orders_by_level.keys())
        
        for target_level_id in all_target_level_ids:
            target_lvl = level_by_id.get(target_level_id)
            if not target_lvl:
                continue
            
            expected_qty = expected_sell_by_level.get(target_level_id, 0)
            existing_orders = sell_orders_by_level.get(target_level_id, [])
            
            open_qty = sum(
                float(o.get("base_amount", 0) or 0) or 
                float(o.get("contracts", 0) or 0) * float(self.state.contract_size or 0)
                for o in existing_orders
            )
            
            placing_qty = 0.0
            if target_lvl.status == LevelStatus.PLACING:
                placing_qty = float(target_lvl.target_qty or 0)
            
            effective_pending = open_qty + placing_qty
            deficit = max(0, expected_qty - effective_pending)
            
            if deficit > 0 and deficit < exchange_min_qty:
                deficit = 0
            
            tolerance_threshold = max(exchange_min_qty, expected_qty * 0.05)
            
            if deficit >= tolerance_threshold:
                place_qty = max(deficit, exchange_min_qty)
                actions.append({
                    "action": "place",
                    "side": "sell",
                    "price": target_lvl.price,
                    "qty": place_qty,
                    "level_id": target_level_id,
                    "reason": "sync_mapping_deficit",
                })
                target_lvl.status = LevelStatus.PLACING
                target_lvl.target_qty = place_qty
                target_lvl.last_action_ts = int(time.time())
            
            elif expected_qty <= 0 and open_qty > 0:
                for order in existing_orders:
                    actions.append({
                        "action": "cancel",
                        "side": "sell",
                        "price": target_lvl.price,
                        "order_id": order.get("id", ""),
                        "level_id": target_level_id,
                        "reason": "sync_mapping_no_target",
                    })
                target_lvl.status = LevelStatus.CANCELING
                target_lvl.last_action_ts = int(time.time())
            
            elif expected_qty > 0 and abs(open_qty - expected_qty) > tolerance_threshold:
                for order in existing_orders:
                    actions.append({
                        "action": "cancel",
                        "side": "sell",
                        "price": target_lvl.price,
                        "order_id": order.get("id", ""),
                        "level_id": target_level_id,
                        "reason": "sync_mapping_rebalance",
                    })
                target_lvl.status = LevelStatus.CANCELING
                target_lvl.last_action_ts = int(time.time())
            
            else:
                if existing_orders:
                    target_lvl.status = LevelStatus.ACTIVE
                    target_lvl.active_order_id = existing_orders[0].get("id", "")
                    target_lvl.open_qty = open_qty
        
        return actions

    # ============================================
    # 持仓清单管理 (SELL_MAPPING.md Section 7)
    # ============================================

    def find_level_index_for_price(
        self,
        price: float,
        levels: Optional[List[GridLevelState]] = None,
        tolerance: float = 0.005  # 0.5% 容差
    ) -> int:
        """
        根据成交价确定归属的水位索引 (SELL_MAPPING.md Section 7.4)
        
        Args:
            price: 成交价格
            levels: 支撑位列表（按价格降序）
            tolerance: 价格匹配容差（默认 0.5%）
        
        Returns:
            归属的水位索引（0=支撑位1, 1=支撑位2...）
        """
        if levels is None:
            levels = self.state.support_levels_state if self.state else []
        
        if not levels:
            return 0
        
        # 优先精确匹配（容差内）
        for i, level in enumerate(levels):
            if abs(price - level.price) / level.price < tolerance:
                return i
        
        # 兜底：找最近的低于成交价的水位
        candidates = [(i, lvl) for i, lvl in enumerate(levels) if lvl.price <= price]
        if candidates:
            # 取最近的（价格最高的）
            return max(candidates, key=lambda x: x[1].price)[0]
        
        # 极端情况：成交价低于所有水位
        return len(levels) - 1

    def get_level_for_fill(
        self,
        fill: ActiveFill,
        levels: Optional[List[GridLevelState]] = None
    ) -> Optional[GridLevelState]:
        """
        根据索引获取归属水位，处理越界 (SELL_MAPPING.md Section 7.4)
        
        规则 3（索引越界兜底）：
        - 若 level_index < len(levels): 返回对应水位
        - 若越界: 返回最后一个水位
        
        Args:
            fill: 持仓记录
            levels: 支撑位列表
        
        Returns:
            归属的水位，若无水位则返回 None
        """
        if levels is None:
            levels = self.state.support_levels_state if self.state else []
        
        if not levels:
            return None
        
        # 索引越界兜底
        idx = min(fill.level_index, len(levels) - 1)
        
        if fill.level_index >= len(levels):
            self.logger.debug(
                f"📦 [Inventory] level_index={fill.level_index} 越界, "
                f"兜底到 index={idx}"
            )
        
        return levels[idx]

    def get_effective_index(
        self,
        fill: ActiveFill,
        levels: Optional[List[GridLevelState]] = None
    ) -> int:
        """
        获取有效索引（考虑越界兜底）
        
        Args:
            fill: 持仓记录
            levels: 支撑位列表
        
        Returns:
            有效的水位索引
        """
        if levels is None:
            levels = self.state.support_levels_state if self.state else []
        
        if not levels:
            return 0
        
        return min(fill.level_index, len(levels) - 1)

    def get_level_index_by_level_id(
        self,
        level_id: int,
        levels: Optional[List[GridLevelState]] = None
    ) -> Optional[int]:
        """
        根据 level_id 获取当前水位索引
        
        仅用于运行时从水位列表推导索引（不持久化）。
        """
        if levels is None:
            levels = self.state.support_levels_state if self.state else []
        
        for i, level in enumerate(levels):
            if level.level_id == level_id:
                return i
        
        return None

    def verify_inventory_consistency(
        self,
        levels: Optional[List[GridLevelState]] = None
    ) -> bool:
        """
        校验 fill_counter 与 inventory 一致性 (SELL_MAPPING.md 规则 7)
        
        若不一致，以 inventory 为准修正 fill_counter
        
        Returns:
            True 如果一致，False 如果进行了修正
        """
        if not self.state:
            return True
        
        if levels is None:
            levels = self.state.support_levels_state
        
        is_consistent = True
        
        for i, level in enumerate(levels):
            # 计算 inventory 中归属到此索引的记录数
            actual_count = sum(
                1 for f in self.state.active_inventory 
                if self.get_effective_index(f, levels) == i
            )
            
            if actual_count != level.fill_counter:
                self.logger.warning(
                    f"⚠️ [Consistency] index={i} 不一致: "
                    f"inventory={actual_count}, fill_counter={level.fill_counter}, "
                    f"以 inventory 为准修正"
                )
                level.fill_counter = actual_count
                is_consistent = False
        
        if not is_consistent:
            self._save_state()
        
        return is_consistent

    def validate_and_rebuild_inventory(
        self,
        recent_trades: List[Dict],
        local_trades: List[Dict],
        expected_count: int,
        base_qty: float
    ) -> tuple:
        """
        校验并重建持仓清单 (SELL_MAPPING.md Section 7.4)
        
        规则 1：订单有效性校验
        规则 2：索引归属原则
        
        Args:
            recent_trades: 交易所成交历史（buy 方向）
            local_trades: 本地成交账本（trades.jsonl）
            expected_count: 期望的持仓记录数（基于持仓量计算）
            base_qty: 每格基础数量
        
        Returns:
            (重建后的 active_inventory, 是否发生了重建)
        """
        if not self.state:
            return [], False
        
        levels = self.state.support_levels_state
        
        # Step 1: 合并成交记录
        all_trades = self._merge_trades(recent_trades, local_trades)
        valid_order_ids = {
            str(t.get("order_id") or t.get("id", "")) 
            for t in all_trades 
            if t.get("side") == "buy"
        }
        
        # Step 2: 校验现有记录的订单有效性
        current_inventory = self.state.active_inventory
        invalid_records = [
            fill for fill in current_inventory 
            if fill.order_id and fill.order_id not in valid_order_ids
        ]
        
        # Step 3: 若全部有效且数量匹配，无需重建
        if not invalid_records and len(current_inventory) == expected_count:
            return current_inventory, False
        
        # Step 4: 触发完全重建
        self.logger.warning(
            f"⚠️ [Inventory] 检测到 {len(invalid_records)} 条无效记录，"
            f"触发完全重建 (expected={expected_count})"
        )
        
        # Step 5: 从成交记录重建
        new_inventory = []
        buy_trades = sorted(
            [t for t in all_trades if t.get("side") == "buy"],
            key=lambda x: x.get("timestamp", 0),
            reverse=True  # 最新在前
        )
        
        for trade in buy_trades:
            if len(new_inventory) >= expected_count:
                break
            
            order_id = str(trade.get("order_id") or trade.get("id", ""))
            price = float(trade.get("price", 0))
            qty = float(trade.get("amount") or trade.get("qty", base_qty))
            timestamp = int(trade.get("timestamp", 0))
            
            # 优先使用 trade 中的 level_index（不依赖旧数据）
            trade_level_index = trade.get("level_index")
            if trade_level_index is not None:
                level_index = max(0, int(trade_level_index))
                self.logger.debug(
                    f"📌 [Inventory] 使用原始 level_index={trade_level_index}"
                )
            else:
                # 无 level_index，才用价格计算
                level_index = self.find_level_index_for_price(price, levels)
                self.logger.debug(
                    f"📐 [Inventory] 根据价格计算 price={price} → level_index={level_index}"
                )
            
            new_fill = ActiveFill(
                order_id=order_id,
                price=price,
                qty=qty,
                timestamp=timestamp // 1000 if timestamp > 1e12 else timestamp,
                level_index=level_index
            )
            new_inventory.append(new_fill)
            
            self.logger.info(
                f"➕ [Inventory] 新增持仓: order_id={order_id}, "
                f"price={price}, level_index={level_index}"
            )
        
        # Step 6: 若仍不足，兜底按水位填充
        if len(new_inventory) < expected_count:
            self.logger.warning(
                f"⚠️ [Inventory] 成交记录不足，兜底填充 "
                f"({len(new_inventory)} < {expected_count})"
            )
            new_inventory = self._fallback_fill_by_levels(
                new_inventory, 
                expected_count, 
                base_qty
            )
        
        self.logger.info(
            f"🔄 [Inventory] 重建完成: {len(new_inventory)} 条记录"
        )
        
        return new_inventory, True

    def _merge_trades(
        self,
        recent_trades: List[Dict],
        local_trades: List[Dict]
    ) -> List[Dict]:
        """
        合并交易所和本地成交记录
        
        合并规则：
        - 相同 order_id 的记录合并
        - 交易所数据优先（price, amount 等）
        - 但保留本地记录的 level_id（用于索引继承）
        """
        merged = {}
        
        # 先加载本地记录（包含 level_id）
        local_level_ids = {}
        for t in local_trades:
            order_id = str(t.get("order_id") or t.get("id", ""))
            if order_id:
                merged[order_id] = t
                # 保存本地记录的 level_id
                if t.get("level_id") is not None:
                    local_level_ids[order_id] = t.get("level_id")
        
        # 交易所记录覆盖本地，但保留 level_id
        for t in recent_trades:
            order_id = str(t.get("order_id") or t.get("id", ""))
            if order_id:
                # 如果本地有 level_id，保留它
                if order_id in local_level_ids and t.get("level_id") is None:
                    t = dict(t)  # 复制以避免修改原始数据
                    t["level_id"] = local_level_ids[order_id]
                merged[order_id] = t
        
        return list(merged.values())

    def _fallback_fill_by_levels(
        self,
        current_inventory: List[ActiveFill],
        expected_count: int,
        base_qty: float
    ) -> List[ActiveFill]:
        """兜底按水位填充"""
        if not self.state:
            return current_inventory
        
        levels = self.state.support_levels_state
        new_inventory = list(current_inventory)
        added = 0
        
        for i, level in enumerate(levels):
            while len(new_inventory) < expected_count:
                # 检查该索引是否已达到 max_fill_per_level
                level_count = sum(
                    1 for f in new_inventory 
                    if self.get_effective_index(f, levels) == i
                )
                if level_count >= int(self.state.max_fill_per_level or 1):
                    break
                
                new_fill = ActiveFill(
                    order_id=f"recon_{int(time.time())}_{added}",
                    price=level.price,
                    qty=base_qty,
                    timestamp=int(time.time()),
                    level_index=i
                )
                new_inventory.append(new_fill)
                added += 1
                
                self.logger.warning(
                    f"⚠️ [Inventory] 兜底填充: level_index={i}, "
                    f"price={level.price}, order_id={new_fill.order_id}"
                )
        
        return new_inventory

    def clear_fill_counters(self, reason: str = "manual") -> None:
        """清空持仓清单"""
        if not self.state:
            return
        self.state.active_inventory = []
        self.state.settled_inventory = []
        for lvl in self.state.support_levels_state:
            lvl.fill_counter = 0
        self.logger.info("🧹 fill_counter & Inventory 清零: reason=%s", reason)
        self._save_state()

    def reconcile_counters_with_position(
        self,
        current_price: float,
        holdings_btc: float,
        recent_trades: Optional[List[Dict]] = None,
        local_trades: Optional[List[Dict]] = None,
    ) -> Optional[Dict[str, str]]:
        """
        对账持仓清单与实际持仓 (SELL_MAPPING.md Section 7)
        
        核心逻辑：
        1. 校验订单有效性（规则 1）
        2. 使用索引归属原则（规则 2）
        3. 校验 fill_counter 一致性（规则 7）
        
        Args:
            current_price: 当前价格
            holdings_btc: 实际持仓量（BTC）
            recent_trades: 交易所成交历史
            local_trades: 本地成交账本（trades.jsonl）
        
        Returns:
            对账结果描述
        """
        if not self.state:
            return None
        base_qty = float(self.state.base_amount_per_grid or 0)
        if base_qty <= 0:
            return None
        
        holdings_btc = max(float(holdings_btc or 0), 0.0)
        locked_qty = float(self.state.base_position_locked or 0)
        grid_holdings = max(holdings_btc - locked_qty, 0.0)
        
        expected = int(round(grid_holdings / base_qty))
        current = len(self.state.active_inventory)
        
        # 持仓为 0 时清空
        if holdings_btc == 0:
            if current > 0:
                self.clear_fill_counters("auto_clear_zero_position")
                return {"action": "auto_clear", "detail": "持仓为 0，已清空清单"}
            return None
        
        # 使用新的校验和重建逻辑
        new_inventory, was_rebuilt = self.validate_and_rebuild_inventory(
            recent_trades=recent_trades or [],
            local_trades=local_trades or [],
            expected_count=expected,
            base_qty=base_qty
        )
        
        if was_rebuilt:
            self.state.active_inventory = new_inventory
            self._update_fill_counters_from_inventory()
            self._save_state()
            
            # 校验一致性（规则 7）
            self.verify_inventory_consistency()
            
            return {
                "action": "rebuild",
                "detail": f"重建完成, final_count={len(new_inventory)}, expected={expected}",
            }
        
        # 数量不匹配时的补齐/移除
        if current != expected:
            if current < expected:
                # 补齐
                diff = expected - current
                added = 0
                levels = self.state.support_levels_state
                
                # 优先从成交记录补齐
                if recent_trades:
                    existing_ids = {f.order_id for f in self.state.active_inventory if f.order_id}
                    for t in recent_trades:
                        if added >= diff:
                            break
                        if t.get("side") != "buy":
                            continue
                        order_id = str(t.get("order_id") or t.get("id", ""))
                        if order_id in existing_ids:
                            continue
                        price = float(t.get("price", 0) or 0)
                        level_index = self.find_level_index_for_price(price, levels)
                        
                        # 检查该索引是否已达到 max_fill_per_level
                        lvl_count = sum(
                            1 for f in self.state.active_inventory 
                            if self.get_effective_index(f, levels) == level_index
                        )
                        if lvl_count < int(self.state.max_fill_per_level or 1):
                            new_fill = ActiveFill(
                                order_id=order_id,
                                price=price,
                                qty=float(t.get("amount", base_qty)),
                                timestamp=int(t.get("timestamp", time.time()*1000) / 1000),
                                level_index=level_index
                            )
                            self.state.active_inventory.append(new_fill)
                            existing_ids.add(order_id)
                            added += 1
                
                # 兜底按水位填充
                if added < diff:
                    self.state.active_inventory = self._fallback_fill_by_levels(
                        self.state.active_inventory,
                        expected,
                        base_qty
                    )
                
            elif current > expected:
                # FIFO 移除
                diff = current - expected
                for _ in range(diff):
                    if self.state.active_inventory:
                        self.state.active_inventory.pop(0)
            
            self._update_fill_counters_from_inventory()
            self._save_state()
            
            # 校验一致性（规则 7）
            self.verify_inventory_consistency()
            
            return {
                "action": "reconcile",
                "detail": f"synced_inventory, final_count={len(self.state.active_inventory)}, expected={expected}",
            }
        
        # 数量匹配，校验一致性
        self.verify_inventory_consistency()
        return None

    def _btc_to_contracts(self, btc_qty: float, exchange_min_qty: float = 0.0) -> float:
        """BTC 转合约张数"""
        if not self.state or btc_qty <= 0:
            return 0.0
        contract_size = float(getattr(self.state, "contract_size", 0) or 0)
        if contract_size > 0:
            import math
            contracts = math.ceil(btc_qty / contract_size)
        else:
            contracts = btc_qty
        if exchange_min_qty:
            import math
            contracts = max(contracts, math.ceil(exchange_min_qty))
        return float(contracts)

    def compute_total_sell_qty(self, current_holdings: float) -> float:
        """计算止盈总量"""
        if not self.state:
            return 0.0
        base_locked = max(self.state.base_position_locked, 0.0)
        tradable = max(current_holdings - base_locked, 0.0)
        return tradable * self.state.sell_quota_ratio

    def allocate_sell_targets(
        self,
        total_sell_qty: float,
        base_amount_per_grid: float,
        min_order_qty: float,
        levels_count: Optional[int] = None,
    ) -> List[float]:
        """瀑布流分配止盈数量"""
        if total_sell_qty <= 0 or not self.state:
            return []
        targets: List[float] = []
        q_rem = total_sell_qty
        max_levels = levels_count if levels_count is not None else len(self.state.resistance_levels_state)
        while q_rem > 0 and len(targets) < max_levels:
            q = min(q_rem, base_amount_per_grid)
            targets.append(q)
            q_rem -= q
        if q_rem > 0 and targets:
            targets[-1] += q_rem

        for i in range(len(targets) - 1, -1, -1):
            if targets[i] < min_order_qty:
                if i > 0:
                    targets[i - 1] += targets[i]
                targets[i] = 0.0
        return targets

    # ============================================
    # Recon 动作构建
    # ============================================

    def build_recon_actions(
        self,
        current_price: float,
        open_orders: List[Dict],
        exchange_min_qty_btc: float,
    ) -> List[Dict[str, Any]]:
        """生成 Recon 挂/撤单动作"""
        if not self.state:
            return []

        actions: List[Dict[str, Any]] = []
        price_tol = 0.0001 

        order_by_price: Dict[str, Dict[float, List[Dict]]] = {}
        for o in open_orders:
            price = float(o.get("price", 0) or 0)
            if price <= 0:
                continue
            side = o.get("side", "")
            order_by_price.setdefault(side, {}).setdefault(price, []).append(o)

        def _match_orders(side: str, price: float) -> List[Dict]:
            matches: List[Dict] = []
            for p, orders in order_by_price.get(side, {}).items():
                if abs(p - price) <= price * price_tol:
                    matches.extend(orders)
            return matches

        def _sum_open_qty(orders: List[Dict]) -> float:
            total_qty = 0.0
            for o in orders:
                qty = float(o.get("base_amount", 0) or 0)
                if qty <= 0:
                    qty = float(o.get("contracts", 0) or 0) * float(self.state.contract_size or 0)
                total_qty += qty
            return total_qty

        # 动态角色判定（基于价格位置分类，不修改原对象的 role/side 字段）
        # 只有支撑位列表中价格低于当前价的才作为买入候选
        # 避免污染 GridLevelState 的持久化字段
        buy_levels = [
            lvl for lvl in self.state.support_levels_state 
            if lvl.price < current_price
        ]
        # 阻力位列表中价格高于当前价的作为卖出候选（但卖单通过 sync_mapping 处理）
        sell_levels = [
            lvl for lvl in self.state.resistance_levels_state 
            if lvl.price > current_price
        ]
        all_levels = self.state.support_levels_state + self.state.resistance_levels_state

        # 买单处理
        for lvl in buy_levels:
            existing_orders = _match_orders("buy", lvl.price)
            if existing_orders:
                lvl.status = LevelStatus.ACTIVE
                lvl.order_id = existing_orders[0].get("id", "")
                lvl.active_order_id = lvl.order_id
                lvl.open_qty = _sum_open_qty(existing_orders)
                if int(lvl.fill_counter or 0) >= int(self.state.max_fill_per_level or 1):
                    for existing in existing_orders:
                        actions.append({
                            "action": "cancel",
                            "side": "buy",
                            "price": lvl.price,
                            "order_id": existing.get("id", ""),
                            "level_id": lvl.level_id,
                            "reason": "fill_counter_limit",
                        })
                    lvl.status = LevelStatus.CANCELING
                    lvl.last_action_ts = int(time.time())
                    continue
                target_qty = max(self.state.base_amount_per_grid, exchange_min_qty_btc)
                diff = abs(lvl.open_qty - target_qty)
                is_diff_significant = diff >= exchange_min_qty_btc and (diff / target_qty > 0.05 if target_qty > 0 else True)
                
                if is_diff_significant:
                    for existing in existing_orders:
                        actions.append({
                            "action": "cancel",
                            "side": "buy",
                            "price": lvl.price,
                            "order_id": existing.get("id", ""),
                            "level_id": lvl.level_id,
                            "reason": "rebalance_qty",
                        })
                    lvl.status = LevelStatus.CANCELING
                    lvl.last_action_ts = int(time.time())
                continue
            
            existing_sells = _match_orders("sell", lvl.price)
            if existing_sells:
                for existing_sell in existing_sells:
                    actions.append({
                        "action": "cancel",
                        "side": "sell",
                        "price": lvl.price,
                        "order_id": existing_sell.get("id", ""),
                        "level_id": lvl.level_id,
                        "reason": "polarity_flip_cancel_sell",
                    })
                lvl.status = LevelStatus.CANCELING
                lvl.last_action_ts = int(time.time())
                continue
            
            if lvl.status == LevelStatus.ACTIVE:
                lvl.status = LevelStatus.IDLE
                lvl.order_id = ""
                lvl.open_qty = 0.0

            if lvl.status in (LevelStatus.PLACING, LevelStatus.CANCELING) and lvl.last_action_ts:
                if time.time() - (lvl.last_action_ts or 0) > self.state.order_action_timeout_sec:
                    lvl.status = LevelStatus.IDLE
                    lvl.last_error = "action_timeout"

            if lvl.status == LevelStatus.IDLE:
                if lvl.fill_counter >= self.state.max_fill_per_level:
                    pass
                elif current_price > lvl.price * (1 + self.state.buy_price_buffer_pct):
                    qty = max(self.state.base_amount_per_grid, exchange_min_qty_btc)
                    actions.append({
                        "action": "place",
                        "side": "buy",
                        "price": lvl.price,
                        "qty": qty,
                        "level_id": lvl.level_id,
                        "reason": "recon_buy_sync",
                    })
                    lvl.status = LevelStatus.PLACING
                    lvl.target_qty = qty
                    lvl.last_action_ts = int(time.time())
            elif lvl.status in (LevelStatus.PLACING, LevelStatus.CANCELING):
                if lvl.last_action_ts and (time.time() - lvl.last_action_ts) > self.state.order_action_timeout_sec:
                    lvl.status = LevelStatus.IDLE
                    lvl.last_error = "action_timeout"

        # 孤儿买单清理
        buy_level_prices = {lvl.price for lvl in buy_levels}
        for order_price, orders in order_by_price.get("buy", {}).items():
            is_matched = any(
                abs(order_price - lvl_price) <= lvl_price * price_tol
                for lvl_price in buy_level_prices
            )
            if not is_matched:
                for orphan_order in orders:
                    actions.append({
                        "action": "cancel",
                        "side": "buy",
                        "price": order_price,
                        "order_id": orphan_order.get("id", ""),
                        "level_id": 0,
                        "reason": "orphan_order_cleanup",
                    })

        # 卖单同步（使用逐级邻位映射）
        sell_actions = self.sync_mapping(
            current_price=current_price,
            open_orders=open_orders,
            exchange_min_qty=exchange_min_qty_btc,
        )
        actions.extend(sell_actions)
        
        # 孤儿卖单清理
        all_level_prices = {lvl.price for lvl in all_levels}
        for order_price, orders in order_by_price.get("sell", {}).items():
            is_matched = any(
                abs(order_price - lvl_price) <= lvl_price * price_tol
                for lvl_price in all_level_prices
            )
            if not is_matched:
                for orphan_order in orders:
                    actions.append({
                        "action": "cancel",
                        "side": "sell",
                        "price": order_price,
                        "order_id": orphan_order.get("id", ""),
                        "level_id": 0,
                        "reason": "orphan_order_cleanup",
                    })

        return actions

    def build_event_sell_increment(
        self,
        delta_buy_qty: float,
        exchange_min_qty_btc: float,
        current_price: float,
        filled_support_level_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """买单成交后增量补卖单"""
        if not self.state or delta_buy_qty <= 0:
            return []
        
        delta_sell = delta_buy_qty * self.state.sell_quota_ratio
        if delta_sell < exchange_min_qty_btc:
            return []

        target_level = None
        
        if filled_support_level_id:
            target_level_id = self.state.level_mapping.get(filled_support_level_id)
            if target_level_id:
                target_level = self._get_level_by_id(target_level_id)
        
        if not target_level:
            recent_fill = None
            for lvl in sorted(self.state.support_levels_state, key=lambda x: x.price, reverse=True):
                if lvl.fill_counter > 0 and lvl.price < current_price:
                    recent_fill = lvl
                    break
            
            if recent_fill:
                target_level_id = self.state.level_mapping.get(recent_fill.level_id)
                if target_level_id:
                    target_level = self._get_level_by_id(target_level_id)
        
        if not target_level:
            all_levels = self.state.support_levels_state + self.state.resistance_levels_state
            candidates = [lvl for lvl in all_levels if lvl.price > current_price]
            if candidates:
                target_level = min(candidates, key=lambda x: x.price)
        
        if not target_level:
            return []
        
        if current_price >= target_level.price * (1 - self.state.sell_price_buffer_pct):
            return []
        
        return [{
            "action": "place",
            "side": "sell",
            "price": target_level.price,
            "qty": delta_sell,
            "level_id": target_level.level_id,
            "reason": "event_sell_mapping",
        }]

    def _find_support_level_for_price(self, price: float) -> Optional[GridLevelState]:
        """根据价格查找支撑位"""
        if not self.state:
            return None
        price = float(price or 0)
        if price <= 0:
            return None
        price_tol = 0.001
        for lvl in self.state.support_levels_state:
            if abs(lvl.price - price) <= lvl.price * price_tol:
                return lvl
        candidates = [lvl for lvl in self.state.support_levels_state if lvl.price < price]
        if not candidates:
            return None
        return max(candidates, key=lambda x: x.price)

    def increment_fill_counter_by_order(self, order_id: str, buy_price: float, buy_qty: float) -> bool:
        """记录新买入成交"""
        if not self.state:
            return False
        order_id = str(order_id or "").strip()
        if not order_id:
            return False
        
        matched_lvl = None
        for lvl in self.state.support_levels_state:
            if lvl.order_id == order_id or lvl.active_order_id == order_id:
                matched_lvl = lvl
                break
        
        if not matched_lvl:
            matched_lvl = self._find_support_level_for_price(buy_price)
            
        if not matched_lvl:
            return False

        # 计算 level_index（索引归属原则）
        level_index = self.get_level_index_by_level_id(matched_lvl.level_id)
        if level_index is None:
            level_index = self.find_level_index_for_price(buy_price, self.state.support_levels_state)

        new_fill = ActiveFill(
            order_id=order_id,
            price=buy_price,
            qty=buy_qty,
            timestamp=int(time.time()),
            level_index=level_index
        )
        self.state.active_inventory.append(new_fill)
        self._update_fill_counters_from_inventory()
        self._save_state()
        return True

    def _update_fill_counters_from_inventory(self) -> None:
        """
        从清单同步计数器 (SELL_MAPPING.md 规则 7)
        
        使用 level_index 而非 level_id 进行归属计算
        """
        if not self.state:
            return
        
        levels = self.state.support_levels_state
        
        # 重置所有计数器
        for lvl in levels:
            lvl.fill_counter = 0
        
        # 根据 level_index 计算归属（考虑越界兜底）
        for fill in self.state.active_inventory:
            effective_idx = self.get_effective_index(fill, levels)
            if effective_idx < len(levels):
                levels[effective_idx].fill_counter += 1

    def release_fill_counter_by_qty(self, sell_qty: float) -> None:
        """卖出后释放持仓记录"""
        if not self.state or not self.state.active_inventory:
            return
            
        base_qty = float(self.state.base_amount_per_grid or 0)
        if base_qty <= 0:
            return
            
        sell_qty = max(float(sell_qty or 0), 0.0)
        count = int(round(sell_qty / base_qty))
        if count <= 0:
            count = 1
            
        for _ in range(count):
            if self.state.active_inventory:
                removed = self.state.active_inventory.pop(0)
                self.state.settled_inventory.insert(0, removed)
                if len(self.state.settled_inventory) > 10:
                    self.state.settled_inventory = self.state.settled_inventory[:10]
                
        if count > 0:
            self._update_fill_counters_from_inventory()
            self._save_state()
    
    # ============================================
    # 止损管理
    # ============================================
    
    def check_stop_loss(self, current_price: float) -> bool:
        """检查是否触发止损"""
        if self.state is None:
            return False
        return current_price <= self.state.grid_floor
    
    def execute_stop_loss(self, fill_price: float) -> dict:
        """执行止损"""
        if self.state is None or self.state.total_position_usdt <= 0:
            return {"action": "stop_loss", "status": "no_position"}
        
        pnl_pct = (fill_price - self.state.avg_entry_price) / self.state.avg_entry_price
        pnl_usdt = self.state.total_position_usdt * pnl_pct
        
        result = {
            "action": "stop_loss",
            "price": fill_price,
            "amount_usdt": self.state.total_position_usdt,
            "pnl_usdt": pnl_usdt,
            "pnl_pct": pnl_pct,
            "grid_floor": self.state.grid_floor,
        }
        
        trade_record = {
            "time": int(time.time() * 1000),
            "side": "stop_loss",
            "grid_id": 0,
            "price": fill_price,
            "amount_usdt": self.state.total_position_usdt,
            "source": "grid_floor",
            "pnl_usdt": pnl_usdt,
            "pnl_pct": pnl_pct * 100,
        }
        self.trade_history.append(trade_record)
        
        self.state.total_position_usdt = 0
        self.state.avg_entry_price = 0
        
        self._save_state()
        return result
    
    def update_pnl(self, current_price: float):
        """更新未实现盈亏"""
        if self.state is None or self.state.total_position_usdt <= 0:
            return
        pnl_pct = (current_price - self.state.avg_entry_price) / self.state.avg_entry_price
        self.state.unrealized_pnl = self.state.total_position_usdt * pnl_pct
    
    def get_summary(self, current_price: float) -> dict:
        """获取网格摘要"""
        if self.state is None:
            return {"has_grid": False}
        
        self.update_pnl(current_price)
        
        filled_buys = sum(1 for o in self.state.buy_orders if o.is_filled)
        filled_sells = sum(1 for o in self.state.sell_orders if o.is_filled)
        
        return {
            "has_grid": True,
            "symbol": self.state.symbol,
            "current_price": current_price,
            "upper_price": self.state.upper_price,
            "lower_price": self.state.lower_price,
            "grid_floor": self.state.grid_floor,
            "total_position_usdt": self.state.total_position_usdt,
            "avg_entry_price": self.state.avg_entry_price,
            "unrealized_pnl": self.state.unrealized_pnl,
            "buy_orders_filled": f"{filled_buys}/{len(self.state.buy_orders)}",
            "sell_orders_filled": f"{filled_sells}/{len(self.state.sell_orders)}",
            "distance_to_floor": (current_price - self.state.grid_floor) / current_price,
        }
    
    def reset(self):
        """重置网格"""
        self.state = None
        self._save_state()
    
    # ============================================
    # 持久化
    # ============================================
    
    def _save_state(self) -> None:
        """保存状态"""
        try:
            payload: Dict = {"trade_history": self.trade_history}
            if self.state:
                payload["grid_state"] = self.state.to_dict()
            else:
                payload["grid_state"] = None
            
            with self.state_file.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.error(f"保存网格状态失败: {e}", exc_info=True)
    
    def restore_state(self, current_price: float, price_tolerance: float = 0.02) -> bool:
        """恢复网格状态"""
        if not self.state_file.exists():
            return False
        
        try:
            with self.state_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            self.logger.error(f"读取网格状态失败: {e}", exc_info=True)
            return False
        
        try:
            grid_data = data.get("grid_state")
            self.trade_history = data.get("trade_history", [])
            
            if not grid_data:
                return False
            
            buy_orders = [
                GridOrder(
                    grid_id=o.get("grid_id", 0),
                    price=o.get("price", 0.0),
                    amount_usdt=o.get("amount_usdt", 0.0),
                    strength=o.get("strength", 0.0),
                    source=o.get("source", ""),
                    is_filled=o.get("is_filled", False),
                    fill_price=o.get("fill_price"),
                    fill_time=o.get("fill_time"),
                )
                for o in grid_data.get("buy_orders", [])
            ]
            sell_orders = [
                GridOrder(
                    grid_id=o.get("grid_id", 0),
                    price=o.get("price", 0.0),
                    amount_usdt=o.get("amount_usdt", 0.0),
                    strength=o.get("strength", 0.0),
                    source=o.get("source", ""),
                    is_filled=o.get("is_filled", False),
                    fill_price=o.get("fill_price"),
                    fill_time=o.get("fill_time"),
                )
                for o in grid_data.get("sell_orders", [])
            ]
            
            restored_state = GridState(
                symbol=grid_data.get("symbol", self.symbol),
                direction=grid_data.get("direction", "long"),
                state_version=STATE_VERSION,
                upper_price=grid_data.get("upper_price", 0.0),
                lower_price=grid_data.get("lower_price", 0.0),
                grid_floor=grid_data.get("grid_floor", 0.0),
                buy_orders=buy_orders,
                sell_orders=sell_orders,
                support_levels_state=[
                    GridLevelState.from_dict(s) for s in grid_data.get("support_levels_state", [])
                ],
                resistance_levels_state=[
                    GridLevelState.from_dict(r) for r in grid_data.get("resistance_levels_state", [])
                ],
                retired_levels=[
                    GridLevelState.from_dict(r) for r in grid_data.get("retired_levels", [])
                ],
                active_inventory=[
                    ActiveFill.from_dict(f) for f in grid_data.get("active_inventory", [])
                ],
                settled_inventory=[
                    ActiveFill.from_dict(f) for f in grid_data.get("settled_inventory", [])
                ],
                # JSON 的键总是字符串，需要转换为整数
                level_mapping={
                    int(k): v for k, v in grid_data.get("level_mapping", {}).items()
                },
                per_grid_contracts=grid_data.get("per_grid_contracts", 0),
                contract_size=grid_data.get("contract_size", 0.0001),
                num_grids=grid_data.get("num_grids", 0),
                sell_quota_ratio=grid_data.get("sell_quota_ratio", self.grid_config.sell_quota_ratio),
                min_profit_pct=grid_data.get("min_profit_pct", self.grid_config.min_profit_pct),
                buy_price_buffer_pct=grid_data.get("buy_price_buffer_pct", self.grid_config.buy_price_buffer_pct),
                sell_price_buffer_pct=grid_data.get("sell_price_buffer_pct", self.grid_config.sell_price_buffer_pct),
                base_amount_per_grid=grid_data.get("base_amount_per_grid", self.grid_config.base_amount_per_grid),
                base_position_locked=grid_data.get("base_position_locked", self.grid_config.base_position_locked),
                max_fill_per_level=int(grid_data.get("max_fill_per_level", self.grid_config.max_fill_per_level) or 1),
                recon_interval_sec=grid_data.get("recon_interval_sec", self.grid_config.recon_interval_sec),
                order_action_timeout_sec=grid_data.get("order_action_timeout_sec", self.grid_config.order_action_timeout_sec),
                anchor_price=grid_data.get("anchor_price", 0.0),
                anchor_ts=grid_data.get("anchor_ts", 0),
                total_position_usdt=grid_data.get("total_position_usdt", 0.0),
                avg_entry_price=grid_data.get("avg_entry_price", 0.0),
                unrealized_pnl=grid_data.get("unrealized_pnl", 0.0),
                total_position_contracts=grid_data.get("total_position_contracts", 0.0),
                resistance_levels=grid_data.get("resistance_levels", []),
                support_levels=grid_data.get("support_levels", []),
            )

            # 覆盖配置参数
            if restored_state.base_amount_per_grid != self.grid_config.base_amount_per_grid:
                restored_state.base_amount_per_grid = self.grid_config.base_amount_per_grid
            if self.grid_config.base_position_locked > 0:
                restored_state.base_position_locked = self.grid_config.base_position_locked
            if restored_state.max_fill_per_level != self.grid_config.max_fill_per_level:
                restored_state.max_fill_per_level = self.grid_config.max_fill_per_level
            
            # 价格校验
            if current_price > 0 and restored_state.lower_price > 0 and restored_state.upper_price > 0:
                below_ok = current_price >= restored_state.lower_price * (1 - price_tolerance)
                above_ok = current_price <= restored_state.upper_price * (1 + price_tolerance)
                if not (below_ok and above_ok):
                    self.logger.warning("恢复状态失败: 当前价偏离网格区间")
                    return False
            
            self.state = restored_state
            self._normalize_level_ids_and_rebuild_mapping()
            self._save_state()
            return True
        except Exception as e:
            self.logger.error(f"恢复网格状态失败: {e}", exc_info=True)
            return False
    
    def clear_state_file(self) -> None:
        """删除状态文件"""
        try:
            if self.state_file.exists():
                self.state_file.unlink()
        except Exception:
            pass
    
    # ============================================
    # 兼容层
    # ============================================
    
    @property
    def resistance_calc(self):
        """兼容: 返回阻力计算器"""
        from key_level_grid.analysis.resistance import ResistanceCalculator
        from key_level_grid.core.config import ResistanceConfig as CalcResistanceConfig
        if not hasattr(self, '_resistance_calc'):
            calc_config = CalcResistanceConfig(
                swing_lookbacks=self.resistance_config.swing_lookbacks,
                fib_ratios=self.resistance_config.fib_ratios,
                merge_tolerance=self.resistance_config.merge_tolerance,
                min_distance_pct=self.resistance_config.min_distance_pct,
                max_distance_pct=self.resistance_config.max_distance_pct,
            )
            self._resistance_calc = ResistanceCalculator(calc_config)
        return self._resistance_calc
    
    # ============================================
    # 🆕 V3.0 MTF 水位生成
    # ============================================
    
    @property
    def level_calculator(self):
        """
        V3.0: MTF 水位计算器
        
        延迟初始化，首次访问时创建。
        """
        if self._level_calculator is None:
            from key_level_grid.level_calculator import LevelCalculator
            self._level_calculator = LevelCalculator(self.full_config)
        return self._level_calculator
    
    @property
    def mtf_feed(self):
        """
        V3.0: MTF K 线数据源
        
        延迟初始化，首次访问时创建。
        """
        if self._mtf_feed is None:
            from key_level_grid.data.feeds import MTFKlineFeed
            level_gen_config = self.full_config.get("level_generation", {})
            self._mtf_feed = MTFKlineFeed(
                timeframes=level_gen_config.get("timeframes", ["1d", "4h", "15m"]),
                config=self.full_config,
            )
        return self._mtf_feed
    
    def is_v3_enabled(self) -> bool:
        """
        检查是否启用 V3.0 水位生成
        
        Returns:
            True if V3.0 level generation is enabled
        """
        return self.full_config.get("grid", {}).get("level_generation", {}).get("enabled", False)
    
    def generate_levels_v3(
        self,
        klines_by_tf: Dict[str, List[Dict]],
        current_price: float,
        role: str = "support",
        max_levels: int = 10,
    ) -> Optional[List[tuple]]:
        """
        V3.0: 使用 MTF 评分生成水位
        
        Args:
            klines_by_tf: 多时间框架 K 线数据
            current_price: 当前价格
            role: "support" | "resistance"
            max_levels: 最大水位数
        
        Returns:
            [(price, LevelScore), ...] 或 None
        """
        if not self.is_v3_enabled():
            self.logger.debug("V3.0 level generation is disabled")
            return None
        
        # 更新 MTF Feed
        for tf, klines in klines_by_tf.items():
            self.mtf_feed.update(tf, klines)
        
        # 检查数据同步
        if not self.mtf_feed.is_synced():
            stale = self.mtf_feed.get_stale_timeframes()
            self.logger.warning(f"MTF data not synced, stale: {stale}")
            return None
        
        # 生成水位
        return self.level_calculator.generate_target_levels(
            klines_by_tf=klines_by_tf,
            current_price=current_price,
            role=role,
            max_levels=max_levels,
        )
    
    def should_rebuild_grid_v3(self, current_price: float) -> tuple:
        """
        V3.0: 检查是否应该重构网格
        
        Args:
            current_price: 当前价格
        
        Returns:
            (should_rebuild: bool, trigger: RebuildTrigger or None)
        """
        from key_level_grid.core.triggers import (
            should_rebuild_grid,
            RebuildTrigger,
        )
        
        if self.state is None:
            return True, RebuildTrigger.COLD_START
        
        level_gen_config = self.full_config.get("grid", {}).get("level_generation", {})
        rebuild_config = level_gen_config.get("rebuild", {})
        
        # 检查锚点偏移
        should = should_rebuild_grid(
            current_anchor=current_price,
            last_anchor=self.state.anchor_price,
            last_rebuild_ts=self.state.last_rebuild_ts,
            anchor_drift_threshold=float(rebuild_config.get("anchor_drift_threshold", 0.03)),
            rebuild_cooldown=int(rebuild_config.get("cooldown_sec", 14400)),
        )
        
        if should:
            return True, RebuildTrigger.ANCHOR_DRIFT
        
        # 检查覆盖告急
        if self.state.support_levels_state:
            lowest_support = min(l.price for l in self.state.support_levels_state)
            if current_price <= lowest_support * 1.01:  # 距最低支撑 1%
                return True, RebuildTrigger.BOUNDARY_ALERT
        
        return False, None
    
    def update_position(self, current_price: float, market_state=None) -> dict:
        """兼容: 更新仓位状态"""
        result = {"status": "ok", "actions": []}
        if self.check_stop_loss(current_price):
            result["status"] = "stop_loss_triggered"
            result["actions"].append({
                "action": "close_all",
                "price": current_price,
                "reason": "grid_floor_breach"
            })
        self.update_pnl(current_price)
        return result
    
    def open_position(self, entry_price: float, stop_loss_price: float = 0, 
                      direction: str = "long", market_state=None, klines=None):
        """兼容: 开仓"""
        return self.state
    
    def close_position(self, price: float, reason: str = "") -> dict:
        """兼容: 平仓"""
        return self.execute_stop_loss(price)
    
    def get_position_summary(self, current_price: float) -> dict:
        """兼容: 获取仓位摘要"""
        summary = self.get_summary(current_price)
        if not summary.get("has_grid"):
            return {
                "has_position": False,
                "direction": "none",
                "position_usdt": 0,
            }
        return {
            "has_position": summary["total_position_usdt"] > 0,
            "direction": self.state.direction if self.state else "none",
            "position_usdt": summary["total_position_usdt"],
            "entry_price": summary["avg_entry_price"],
            "unrealized_pnl": summary["unrealized_pnl"],
            "grid_floor": summary["grid_floor"],
        }


# 别名 - 向后兼容
KeyLevelPositionManager = GridPositionManager
