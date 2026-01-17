"""
展示数据模块

负责生成策略状态的展示数据，供前端面板显示
"""

from typing import Any, Dict, List, Optional

from key_level_grid.core.models import KeyLevelGridState
from key_level_grid.core.state import GridState


class DisplayDataGenerator:
    """展示数据生成器"""
    
    def __init__(
        self,
        position_manager,
        config,
        account_balance: Dict[str, float] = None,
        gate_position: Dict[str, Any] = None,
        gate_open_orders: List[Dict] = None,
        contract_size: float = 1.0,
    ):
        self.position_manager = position_manager
        self.config = config
        self._account_balance = account_balance or {"total": 0, "free": 0, "used": 0}
        self._gate_position = gate_position or {}
        self._gate_open_orders = gate_open_orders or []
        self._contract_size = contract_size
    
    def update_context(
        self,
        account_balance: Dict[str, float] = None,
        gate_position: Dict[str, Any] = None,
        gate_open_orders: List[Dict] = None,
        contract_size: float = None,
    ):
        """更新上下文数据"""
        if account_balance is not None:
            self._account_balance = account_balance
        if gate_position is not None:
            self._gate_position = gate_position
        if gate_open_orders is not None:
            self._gate_open_orders = gate_open_orders
        if contract_size is not None:
            self._contract_size = contract_size
    
    def get_status(
        self, 
        current_state: Optional[KeyLevelGridState],
        running: bool,
        pending_signal,
        kline_feed
    ) -> Dict[str, Any]:
        """获取策略状态"""
        position_summary = self.position_manager.get_position_summary(
            current_state.close if current_state else 0
        )
        
        return {
            "running": running,
            "symbol": self.config.symbol,
            "current_price": current_state.close if current_state else None,
            "indicators": {
                "macd": current_state.macd if current_state else None,
                "rsi": current_state.rsi if current_state else None,
                "atr": current_state.atr if current_state else None,
                "adx": current_state.adx if current_state else None,
            },
            "position": position_summary,
            "pending_signal": pending_signal.to_dict() if pending_signal else None,
            "kline_stats": kline_feed.get_stats(),
        }
    
    def get_display_data(
        self,
        current_state: Optional[KeyLevelGridState],
        kline_feed,
        build_klines_by_timeframe_func,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        """获取显示面板数据"""
        state = current_state
        pos = self.position_manager.state
        grid_config = self.position_manager.grid_config
        resistance_config = self.position_manager.resistance_config
        levels_from_grid = False
        
        # 周期信息
        kline_config = self.config.kline_config
        primary_tf = kline_config.primary_timeframe.value
        aux_tfs = [tf.value for tf in kline_config.auxiliary_timeframes]
        
        data = {
            "symbol": self.config.symbol,
            "timestamp": state.timestamp if state else None,
            "timeframe": {
                "primary": primary_tf,
                "auxiliary": aux_tfs,
                "display": f"{primary_tf} + {' + '.join(aux_tfs)}" if aux_tfs else primary_tf,
            },
        }
        
        # 价格数据
        if state:
            data["price"] = {
                "current": state.close,
                "open": state.open,
                "high": state.high,
                "low": state.low,
            }
            
            # 技术指标
            data["indicators"] = {
                "macd": state.macd,
                "macd_signal": state.macd_signal,
                "macd_histogram": state.macd_histogram,
                "rsi": state.rsi,
                "atr": state.atr,
                "adx": state.adx,
                "volume_ratio": state.volume_ratio,
            }
            
            # 实时计算阻力位和支撑位
            if not (pos and (pos.support_levels_state or pos.resistance_levels_state)):
                klines = kline_feed.get_cached_klines(
                    self.config.kline_config.primary_timeframe
                )
                
                if len(klines) >= 50:
                    klines_dict = build_klines_by_timeframe_func(klines)
                    resistance_calc = self.position_manager.resistance_calc
                    
                    resistances = resistance_calc.calculate_resistance_levels(
                        state.close, klines, "long", klines_by_timeframe=klines_dict
                    )
                    supports = resistance_calc.calculate_support_levels(
                        state.close, klines, klines_by_timeframe=klines_dict
                    )
                    
                    data["resistance_levels"] = [
                        {
                            "price": r.price, 
                            "type": r.level_type.value, 
                            "strength": r.strength, 
                            "timeframe": getattr(r, 'timeframe', '4h'),
                            "source": getattr(r, 'source', ''),
                            "description": getattr(r, 'description', ''),
                            "fill_counter": 0,
                        }
                        for r in resistances[:10]
                    ]
                    data["support_levels"] = [
                        {
                            "price": s.price, 
                            "type": s.level_type.value, 
                            "strength": s.strength, 
                            "timeframe": getattr(s, 'timeframe', '4h'),
                            "source": getattr(s, 'source', ''),
                            "description": getattr(s, 'description', ''),
                            "fill_counter": 0,
                        }
                        for s in supports[:10]
                    ]
        
        # 仓位信息
        if pos:
            data["position"] = {
                "direction": pos.direction,
                "entry_price": pos.entry_price,
                "size_usdt": pos.position_usdt,
                "unrealized_pnl": pos.unrealized_pnl,
            }
            if pos.stop_loss:
                data["stop_loss"] = {
                    "price": pos.stop_loss.stop_price,
                    "type": pos.stop_loss.stop_type.value,
                }
            if pos.take_profit_plan:
                data["take_profit"] = [
                    {"price": tp.price, "pct": tp.close_pct, "rr": tp.rr_multiple}
                    for tp in pos.take_profit_plan.levels if tp.close_pct > 0
                ]
            
            # 使用网格固定水位
            support_meta = {
                float(s.get("price", 0) if isinstance(s, dict) else s.price): s
                for s in (pos.support_levels or [])
            }
            resistance_meta = {
                float(r.get("price", 0) if isinstance(r, dict) else r.price): r
                for r in (pos.resistance_levels or [])
            }

            if pos.support_levels_state or pos.resistance_levels_state:
                levels_from_grid = True
                data["support_levels"] = [
                    {
                        "price": lvl.price,
                        "type": "support",
                        "strength": support_meta.get(lvl.price, {}).get("strength", 0),
                        "timeframe": support_meta.get(lvl.price, {}).get("timeframe", "4h"),
                        "source": support_meta.get(lvl.price, {}).get("source", ""),
                        "description": support_meta.get(lvl.price, {}).get("description", ""),
                        "fill_counter": int(getattr(lvl, "fill_counter", 0) or 0),
                    }
                    for lvl in pos.support_levels_state
                ]
                data["resistance_levels"] = [
                    {
                        "price": lvl.price,
                        "type": "resistance",
                        "strength": resistance_meta.get(lvl.price, {}).get("strength", 0),
                        "timeframe": resistance_meta.get(lvl.price, {}).get("timeframe", "4h"),
                        "source": resistance_meta.get(lvl.price, {}).get("source", ""),
                        "description": resistance_meta.get(lvl.price, {}).get("description", ""),
                        "fill_counter": int(getattr(lvl, "fill_counter", 0) or 0),
                    }
                    for lvl in pos.resistance_levels_state
                ]
            else:
                data["resistance_levels"] = [
                    {
                        "price": r.get("price", 0) if isinstance(r, dict) else r.price,
                        "type": r.get("type", "resistance") if isinstance(r, dict) else getattr(r, 'level_type', 'resistance'),
                        "strength": r.get("strength", 0) if isinstance(r, dict) else r.strength,
                        "timeframe": r.get("timeframe", "4h") if isinstance(r, dict) else getattr(r, 'timeframe', '4h'),
                        "source": r.get("source", "") if isinstance(r, dict) else getattr(r, 'source', ''),
                        "description": r.get("description", "") if isinstance(r, dict) else getattr(r, 'description', ''),
                        "fill_counter": r.get("fill_counter", 0) if isinstance(r, dict) else int(getattr(r, "fill_counter", 0) or 0),
                    }
                    for r in pos.resistance_levels[:10]
                ]
                data["support_levels"] = [
                    {
                        "price": s.get("price", 0) if isinstance(s, dict) else s.price,
                        "type": s.get("type", "support") if isinstance(s, dict) else getattr(s, 'level_type', 'support'),
                        "strength": s.get("strength", 0) if isinstance(s, dict) else s.strength,
                        "timeframe": s.get("timeframe", "4h") if isinstance(s, dict) else getattr(s, 'timeframe', '4h'),
                        "source": s.get("source", "") if isinstance(s, dict) else getattr(s, 'source', ''),
                        "description": s.get("description", "") if isinstance(s, dict) else getattr(s, 'description', ''),
                        "fill_counter": s.get("fill_counter", 0) if isinstance(s, dict) else int(getattr(s, "fill_counter", 0) or 0),
                    }
                    for s in pos.support_levels[:10]
                ]

        # 过滤水位
        min_strength = getattr(resistance_config, "min_strength", 0) or 0
        lower = grid_config.manual_lower if grid_config.range_mode == "manual" else 0
        upper = grid_config.manual_upper if grid_config.range_mode == "manual" else 0
        if lower <= 0 or upper <= 0:
            lower, upper = 0, 0

        if not levels_from_grid:
            data["resistance_levels"] = self._filter_levels(
                data.get("resistance_levels", []), min_strength, lower, upper
            )
            data["support_levels"] = self._filter_levels(
                data.get("support_levels", []), min_strength, lower, upper
            )
        
        # 交易历史
        data["active_inventory"] = [f.to_dict() for f in pos.active_inventory] if pos else []
        data["settled_inventory"] = [f.to_dict() for f in pos.settled_inventory] if pos else []
        
        # 账户信息
        data["account"] = self.get_account_display_data()
        
        # 持仓信息
        data["position"] = self.get_position_display_data(state)
        
        # 当前挂单
        data["pending_orders"] = self.get_pending_orders_display(
            state, 
            data.get("support_levels", []),
            data.get("resistance_levels", []),
            dry_run
        )
        
        return data
    
    def _filter_levels(
        self, 
        levels: List[Dict[str, Any]], 
        min_strength: float,
        lower: float,
        upper: float
    ) -> List[Dict[str, Any]]:
        """过滤水位"""
        filtered = []
        for lvl in levels or []:
            price = float(lvl.get("price", 0) or 0)
            strength = float(lvl.get("strength", 0) or 0)
            if min_strength and strength < min_strength:
                continue
            if lower and price < lower:
                continue
            if upper and price > upper:
                continue
            filtered.append(lvl)
        return filtered
    
    def get_account_display_data(self) -> Dict[str, Any]:
        """获取账户信息显示数据"""
        pos_config = self.position_manager.position_config
        grid_config = self.position_manager.grid_config
        grid_state = self.position_manager.state
        total_invested = grid_state.position_usdt if grid_state else 0
        
        # 账户余额
        if self._account_balance.get("total", 0) > 0:
            total_balance = self._account_balance["total"]
            available = self._account_balance["free"]
            frozen = self._account_balance["used"]
        else:
            total_balance = pos_config.total_capital
            available = pos_config.total_capital - total_invested
            frozen = total_invested
        
        max_position = total_balance * pos_config.max_leverage * pos_config.max_capital_usage
        
        # 网格底线和止损价格
        grid_floor = 0
        stop_loss_price = 0
        avg_entry_price = 0
        expected_avg_price = 0
        
        if grid_state and grid_state.grid_floor > 0:
            grid_floor = grid_state.grid_floor
            stop_loss_price = grid_floor
            avg_entry_price = grid_state.avg_entry_price
            
            if grid_state.total_position_usdt > 0 and avg_entry_price > 0:
                expected_avg_price = avg_entry_price
            elif grid_state.buy_orders:
                prices = [o.price for o in grid_state.buy_orders if o.price > 0]
                expected_avg_price = sum(prices) / len(prices) if prices else 0
        
        # 预计最大亏损
        max_loss = 0.0
        max_loss_pct = 0.0
        if expected_avg_price > 0 and stop_loss_price > 0:
            max_loss_pct = ((expected_avg_price - stop_loss_price) / expected_avg_price) * 100
            max_loss = max_position * (max_loss_pct / 100)
        
        return {
            "total_balance": total_balance,
            "available": available,
            "frozen": frozen,
            "grid_config": {
                "max_position": max_position,
                "max_leverage": pos_config.max_leverage,
                "max_capital_usage": pos_config.max_capital_usage,
                "grid_floor": grid_floor,
                "stop_loss_price": stop_loss_price,
                "expected_avg_price": expected_avg_price,
                "max_loss": max_loss,
                "max_loss_pct": max_loss_pct,
                "floor_buffer": grid_config.floor_buffer,
            },
            "grid_status": {
                "total_invested": total_invested,
                "pending_orders": 0,
                "filled_orders": 0,
            }
        }
    
    def get_position_display_data(self, state: Optional[KeyLevelGridState]) -> Dict[str, Any]:
        """获取持仓信息显示数据"""
        current_price = state.close if state else 0
        
        # 优先使用 Gate 真实持仓数据
        if self._gate_position and self._gate_position.get("contracts", 0) > 0:
            gate_pos = self._gate_position
            notional = gate_pos.get("notional", 0)
            entry_price = gate_pos.get("entry_price", 0)
            contracts = gate_pos.get("contracts", 0)
            unrealized_pnl = gate_pos.get("unrealized_pnl", 0)
            
            if notional == 0 and entry_price > 0:
                notional = contracts * entry_price
            
            grid_floor = 0
            pos = self.position_manager.state
            if pos and pos.support_levels:
                prices = [s.get('price', 0) if isinstance(s, dict) else s.price 
                          for s in pos.support_levels if (s.get('price', 0) if isinstance(s, dict) else s.price) > 0]
                if prices:
                    min_support = min(prices)
                    grid_floor = min_support * 0.995
            
            return {
                "side": "long",
                "qty": contracts,
                "avg_entry_price": entry_price,
                "value": notional,
                "unrealized_pnl": unrealized_pnl,
                "grid_floor": grid_floor,
            }
        
        # 回退：使用本地状态
        pos = self.position_manager.state
        if not pos or pos.position_usdt <= 0:
            return {}
        
        if pos.entry_price > 0 and current_price > 0:
            if pos.direction == "long":
                pnl = (current_price - pos.entry_price) * (pos.position_usdt / pos.entry_price)
            else:
                pnl = (pos.entry_price - current_price) * (pos.position_usdt / pos.entry_price)
        else:
            pnl = 0
        
        grid_floor = 0
        if pos.support_levels:
            prices = [s.get('price', 0) if isinstance(s, dict) else s.price 
                      for s in pos.support_levels if (s.get('price', 0) if isinstance(s, dict) else s.price) > 0]
            if prices:
                min_support = min(prices)
                grid_floor = min_support * 0.995
        
        return {
            "side": pos.direction,
            "qty": pos.position_usdt / pos.entry_price if pos.entry_price > 0 else 0,
            "avg_entry_price": pos.entry_price,
            "value": pos.position_usdt,
            "unrealized_pnl": pnl,
            "grid_floor": grid_floor,
        }
    
    def get_pending_orders_display(
        self, 
        state: Optional[KeyLevelGridState],
        support_levels: List[Dict] = None,
        resistance_levels: List[Dict] = None,
        dry_run: bool = True,
    ) -> List[Dict[str, Any]]:
        """获取当前挂单显示数据"""
        if not state:
            return []
        
        # 实盘模式使用真实挂单
        if not dry_run and self._gate_open_orders:
            orders = []
            for o in self._gate_open_orders:
                orders.append({
                    "side": o.get("side", ""),
                    "price": o.get("price", 0),
                    "amount": o.get("amount", 0),
                    "contracts": o.get("base_amount", 0),
                    "status": o.get("status", "pending"),
                    "source": "Gate",
                    "strength": 0,
                    "order_id": o.get("id", ""),
                })
            buy_orders = sorted([o for o in orders if o.get("side") == "buy"], key=lambda x: x["price"], reverse=True)
            sell_orders = sorted([o for o in orders if o.get("side") == "sell"], key=lambda x: x["price"], reverse=True)
            return sell_orders + buy_orders
        
        # 使用本地网格状态
        orders = []
        pos_state = self.position_manager.state
        if pos_state:
            if pos_state.support_levels_state or pos_state.resistance_levels_state:
                base_btc = float(getattr(pos_state, "base_amount_per_grid", 0) or 0)
                buy_orders = [
                    {
                        "side": "buy",
                        "price": lvl.price,
                        "amount": base_btc * lvl.price,
                        "contracts": base_btc,
                        "status": "pending",
                        "source": "support",
                        "strength": 0,
                    }
                    for lvl in sorted(pos_state.support_levels_state, key=lambda x: x.price, reverse=True)
                ]
                sell_orders = [
                    {
                        "side": "sell",
                        "price": lvl.price,
                        "amount": lvl.target_qty * lvl.price,
                        "contracts": lvl.target_qty,
                        "status": "pending",
                        "source": "resistance",
                        "strength": 0,
                    }
                    for lvl in sorted(pos_state.resistance_levels_state, key=lambda x: x.price, reverse=True)
                    if lvl.target_qty > 0
                ]
                return buy_orders + sell_orders

            # 兼容旧网格状态
            buy_orders = [
                {
                    "side": "buy",
                    "price": o.price,
                    "amount": o.amount_usdt,
                    "status": "filled" if o.is_filled else "pending",
                    "source": o.source,
                    "strength": o.strength,
                }
                for o in sorted(pos_state.buy_orders, key=lambda x: x.price, reverse=True)
            ]
            sell_orders = [
                {
                    "side": "sell",
                    "price": o.price,
                    "amount": o.amount_usdt,
                    "status": "filled" if o.is_filled else "pending",
                    "source": o.source,
                    "strength": o.strength,
                }
                for o in sorted(pos_state.sell_orders, key=lambda x: x.price, reverse=True)
            ]
            return buy_orders + sell_orders

        # 使用计算的支撑/阻力位
        config = self.position_manager.position_config
        support_levels = support_levels or []
        resistance_levels = resistance_levels or []
        
        min_strength = getattr(self.position_manager.resistance_config, 'min_strength', 80)
        strong_supports = [
            s for s in support_levels 
            if s.get("strength", 0) >= min_strength and s.get("price", 0) < state.close
        ]
        strong_resistances = [
            r for r in resistance_levels 
            if r.get("strength", 0) >= min_strength and r.get("price", 0) > state.close
        ]
        
        strong_supports.sort(key=lambda x: -x.get("price", 0))
        strong_resistances.sort(key=lambda x: x.get("price", 0))
        
        max_grids = getattr(self.position_manager.grid_config, 'max_grids', 10)
        strong_supports = strong_supports[:max_grids]
        strong_resistances = strong_resistances[:max_grids]
        
        max_position = config.total_capital * config.max_leverage * config.max_capital_usage
        if not strong_supports:
            return []
        
        per_grid_usdt = max_position / len(strong_supports)
        for support in strong_supports:
            orders.append({
                "side": "buy",
                "price": support.get("price", 0),
                "amount": per_grid_usdt,
                "status": "pending",
                "source": support.get("source", "support"),
                "strength": support.get("strength", 0),
            })
        
        if strong_resistances:
            per_tp_usdt = max_position / len(strong_resistances)
            for resistance in strong_resistances:
                orders.append({
                    "side": "sell",
                    "price": resistance.get("price", 0),
                    "amount": per_tp_usdt,
                    "status": "pending",
                    "source": resistance.get("source", "resistance"),
                    "strength": resistance.get("strength", 0),
                })
        
        return orders
    
    def generate_trade_plan_display(
        self, 
        state: Optional[KeyLevelGridState],
        pending_signal = None,
    ) -> Dict[str, Any]:
        """生成交易执行计划显示数据"""
        if state is None:
            return {}
        
        # 有待处理信号
        if pending_signal:
            signal = pending_signal
            entry = signal.entry_price
            stop = signal.stop_loss
            risk_pct = abs(entry - stop) / entry
            risk_per_trade = getattr(self.config.position_config, 'risk_per_trade', 0.02)
            risk_usdt = self.config.position_config.total_capital * risk_per_trade
            position_usdt = risk_usdt / risk_pct if risk_pct > 0 else 0
            
            return {
                "signal_type": signal.signal_type.value,
                "score": signal.score,
                "grade": signal.grade.value,
                "entry_plan": [
                    {"price": entry, "pct": 0.30, "filled": False},
                    {"price": entry * 0.95, "pct": 0.40, "filled": False},
                    {"price": entry * 1.08, "pct": 0.30, "filled": False},
                ],
                "stop_plan": {
                    "initial": stop,
                    "type": "通道止损",
                    "risk_usdt": risk_usdt,
                },
                "tp_plan": [
                    {"price": tp, "pct": 0.40 if i == 0 else 0.30 if i == 1 else 0.20, 
                     "rr": (tp - entry) / (entry - stop) if entry != stop else 0}
                    for i, tp in enumerate(signal.take_profits[:3])
                ],
                "expected_rr": 2.5,
            }
        
        # 有持仓
        if self.position_manager.state:
            pos = self.position_manager.state
            return {
                "signal_type": f"持仓中 ({pos.direction.upper()})",
                "score": 0,
                "grade": "-",
                "entry_plan": [
                    {
                        "price": b.fill_price or pos.entry_price * (1 + b.price_offset),
                        "pct": b.size_pct,
                        "filled": b.is_filled
                    }
                    for b in pos.batches
                ],
                "stop_plan": {
                    "initial": pos.stop_loss.stop_price if pos.stop_loss else 0,
                    "type": pos.stop_loss.stop_type.value if pos.stop_loss else "N/A",
                    "risk_usdt": pos.position_usdt * 0.10,
                },
                "tp_plan": [
                    {"price": tp.price, "pct": tp.close_pct, "rr": tp.rr_multiple}
                    for tp in (pos.take_profit_plan.levels if pos.take_profit_plan else [])[:3]
                ],
                "expected_rr": 2.0,
            }
        
        return {}
