"""
水位映射模块

负责管理支撑位到阻力位的逐级邻位映射
"""

import time
from typing import Any, Dict, List, Optional

from key_level_grid.core.state import GridLevelState, GridState
from key_level_grid.core.types import LevelStatus
from key_level_grid.utils.logger import get_logger


# 价格容差常量（0.01%）
PRICE_TOLERANCE = 0.0001

# 阻力位 ID 偏移量
RESISTANCE_ID_OFFSET = 1000


class LevelMappingManager:
    """
    水位映射管理器
    
    负责:
    1. 构建逐级邻位映射表
    2. 规范化水位 ID
    3. 同步映射到交易所挂单
    """
    
    def __init__(self, logger=None):
        self.logger = logger or get_logger(__name__)
    
    @staticmethod
    def price_matches(p1: float, p2: float, tolerance: float = PRICE_TOLERANCE) -> bool:
        """判断两个价格是否匹配"""
        if p2 == 0:
            return False
        return abs(p1 - p2) / p2 < tolerance
    
    def build_level_mapping(self, state: GridState) -> Dict[int, int]:
        """
        构建逐级邻位映射表
        
        规则：每个支撑位映射到其物理价格上方的第一个水位（邻位）
        
        Args:
            state: 网格状态
            
        Returns:
            {support_level_id: adjacent_level_id}
        """
        if not state:
            return {}
        
        # 合并所有水位并按价格升序排列
        all_levels: List[GridLevelState] = (
            state.support_levels_state + state.resistance_levels_state
        )
        sorted_levels = sorted(all_levels, key=lambda x: x.price)
        
        mapping: Dict[int, int] = {}
        min_profit_pct = float(state.min_profit_pct or 0)
        missing_adjacent_levels: List[float] = []
        
        for i, level in enumerate(sorted_levels):
            # 只为支撑位建立映射
            if level.role != "support":
                continue
            
            # 最小利润价格阈值
            min_sell_price = level.price * (1 + min_profit_pct)
            
            # 查找上方第一个有效水位（邻位）
            target_level = None
            for j in range(i + 1, len(sorted_levels)):
                candidate = sorted_levels[j]
                if candidate.price > min_sell_price:
                    target_level = candidate
                    break
            
            if target_level:
                mapping[level.level_id] = target_level.level_id
                self.logger.debug(
                    f"📍 映射: L_{level.level_id}({level.price:.2f}) → L_{target_level.level_id}({target_level.price:.2f})"
                )
            else:
                # 边界情况：最高支撑位无上方邻位
                missing_adjacent_levels.append(level.price)
        
        # 边界告警
        if missing_adjacent_levels:
            self.logger.warning(
                f"⚠️ [Mapping] 以下支撑位无上方邻位: {missing_adjacent_levels}"
            )
        
        self.logger.info(
            f"📍 [Mapping] 构建完成: {len(mapping)} 个映射, "
            f"{len(missing_adjacent_levels)} 个无邻位"
        )
        
        return mapping
    
    def normalize_level_ids(self, state: GridState) -> bool:
        """
        规范化 level_id（兼容旧版状态文件）
        
        旧版状态文件中，支撑位和阻力位的 level_id 可能重叠（都从 1 开始）。
        新版要求全局唯一：支撑位 1-999，阻力位 1001+。
        
        Args:
            state: 网格状态
            
        Returns:
            是否需要重建映射
        """
        if not state:
            return False
        
        needs_rebuild = False
        
        # 检查是否有 ID 冲突
        support_ids = {lvl.level_id for lvl in state.support_levels_state}
        resistance_ids = {lvl.level_id for lvl in state.resistance_levels_state}
        
        # 如果阻力位 ID 都小于 1000，说明是旧版格式，需要重新分配
        if state.resistance_levels_state:
            max_resistance_id = max(lvl.level_id for lvl in state.resistance_levels_state)
            if max_resistance_id < RESISTANCE_ID_OFFSET:
                self.logger.info("📍 [Mapping] 检测到旧版 level_id 格式，正在规范化...")
                
                for i, lvl in enumerate(state.resistance_levels_state):
                    old_id = lvl.level_id
                    lvl.level_id = RESISTANCE_ID_OFFSET + i + 1
                    self.logger.debug(f"📍 阻力位 ID 重分配: {old_id} → {lvl.level_id}")
                
                needs_rebuild = True
        
        # 检查是否有 ID 重叠
        overlap = support_ids & resistance_ids
        if overlap:
            self.logger.warning(f"📍 [Mapping] 检测到 ID 重叠: {overlap}，正在修复...")
            for i, lvl in enumerate(state.resistance_levels_state):
                lvl.level_id = RESISTANCE_ID_OFFSET + i + 1
            needs_rebuild = True
        
        return needs_rebuild
    
    def get_level_by_id(self, state: GridState, level_id: int) -> Optional[GridLevelState]:
        """通过 level_id 查找水位"""
        if not state:
            return None
        for lvl in state.support_levels_state:
            if lvl.level_id == level_id:
                return lvl
        for lvl in state.resistance_levels_state:
            if lvl.level_id == level_id:
                return lvl
        return None
    
    def index_orders_by_level(
        self,
        state: GridState,
        open_orders: List[Dict],
        side: str = "sell",
    ) -> Dict[int, List[Dict]]:
        """
        按水位索引交易所挂单
        
        Args:
            state: 网格状态
            open_orders: 交易所挂单列表
            side: 订单方向 ("buy" | "sell")
        
        Returns:
            {level_id: [orders]}
        """
        if not state:
            return {}
        
        all_levels = state.support_levels_state + state.resistance_levels_state
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
        state: GridState,
        current_price: float,
        open_orders: List[Dict],
        exchange_min_qty: float,
    ) -> List[Dict[str, Any]]:
        """
        逐级邻位映射同步
        
        遍历每个有成交的支撑位，根据邻位映射计算应挂卖单配额，
        与实盘挂单对比，生成补单/撤单动作。
        
        Args:
            state: 网格状态
            current_price: 当前价格
            open_orders: 交易所挂单列表
            exchange_min_qty: 交易所最小下单量
        
        Returns:
            卖单动作列表 [{"action": "place"|"cancel", ...}]
        """
        if not state:
            return []
        
        actions: List[Dict[str, Any]] = []
        base_qty = float(state.base_amount_per_grid or 0)
        sell_quota_ratio = float(state.sell_quota_ratio or 0.7)
        
        # 索引交易所卖单
        sell_orders_by_level = self.index_orders_by_level(state, open_orders, side="sell")
        
        # 汇总每个目标水位的期望卖单量
        expected_sell_by_level: Dict[int, float] = {}
        
        for support_lvl in state.support_levels_state:
            fill_count = int(support_lvl.fill_counter or 0)
            if fill_count <= 0:
                continue
            
            # 查找邻位映射
            target_level_id = state.level_mapping.get(support_lvl.level_id)
            if not target_level_id:
                self.logger.warning(
                    f"⚠️ [SyncMapping] 支撑位 L_{support_lvl.level_id}({support_lvl.price:.2f}) "
                    f"无邻位映射，跳过卖单同步"
                )
                continue
            
            # 计算该支撑位贡献的卖单量
            contrib_qty = fill_count * base_qty * sell_quota_ratio
            expected_sell_by_level[target_level_id] = (
                expected_sell_by_level.get(target_level_id, 0) + contrib_qty
            )
        
        # 获取所有目标水位
        all_levels = state.support_levels_state + state.resistance_levels_state
        level_by_id = {lvl.level_id: lvl for lvl in all_levels}
        all_target_level_ids = set(expected_sell_by_level.keys()) | set(sell_orders_by_level.keys())
        
        for target_level_id in all_target_level_ids:
            target_lvl = level_by_id.get(target_level_id)
            if not target_lvl:
                continue
            
            expected_qty = expected_sell_by_level.get(target_level_id, 0)
            existing_orders = sell_orders_by_level.get(target_level_id, [])
            
            # 计算实盘已挂量
            open_qty = sum(
                float(o.get("base_amount", 0) or 0) or 
                float(o.get("contracts", 0) or 0) * float(state.contract_size or 0)
                for o in existing_orders
            )
            
            # 计算 PLACING 状态的待挂单量
            placing_qty = 0.0
            if target_lvl.status == LevelStatus.PLACING:
                placing_qty = float(target_lvl.target_qty or 0)
            
            effective_pending = open_qty + placing_qty
            deficit = max(0, expected_qty - effective_pending)
            
            if deficit > 0 and deficit < exchange_min_qty:
                deficit = 0
            
            tolerance_threshold = max(exchange_min_qty, expected_qty * 0.05)
            
            if deficit >= tolerance_threshold:
                # 需要补单
                place_qty = max(deficit, exchange_min_qty)
                actions.append({
                    "action": "place",
                    "side": "sell",
                    "price": target_lvl.price,
                    "qty": place_qty,
                    "level_id": target_level_id,
                    "reason": "sync_mapping_deficit",
                    "expected_qty": expected_qty,
                    "open_qty": open_qty,
                    "placing_qty": placing_qty,
                })
                target_lvl.status = LevelStatus.PLACING
                target_lvl.target_qty = place_qty
                target_lvl.last_action_ts = int(time.time())
                self.logger.info(
                    f"📈 [SyncMapping] 补卖单: L_{target_level_id}({target_lvl.price:.2f}), "
                    f"expected={expected_qty:.6f}, open={open_qty:.6f}, deficit={deficit:.6f}"
                )
            
            elif expected_qty <= 0 and open_qty > 0:
                # 期望量为 0 但有挂单，需要撤单
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
                self.logger.info(
                    f"📉 [SyncMapping] 撤卖单: L_{target_level_id}({target_lvl.price:.2f}), "
                    f"expected=0, open={open_qty:.6f}"
                )
            
            elif expected_qty > 0 and abs(open_qty - expected_qty) > tolerance_threshold:
                # 数量偏差过大，撤单后重挂
                for order in existing_orders:
                    actions.append({
                        "action": "cancel",
                        "side": "sell",
                        "price": target_lvl.price,
                        "order_id": order.get("id", ""),
                        "level_id": target_level_id,
                        "reason": "sync_mapping_rebalance",
                        "expected_qty": expected_qty,
                        "open_qty": open_qty,
                    })
                target_lvl.status = LevelStatus.CANCELING
                target_lvl.last_action_ts = int(time.time())
                self.logger.info(
                    f"🔄 [SyncMapping] 重平衡: L_{target_level_id}({target_lvl.price:.2f}), "
                    f"expected={expected_qty:.6f}, open={open_qty:.6f}"
                )
            
            else:
                # 数量匹配，无需操作
                if existing_orders:
                    target_lvl.status = LevelStatus.ACTIVE
                    target_lvl.active_order_id = existing_orders[0].get("id", "")
                    target_lvl.open_qty = open_qty
        
        return actions
    
    def build_event_sell_increment(
        self,
        state: GridState,
        delta_buy_qty: float,
        exchange_min_qty_btc: float,
        current_price: float,
        filled_support_level_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        买单成交后，基于逐级邻位映射增量补卖单
        
        Args:
            state: 网格状态
            delta_buy_qty: 买入数量
            exchange_min_qty_btc: 交易所最小下单量
            current_price: 当前价格
            filled_support_level_id: 成交的支撑位 ID（可选）
        
        Returns:
            卖单动作列表
        """
        if not state or delta_buy_qty <= 0:
            return []
        
        delta_sell = delta_buy_qty * state.sell_quota_ratio
        if delta_sell < exchange_min_qty_btc:
            self.logger.warning(
                f"⚠️ 最小卖单量不足: delta_sell={delta_sell:.6f}, "
                f"min={exchange_min_qty_btc:.6f}"
            )
            return []

        # 确定目标卖单水位
        target_level = None
        
        if filled_support_level_id:
            target_level_id = state.level_mapping.get(filled_support_level_id)
            if target_level_id:
                target_level = self.get_level_by_id(state, target_level_id)
                if target_level:
                    self.logger.debug(
                        f"⚡ [Event] 使用邻位映射: S_{filled_support_level_id} → "
                        f"L_{target_level_id}({target_level.price:.2f})"
                    )
        
        if not target_level:
            # 回退：查找最近成交支撑位的映射
            recent_fill = None
            for lvl in sorted(state.support_levels_state, key=lambda x: x.price, reverse=True):
                if lvl.fill_counter > 0 and lvl.price < current_price:
                    recent_fill = lvl
                    break
            
            if recent_fill:
                target_level_id = state.level_mapping.get(recent_fill.level_id)
                if target_level_id:
                    target_level = self.get_level_by_id(state, target_level_id)
        
        if not target_level:
            # 再次回退：找当前价上方最近的水位
            all_levels = state.support_levels_state + state.resistance_levels_state
            candidates = [lvl for lvl in all_levels if lvl.price > current_price]
            if candidates:
                target_level = min(candidates, key=lambda x: x.price)
                self.logger.warning(
                    f"⚠️ [Event] 无映射可用，使用最近上方水位: {target_level.price:.2f}"
                )
        
        if not target_level:
            self.logger.warning(
                f"⚠️ 无可用卖单水位(Event): delta_sell={delta_sell:.6f}, current={current_price:.2f}"
            )
            return []
        
        # 检查价格缓冲
        if current_price >= target_level.price * (1 - state.sell_price_buffer_pct):
            self.logger.warning(
                f"⚠️ 卖单水位太近: current={current_price:.2f}, "
                f"target={target_level.price:.2f}"
            )
            return []
        
        self.logger.info(
            f"⚡ [Event] 补卖单: price={target_level.price:.2f}, qty={delta_sell:.6f}, "
            f"level_id={target_level.level_id}"
        )
        return [{
            "action": "place",
            "side": "sell",
            "price": target_level.price,
            "qty": delta_sell,
            "level_id": target_level.level_id,
            "reason": "event_sell_mapping",
        }]
