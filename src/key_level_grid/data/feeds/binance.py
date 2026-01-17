"""
币安 K线数据源

使用币安 Futures API 获取 USDT 永续合约 K线数据
API 文档: https://binance-docs.github.io/apidocs/futures/cn/
"""

import asyncio
import time
from typing import Callable, Dict, List, Optional

import aiohttp

from key_level_grid.utils.logger import get_logger
from key_level_grid.core.models import (
    Kline,
    KlineFeedConfig,
    Timeframe,
)


class BinanceKlineFeed:
    """
    币安 K线数据源
    
    功能:
    1. REST API 获取历史 K线
    2. WebSocket 订阅实时 K线
    3. 多周期数据缓存
    4. 断线重连与限频保护
    """
    
    BASE_URL = "https://fapi.binance.com"
    WS_URL = "wss://fstream.binance.com/ws"
    
    def __init__(self, config: KlineFeedConfig):
        self.config = config
        self.session: Optional[aiohttp.ClientSession] = None
        self.ws: Optional[aiohttp.ClientWebSocketResponse] = None
        
        # 多周期数据缓存: timeframe -> List[Kline]
        self._kline_cache: Dict[Timeframe, List[Kline]] = {}
        
        # 最后更新时间
        self._last_update: Dict[Timeframe, int] = {}
        
        # 限频控制
        self._last_request_time: float = 0
        self._min_request_interval: float = 0.1  # 100ms
        
        # 运行状态
        self._running = False
        self._ws_task: Optional[asyncio.Task] = None
        
        self.logger = get_logger(__name__)
    
    async def start(self) -> None:
        """启动数据源"""
        if self._running:
            return
        
        self._running = True
        
        # 创建 HTTP session
        timeout = aiohttp.ClientTimeout(total=self.config.request_timeout_sec)
        self.session = aiohttp.ClientSession(timeout=timeout)
        
        # 加载所有周期的历史数据
        await self._load_history(self.config.primary_timeframe)
        for tf in self.config.auxiliary_timeframes:
            await self._load_history(tf)
        
        self.logger.info(
            f"K线数据源启动: {self.config.symbol}, "
            f"主周期={self.config.primary_timeframe.value}, "
            f"辅助周期={[tf.value for tf in self.config.auxiliary_timeframes]}"
        )
    
    async def stop(self) -> None:
        """停止数据源"""
        self._running = False
        
        # 关闭 WebSocket
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        
        if self.ws and not self.ws.closed:
            await self.ws.close()
        
        # 关闭 HTTP session
        if self.session and not self.session.closed:
            await self.session.close()
        
        self.logger.info("K线数据源已停止")
    
    async def _load_history(self, timeframe: Timeframe) -> None:
        """加载历史K线"""
        url = f"{self.BASE_URL}/fapi/v1/klines"
        params = {
            "symbol": self.config.symbol,
            "interval": timeframe.value,
            "limit": min(self.config.history_bars, 1500)
        }
        
        for attempt in range(self.config.max_retries):
            try:
                await self._rate_limit()
                
                async with self.session.get(url, params=params) as resp:
                    if resp.status == 429 or resp.status == 418:
                        delay = self.config.retry_base_delay_sec * (2 ** attempt)
                        self.logger.warning(
                            f"币安限频 ({resp.status})，等待 {delay:.1f}s 后重试"
                        )
                        await asyncio.sleep(delay)
                        continue
                    
                    if resp.status != 200:
                        text = await resp.text()
                        raise Exception(f"获取K线失败: {resp.status}, {text}")
                    
                    data = await resp.json()
                    klines = [self._parse_kline(k) for k in data]
                    self._kline_cache[timeframe] = klines
                    self._last_update[timeframe] = klines[-1].timestamp if klines else 0
                    
                    self.logger.info(
                        f"加载历史K线: {timeframe.value}, 数量={len(klines)}"
                    )
                    return
                    
            except asyncio.TimeoutError:
                self.logger.warning(f"请求超时，重试 {attempt + 1}/{self.config.max_retries}")
                await asyncio.sleep(self.config.retry_base_delay_sec * (2 ** attempt))
            except Exception as e:
                self.logger.error(f"加载历史K线失败: {e}")
                if attempt == self.config.max_retries - 1:
                    raise
                await asyncio.sleep(self.config.retry_base_delay_sec * (2 ** attempt))
    
    def _parse_kline(self, data: list) -> Kline:
        """解析币安K线数据"""
        return Kline(
            timestamp=data[0],
            open=float(data[1]),
            high=float(data[2]),
            low=float(data[3]),
            close=float(data[4]),
            volume=float(data[5]),
            quote_volume=float(data[7]),
            trades=int(data[8]),
            is_closed=True
        )
    
    async def _rate_limit(self) -> None:
        """限频控制"""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._min_request_interval:
            await asyncio.sleep(self._min_request_interval - elapsed)
        self._last_request_time = time.time()
    
    async def get_latest_klines(
        self,
        timeframe: Timeframe,
        count: int = 200
    ) -> List[Kline]:
        """获取最新K线数据"""
        if timeframe not in self._kline_cache:
            await self._load_history(timeframe)
        
        klines = self._kline_cache.get(timeframe, [])
        return klines[-count:] if len(klines) > count else klines.copy()
    
    async def update_latest(self, timeframe: Timeframe) -> Optional[Kline]:
        """更新最新K线"""
        url = f"{self.BASE_URL}/fapi/v1/klines"
        params = {
            "symbol": self.config.symbol,
            "interval": timeframe.value,
            "limit": 2
        }
        
        try:
            await self._rate_limit()
            
            async with self.session.get(url, params=params) as resp:
                if resp.status != 200:
                    self.logger.warning(f"获取实时K线失败: {resp.status}")
                    return None
                
                data = await resp.json()
                if not data:
                    return None
                
                for k_data in data:
                    kline = self._parse_kline(k_data)
                    close_time = k_data[6]
                    kline.is_closed = close_time < int(time.time() * 1000)
                    self._update_cache(timeframe, kline)
                
                return self._kline_cache[timeframe][-1] if timeframe in self._kline_cache else None
                
        except Exception as e:
            self.logger.warning(f"更新实时K线失败: {e}")
            return None
    
    def _update_cache(self, timeframe: Timeframe, kline: Kline) -> None:
        """更新K线缓存"""
        if timeframe not in self._kline_cache:
            self._kline_cache[timeframe] = []
        
        cache = self._kline_cache[timeframe]
        
        if cache and cache[-1].timestamp == kline.timestamp:
            cache[-1] = kline
        else:
            cache.append(kline)
            if len(cache) > self.config.history_bars:
                cache.pop(0)
        
        self._last_update[timeframe] = kline.timestamp
    
    def get_cached_klines(self, timeframe: Timeframe) -> List[Kline]:
        """获取缓存的K线"""
        return self._kline_cache.get(timeframe, []).copy()
    
    async def subscribe_kline_close(
        self,
        callback: Callable[[Kline], None]
    ) -> None:
        """订阅K线收盘事件"""
        stream_name = f"{self.config.symbol.lower()}@kline_{self.config.primary_timeframe.value}"
        ws_url = f"{self.WS_URL}/{stream_name}"
        
        while self._running:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(ws_url) as ws:
                        self.ws = ws
                        self.logger.info(f"WebSocket 已连接: {stream_name}")
                        
                        async for msg in ws:
                            if not self._running:
                                break
                            
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                await self._handle_ws_message(msg.json(), callback)
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                self.logger.error(f"WebSocket 错误: {msg.data}")
                                break
                            elif msg.type == aiohttp.WSMsgType.CLOSED:
                                self.logger.warning("WebSocket 已关闭")
                                break
                                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"WebSocket 异常: {e}")
            
            if self._running:
                self.logger.info(
                    f"WebSocket 断开，{self.config.ws_reconnect_delay_sec}s 后重连..."
                )
                await asyncio.sleep(self.config.ws_reconnect_delay_sec)
    
    async def _handle_ws_message(
        self,
        data: dict,
        callback: Callable[[Kline], None]
    ) -> None:
        """处理 WebSocket 消息"""
        kline_data = data.get("k", {})
        if not kline_data:
            return
        
        is_closed = kline_data.get("x", False)
        
        kline = Kline(
            timestamp=kline_data["t"],
            open=float(kline_data["o"]),
            high=float(kline_data["h"]),
            low=float(kline_data["l"]),
            close=float(kline_data["c"]),
            volume=float(kline_data["v"]),
            quote_volume=float(kline_data["q"]),
            trades=kline_data["n"],
            is_closed=is_closed
        )
        
        self._update_cache(self.config.primary_timeframe, kline)
        
        if is_closed:
            self.logger.debug(
                f"K线收盘: {self.config.symbol} {self.config.primary_timeframe.value} "
                f"O={kline.open} H={kline.high} L={kline.low} C={kline.close}"
            )
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(kline)
                else:
                    callback(kline)
            except Exception as e:
                self.logger.error(f"K线收盘回调异常: {e}")
    
    def start_ws_subscription(self, callback: Callable[[Kline], None]) -> asyncio.Task:
        """启动 WebSocket 订阅"""
        self._ws_task = asyncio.create_task(self.subscribe_kline_close(callback))
        return self._ws_task
    
    async def check_and_fill_gaps(self, timeframe: Timeframe) -> int:
        """检查并填补K线缺口"""
        cache = self._kline_cache.get(timeframe, [])
        if len(cache) < 2:
            return 0
        
        interval_ms = timeframe.to_milliseconds()
        gaps = []
        
        for i in range(1, len(cache)):
            expected_ts = cache[i - 1].timestamp + interval_ms
            actual_ts = cache[i].timestamp
            
            if actual_ts > expected_ts:
                gap_count = (actual_ts - expected_ts) // interval_ms
                if gap_count > 0:
                    gaps.append((expected_ts, gap_count))
        
        if not gaps:
            return 0
        
        total_filled = 0
        for gap_start, gap_count in gaps:
            self.logger.warning(
                f"检测到K线缺口: {timeframe.value}, 起始={gap_start}, 数量={gap_count}"
            )
            await self._load_history(timeframe)
            total_filled += gap_count
        
        return total_filled
    
    def get_stats(self) -> dict:
        """获取数据源统计信息"""
        return {
            "symbol": self.config.symbol,
            "primary_timeframe": self.config.primary_timeframe.value,
            "auxiliary_timeframes": [tf.value for tf in self.config.auxiliary_timeframes],
            "cache_sizes": {
                tf.value: len(klines) for tf, klines in self._kline_cache.items()
            },
            "last_updates": {
                tf.value: ts for tf, ts in self._last_update.items()
            },
            "running": self._running,
            "ws_connected": self.ws is not None and not self.ws.closed,
        }
