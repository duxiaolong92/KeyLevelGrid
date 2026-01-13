"""
Polygon.io 美股 K线数据源

使用 Polygon.io REST API 获取美股历史 K 线数据
API 文档: https://polygon.io/docs/stocks/get_v2_aggs_ticker__stocksticker__range__multiplier___timespan___from___to
"""

import asyncio
import os
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import aiohttp

from key_level_grid.utils.logger import get_logger
from key_level_grid.models import Kline, Timeframe


class PolygonKlineFeed:
    """
    Polygon.io 美股 K 线数据源
    
    功能:
    1. REST API 获取历史 K 线
    2. 支持多周期数据
    3. 自动处理美股交易时段
    4. 限频保护（免费套餐 5 请求/分钟）
    """
    
    BASE_URL = "https://api.polygon.io"
    
    # Timeframe 到 Polygon API 参数的映射
    TIMEFRAME_MAP = {
        Timeframe.M1: ("1", "minute"),
        Timeframe.M5: ("5", "minute"),
        Timeframe.M15: ("15", "minute"),
        Timeframe.M30: ("30", "minute"),
        Timeframe.H1: ("1", "hour"),
        Timeframe.H4: ("4", "hour"),
        Timeframe.D1: ("1", "day"),
        Timeframe.W1: ("1", "week"),
    }
    
    def __init__(
        self,
        symbol: str,
        api_key: Optional[str] = None,
        rate_limit: float = 5.0,  # 每秒请求数（免费套餐建议 5/60 = 0.083）
    ):
        """
        初始化 Polygon 数据源
        
        Args:
            symbol: 股票代码（如 TSLA, AAPL）
            api_key: Polygon API Key（默认从环境变量 POLYGON_API_KEY 读取）
            rate_limit: 每秒最大请求数
        """
        self.symbol = symbol.upper()
        self.api_key = api_key or os.getenv("POLYGON_API_KEY", "")
        self.rate_limit = rate_limit
        
        self.session: Optional[aiohttp.ClientSession] = None
        
        # 数据缓存: timeframe -> List[Kline]
        self._kline_cache: Dict[Timeframe, List[Kline]] = {}
        
        # 限频控制
        self._last_request_time: float = 0
        self._min_request_interval: float = 1.0 / max(rate_limit, 0.1)
        
        self.logger = get_logger(__name__)
        
        if not self.api_key:
            self.logger.warning("⚠️ POLYGON_API_KEY 未设置，API 调用将失败")
    
    async def start(self) -> None:
        """启动数据源"""
        timeout = aiohttp.ClientTimeout(total=30)
        self.session = aiohttp.ClientSession(timeout=timeout)
        self.logger.info(f"Polygon 数据源启动: {self.symbol}")
    
    async def stop(self) -> None:
        """停止数据源"""
        if self.session and not self.session.closed:
            await self.session.close()
        self.logger.info("Polygon 数据源已停止")
    
    async def get_klines(
        self,
        timeframe: Timeframe,
        limit: int = 500,
    ) -> List[Kline]:
        """
        获取 K 线数据
        
        Args:
            timeframe: K 线周期
            limit: 获取数量
            
        Returns:
            K 线列表（按时间升序）
        """
        # 检查缓存
        if timeframe in self._kline_cache and len(self._kline_cache[timeframe]) >= limit:
            return self._kline_cache[timeframe][-limit:]
        
        # 计算时间范围
        end_date = datetime.now()
        
        # 根据周期计算起始时间
        # 注意：美股每天只交易 6.5 小时，4H K线每天约 2 条
        if timeframe in [Timeframe.M1, Timeframe.M5]:
            # 分钟级：获取最近 7 天（免费套餐限制）
            start_date = end_date - timedelta(days=7)
        elif timeframe in [Timeframe.M15, Timeframe.M30]:
            # 15/30分钟：获取最近 30 天
            start_date = end_date - timedelta(days=30)
        elif timeframe == Timeframe.H1:
            # 1小时：获取最近 180 天（约 180 × 7 = 1260 条）
            start_date = end_date - timedelta(days=180)
        elif timeframe == Timeframe.H4:
            # 4小时：获取最近 365 天（约 365 × 2 = 730 条）
            start_date = end_date - timedelta(days=365)
        elif timeframe == Timeframe.D1:
            # 日线：获取最近 2 年
            start_date = end_date - timedelta(days=730)
        else:
            # 周线：获取最近 5 年
            start_date = end_date - timedelta(days=1825)
        
        klines = await self._fetch_klines(timeframe, start_date, end_date, limit)
        
        if klines:
            self._kline_cache[timeframe] = klines
        
        return klines
    
    async def _fetch_klines(
        self,
        timeframe: Timeframe,
        start_date: datetime,
        end_date: datetime,
        limit: int,
    ) -> List[Kline]:
        """
        从 Polygon API 获取 K 线数据
        """
        if not self.api_key:
            self.logger.error("❌ POLYGON_API_KEY 未设置")
            return []
        
        if not self.session:
            await self.start()
        
        # 限频等待
        await self._rate_limit_wait()
        
        # 获取 Polygon API 参数
        if timeframe not in self.TIMEFRAME_MAP:
            self.logger.error(f"不支持的周期: {timeframe}")
            return []
        
        multiplier, timespan = self.TIMEFRAME_MAP[timeframe]
        
        # 构造 URL
        # GET /v2/aggs/ticker/{stocksTicker}/range/{multiplier}/{timespan}/{from}/{to}
        from_date = start_date.strftime("%Y-%m-%d")
        to_date = end_date.strftime("%Y-%m-%d")
        
        url = (
            f"{self.BASE_URL}/v2/aggs/ticker/{self.symbol}/range/"
            f"{multiplier}/{timespan}/{from_date}/{to_date}"
        )
        
        params = {
            "apiKey": self.api_key,
            "adjusted": "true",  # 调整后价格
            "sort": "asc",       # 升序
            "limit": min(limit, 50000),  # Polygon 最大 50000
        }
        
        try:
            self.logger.info(
                f"Polygon API 请求: {self.symbol} {timeframe.value}, "
                f"范围: {from_date} ~ {to_date}, limit={params['limit']}"
            )
            
            async with self.session.get(url, params=params) as response:
                self._last_request_time = time.time()
                
                if response.status == 200:
                    data = await response.json()
                    
                    if data.get("status") == "OK" and "results" in data:
                        results = data["results"]
                        self.logger.info(
                            f"Polygon API 返回 {len(results)} 条原始数据, "
                            f"resultsCount={data.get('resultsCount', 'N/A')}"
                        )
                        klines = []
                        
                        for bar in results[-limit:]:
                            kline = Kline(
                                timestamp=bar["t"],  # 毫秒时间戳
                                open=float(bar["o"]),
                                high=float(bar["h"]),
                                low=float(bar["l"]),
                                close=float(bar["c"]),
                                volume=float(bar.get("v", 0)),
                                quote_volume=float(bar.get("vw", 0) * bar.get("v", 0)),  # 加权均价 * 成交量
                                trades=int(bar.get("n", 0)),  # 成交笔数
                                is_closed=True,  # 历史数据都是已收盘的
                            )
                            klines.append(kline)
                        
                        self.logger.info(
                            f"✅ Polygon 获取 {self.symbol} {timeframe.value}: {len(klines)} 条"
                        )
                        return klines
                    else:
                        self.logger.warning(
                            f"Polygon API 返回异常: {data.get('status')}, {data.get('message', '')}"
                        )
                        return []
                
                elif response.status == 403:
                    self.logger.error("❌ Polygon API Key 无效或权限不足")
                    return []
                
                elif response.status == 429:
                    self.logger.warning("⚠️ Polygon API 限频，等待后重试")
                    await asyncio.sleep(60)  # 等待 1 分钟
                    return await self._fetch_klines(timeframe, start_date, end_date, limit)
                
                else:
                    text = await response.text()
                    self.logger.error(f"Polygon API 错误 {response.status}: {text[:200]}")
                    return []
                    
        except asyncio.TimeoutError:
            self.logger.error("Polygon API 请求超时")
            return []
        except Exception as e:
            self.logger.error(f"Polygon API 异常: {e}", exc_info=True)
            return []
    
    async def _rate_limit_wait(self) -> None:
        """限频等待"""
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_request_interval:
            wait_time = self._min_request_interval - elapsed
            await asyncio.sleep(wait_time)
    
    def get_cached_klines(self, timeframe: Timeframe) -> List[Kline]:
        """获取缓存的 K 线（同步方法）"""
        return self._kline_cache.get(timeframe, [])
    
    def get_current_price(self) -> float:
        """获取当前价格（从最新 K 线）"""
        # 优先使用最小周期的最新数据
        for tf in [Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4, Timeframe.D1]:
            if tf in self._kline_cache and self._kline_cache[tf]:
                return self._kline_cache[tf][-1].close
        return 0.0


async def get_polygon_klines(
    symbol: str,
    timeframes: List[str],
    limit: int = 500,
    api_key: Optional[str] = None,
) -> Dict[str, List[Kline]]:
    """
    便捷函数：获取指定标的的多周期 K 线
    
    Args:
        symbol: 股票代码（如 TSLA, AAPL）
        timeframes: 周期列表（如 ["4h", "1d"]）
        limit: 每个周期获取数量
        api_key: Polygon API Key（可选）
        
    Returns:
        {timeframe: [Kline, ...], ...}
    """
    feed = PolygonKlineFeed(symbol, api_key=api_key)
    await feed.start()
    
    result = {}
    try:
        for tf_str in timeframes:
            tf = Timeframe.from_string(tf_str)
            klines = await feed.get_klines(tf, limit)
            result[tf_str] = klines
    finally:
        await feed.stop()
    
    return result
