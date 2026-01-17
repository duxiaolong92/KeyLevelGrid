"""
Polygon.io 美股 K线数据源

使用 Polygon.io REST API 获取美股历史 K 线数据
API 文档: https://polygon.io/docs/stocks/
"""

import asyncio
import os
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import aiohttp

from key_level_grid.utils.logger import get_logger
from key_level_grid.core.models import Kline, Timeframe


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
        rate_limit: float = 5.0,
    ):
        self.symbol = symbol.upper()
        self.api_key = api_key or os.getenv("POLYGON_API_KEY", "")
        self.rate_limit = rate_limit
        
        self.session: Optional[aiohttp.ClientSession] = None
        
        self._kline_cache: Dict[Timeframe, List[Kline]] = {}
        
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
        """获取 K 线数据"""
        if timeframe in self._kline_cache and len(self._kline_cache[timeframe]) >= limit:
            return self._kline_cache[timeframe][-limit:]
        
        end_date = datetime.now()
        
        if timeframe in [Timeframe.M1, Timeframe.M5]:
            start_date = end_date - timedelta(days=7)
        elif timeframe in [Timeframe.M15, Timeframe.M30]:
            start_date = end_date - timedelta(days=30)
        elif timeframe == Timeframe.H1:
            start_date = end_date - timedelta(days=180)
        elif timeframe == Timeframe.H4:
            start_date = end_date - timedelta(days=365)
        elif timeframe == Timeframe.D1:
            start_date = end_date - timedelta(days=730)
        else:
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
        """从 Polygon API 获取 K 线数据"""
        if not self.api_key:
            self.logger.error("❌ POLYGON_API_KEY 未设置")
            return []
        
        if not self.session:
            await self.start()
        
        await self._rate_limit_wait()
        
        if timeframe not in self.TIMEFRAME_MAP:
            self.logger.error(f"不支持的周期: {timeframe}")
            return []
        
        multiplier, timespan = self.TIMEFRAME_MAP[timeframe]
        
        from_date = start_date.strftime("%Y-%m-%d")
        to_date = end_date.strftime("%Y-%m-%d")
        
        url = (
            f"{self.BASE_URL}/v2/aggs/ticker/{self.symbol}/range/"
            f"{multiplier}/{timespan}/{from_date}/{to_date}"
        )
        
        params = {
            "apiKey": self.api_key,
            "adjusted": "true",
            "sort": "asc",
            "limit": min(limit, 50000),
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
                            f"Polygon API 返回 {len(results)} 条原始数据"
                        )
                        klines = []
                        
                        for bar in results[-limit:]:
                            kline = Kline(
                                timestamp=bar["t"],
                                open=float(bar["o"]),
                                high=float(bar["h"]),
                                low=float(bar["l"]),
                                close=float(bar["c"]),
                                volume=float(bar.get("v", 0)),
                                quote_volume=float(bar.get("vw", 0) * bar.get("v", 0)),
                                trades=int(bar.get("n", 0)),
                                is_closed=True,
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
                    await asyncio.sleep(60)
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
        """获取缓存的 K 线"""
        return self._kline_cache.get(timeframe, [])
    
    def get_current_price(self) -> float:
        """获取当前价格"""
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
    """便捷函数：获取指定标的的多周期 K 线"""
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
