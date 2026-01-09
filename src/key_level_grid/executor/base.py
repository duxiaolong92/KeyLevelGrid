"""
订单执行基类

定义交易所接口和订单数据结构。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, Optional
from uuid import uuid4


class OrderSide(Enum):
    """订单方向"""
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    """订单类型"""
    MARKET = "market"  # 市价单
    LIMIT = "limit"    # 限价单
    IOC = "ioc"        # 立即成交或取消


class OrderStatus(Enum):
    """订单状态"""
    PENDING = "pending"        # 待提交
    SUBMITTED = "submitted"    # 已提交
    PARTIAL = "partial"        # 部分成交
    FILLED = "filled"          # 完全成交
    CANCELLED = "cancelled"    # 已取消
    REJECTED = "rejected"      # 被拒绝
    FAILED = "failed"          # 失败


class PricingMode(Enum):
    """订单计价模式（Phase 6.1）"""
    QUANTITY = "quantity"  # 按数量（合约张数/币数量）
    USDT = "usdt"          # 按USDT金额


@dataclass
class Order:
    """
    订单对象
    
    表示一个交易订单的完整信息。
    """
    # 必需字段
    order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    
    # 价格（限价单必需）
    price: Optional[float] = None
    
    # 状态
    status: OrderStatus = OrderStatus.PENDING
    
    # 成交信息
    filled_quantity: float = 0.0
    avg_fill_price: float = 0.0
    fees: float = 0.0
    
    # 时间戳
    created_at: int = 0  # 创建时间（毫秒）
    submitted_at: Optional[int] = None  # 提交时间
    filled_at: Optional[int] = None  # 完成时间
    
    # 外部ID（交易所返回）
    exchange_order_id: Optional[str] = None
    
    # 拒绝/失败原因
    reject_reason: Optional[str] = None
    
    # === 实盘交易扩展字段（Phase 5.1）===
    # 订单类型标识
    is_paper_trade: bool = True  # 默认为纸交易
    
    # 交易所原始响应
    exchange_response: Optional[Dict] = None
    
    # 实际成交信息（可能与预期不同）
    actual_fill_price: Optional[float] = None    # 实际成交价
    actual_fill_quantity: Optional[float] = None # 实际成交量
    actual_fees: Optional[float] = None          # 实际手续费
    
    # === Phase 6.1: USDT计价和止盈止损 ===
    # 计价模式
    pricing_mode: str = 'quantity'  # 'quantity' 或 'usdt'
    target_value_usd: Optional[float] = None  # USDT计价时的目标金额
    
    # 止盈止损（下单时设置）
    take_profit: Optional[float] = None       # 止盈价格
    stop_loss: Optional[float] = None         # 止损价格
    take_profit_pct: Optional[float] = None   # 止盈百分比（如3.0表示+3%）
    stop_loss_pct: Optional[float] = None     # 止损百分比（如2.0表示-2%）
    
    # 止盈止损订单ID（设置后填充）
    take_profit_order_id: Optional[str] = None
    stop_loss_order_id: Optional[str] = None
    
    # === reduceOnly保护（仅减仓） ===
    reduce_only: bool = False  # 是否仅减仓（平仓订单应设置为True）
    
    # 元数据
    metadata: Dict = field(default_factory=dict)
    
    def __post_init__(self):
        if self.created_at == 0:
            import time
            self.created_at = int(time.time() * 1000)
    
    @classmethod
    def create(
        cls,
        symbol: str,
        side: OrderSide,
        quantity: float,
        order_type: OrderType = OrderType.LIMIT,
        price: Optional[float] = None,
        **kwargs
    ) -> "Order":
        """创建新订单"""
        return cls(
            order_id=str(uuid4()),
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            **kwargs
        )
    
    @property
    def is_filled(self) -> bool:
        """是否完全成交"""
        return self.status == OrderStatus.FILLED
    
    @property
    def is_active(self) -> bool:
        """是否仍在活动中"""
        return self.status in [OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.PARTIAL]
    
    @property
    def is_terminal(self) -> bool:
        """是否已到终态"""
        return self.status in [OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.FAILED]
    
    @property
    def remaining_quantity(self) -> float:
        """剩余数量"""
        return max(0.0, self.quantity - self.filled_quantity)
    
    @property
    def fill_percentage(self) -> float:
        """成交百分比"""
        if self.quantity == 0:
            return 0.0
        return (self.filled_quantity / self.quantity) * 100
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "quantity": self.quantity,
            "price": self.price,
            "status": self.status.value,
            "filled_quantity": self.filled_quantity,
            "avg_fill_price": self.avg_fill_price,
            "fees": self.fees,
            "created_at": self.created_at,
            "submitted_at": self.submitted_at,
            "filled_at": self.filled_at,
            "exchange_order_id": self.exchange_order_id,
            "reject_reason": self.reject_reason,
            "remaining_quantity": self.remaining_quantity,
            "fill_percentage": self.fill_percentage,
            "metadata": self.metadata,
        }


class ExecutorBase(ABC):
    """
    交易所执行器基类
    
    定义所有交易所执行器必须实现的接口。
    """
    
    @abstractmethod
    async def submit_order(self, order: Order) -> bool:
        """
        提交订单
        
        Args:
            order: 订单对象
            
        Returns:
            True 如果提交成功
        """
        pass
    
    @abstractmethod
    async def cancel_order(self, order: Order) -> bool:
        """
        取消订单
        
        Args:
            order: 订单对象
            
        Returns:
            True 如果取消成功
        """
        pass
    
    @abstractmethod
    async def get_order_status(self, order: Order) -> OrderStatus:
        """
        查询订单状态
        
        Args:
            order: 订单对象
            
        Returns:
            当前订单状态
        """
        pass
    
    @abstractmethod
    async def get_balance(self, asset: str = "USDT") -> Dict:
        """
        查询余额
        
        Args:
            asset: 资产符号 (如 'USDT')
            
        Returns:
            {
                'total': 总余额,
                'free': 可用余额,
                'used': 冻结余额
            }
        """
        pass
    
    @abstractmethod
    async def get_positions(self, symbol: str = None) -> list:
        """
        查询持仓
        
        Args:
            symbol: 交易对（可选，None表示查询所有）
        
        Returns:
            持仓列表
        """
        pass
    
    @abstractmethod
    async def get_account_info(self) -> Dict:
        """
        查询账户信息
        
        Returns:
            账户信息字典
        """
        pass

