"""
Gate.io 期货 K线数据源

使用 Gate.io Futures API 获取 USDT 永续合约 K线数据
API 文档: https://www.gate.io/docs/developers/apiv4/zh_CN/
"""

import asyncio
import json
import time
from typing import Callable, Dict, List, Optional

import aiohttp

from key_level_grid.utils.logger import get_logger
from key_level_grid.core.models import (
    Kline,
    KlineFeedConfig,
    Timeframe,
)


class GateKlineFeed:
    """
    Gate.io 期货 K线数据源
    
    功能:
    1. REST API 获取历史 K线
    2. WebSocket 订阅实时 K线
    3. 多周期数据缓存
    4. 断线重连与限频保护
    """
    
    BASE_URL = "https://api.gateio.ws/api/v4"
    WS_URL = "wss://fx-ws.gateio.ws/v4/ws/usdt"
    
    # 周期映射: Timeframe -> Gate API interval
    TIMEFRAME_MAP = {
        Timeframe.M1: "1m",
        Timeframe.M5: "5m",
        Timeframe.M15: "15m",
        Timeframe.M30: "30m",
        Timeframe.H1: "1h",
        Timeframe.H4: "4h",
        Timeframe.D1: "1d",
        Timeframe.W1: "7d",
    }
    
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
    
    def _convert_symbol(self, symbol: str) -> str:
        """转换交易对格式: BTCUSDT -> BTC_USDT"""
        symbol = symbol.upper()
        
        if "_" in symbol:
            return symbol
        
        for suffix in ["USDT", "USD", "BUSD", "USDC"]:
            if symbol.endswith(suffix):
                base = symbol[:-len(suffix)]
                return f"{base}_{suffix}"
        
        return f"{symbol}_USDT"
    
    async def start(self) -> None:
        """启动数据源"""
        if self._running:
            return
        
        self._running = True
        
        timeout = aiohttp.ClientTimeout(total=self.config.request_timeout_sec)
        self.session = aiohttp.ClientSession(timeout=timeout)
        
        await self._load_history(self.config.primary_timeframe)
        for tf in self.config.auxiliary_timeframes:
            await self._load_history(tf)
        
        self.logger.info(
            f"Gate K线数据源启动: {self.config.symbol}, "
            f"主周期={self.config.primary_timeframe.value}, "
            f"辅助周期={[tf.value for tf in self.config.auxiliary_timeframes]}"
        )
    
    async def stop(self) -> None:
        """停止数据源"""
        self._running = False
        
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        
        if self.ws and not self.ws.closed:
            await self.ws.close()
        
        if self.session and not self.session.closed:
            await self.session.close()
        
        self.logger.info("Gate K线数据源已停止")
    
    async def _load_history(self, timeframe: Timeframe) -> None:
        """加载历史 K线数据"""
        try:
            klines = await self._fetch_klines(timeframe, self.config.history_bars)
            self._kline_cache[timeframe] = klines
            self._last_update[timeframe] = int(time.time() * 1000)
            
            self.logger.info(
                f"加载历史数据: {self.config.symbol} {timeframe.value}, "
                f"数量={len(klines)}"
            )
        except Exception as e:
            self.logger.error(f"加载历史数据失败: {timeframe.value}, {e}")
            self._kline_cache[timeframe] = []
    
    async def _fetch_klines(
        self,
        timeframe: Timeframe,
        limit: int = 500
    ) -> List[Kline]:
        """获取 K线数据"""
        await self._rate_limit()
        
        gate_symbol = self._convert_symbol(self.config.symbol)
        interval = self.TIMEFRAME_MAP.get(timeframe, "4h")
        
        url = f"{self.BASE_URL}/futures/usdt/candlesticks"
        params = {
            "contract": gate_symbol,
            "interval": interval,
            "limit": min(limit, 2000),
        }
        
        try:
            async with self.session.get(url, params=params) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise Exception(f"Gate API 错误: {resp.status} - {error_text}")
                
                data = await resp.json()
                
                klines = []
                for item in data:
                    try:
                        kline = Kline(
                            timestamp=int(item["t"]) * 1000,
                            open=float(item["o"]),
                            high=float(item["h"]),
                            low=float(item["l"]),
                            close=float(item["c"]),
                            volume=float(item["v"]),
                            quote_volume=float(item.get("sum", 0)),
                            trades=0,
                            is_closed=True,
                        )
                        klines.append(kline)
                    except (KeyError, ValueError) as e:
                        self.logger.warning(f"解析 K线失败: {item}, {e}")
                        continue
                
                if klines:
                    from datetime import datetime
                    first_ts = datetime.fromtimestamp(klines[0].timestamp / 1000)
                    last_ts = datetime.fromtimestamp(klines[-1].timestamp / 1000)
                    self.logger.debug(
                        f"Gate K线数据: {len(klines)}条, "
                        f"首条={first_ts} 价格={klines[0].close:.2f}, "
                        f"末条={last_ts} 价格={klines[-1].close:.2f}"
                    )
                
                return klines
                
        except aiohttp.ClientError as e:
            self.logger.error(f"Gate API 请求失败: {e}")
            return []
    
    async def _rate_limit(self) -> None:
        """限频控制"""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._min_request_interval:
            await asyncio.sleep(self._min_request_interval - elapsed)
        self._last_request_time = time.time()
    
    def get_cached_klines(self, timeframe: Timeframe) -> List[Kline]:
        """获取缓存的 K线数据"""
        return self._kline_cache.get(timeframe, [])
    
    async def get_latest_klines(self, timeframe: Timeframe) -> List[Kline]:
        """获取最新 K线数据"""
        klines = await self._fetch_klines(timeframe, self.config.history_bars)
        if klines:
            self._kline_cache[timeframe] = klines
            self._last_update[timeframe] = int(time.time() * 1000)
        return klines
    
    async def update_latest(self, timeframe: Timeframe) -> Optional[Kline]:
        """更新最新K线"""
        gate_symbol = self._convert_symbol(self.config.symbol)
        interval = self.TIMEFRAME_MAP.get(timeframe, "4h")
        
        url = f"{self.BASE_URL}/futures/usdt/candlesticks"
        params = {
            "contract": gate_symbol,
            "interval": interval,
            "limit": 2,
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
                
                for item in data:
                    kline = Kline(
                        timestamp=int(item["t"]) * 1000,
                        open=float(item["o"]),
                        high=float(item["h"]),
                        low=float(item["l"]),
                        close=float(item["c"]),
                        volume=float(item["v"]),
                        quote_volume=float(item.get("sum", 0)),
                        trades=0,
                        is_closed=True,
                    )
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
    
    async def subscribe_kline_close(
        self,
        callback: Callable[[Kline], None]
    ) -> None:
        """订阅K线收盘事件"""
        gate_symbol = self._convert_symbol(self.config.symbol)
        interval = self.TIMEFRAME_MAP.get(self.config.primary_timeframe, "4h")
        
        while self._running:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(self.WS_URL) as ws:
                        self.ws = ws
                        
                        subscribe_msg = {
                            "time": int(time.time()),
                            "channel": "futures.candlesticks",
                            "event": "subscribe",
                            "payload": [interval, gate_symbol]
                        }
                        await ws.send_json(subscribe_msg)
                        self.logger.info(f"Gate WebSocket 已连接，订阅: {gate_symbol} {interval}")
                        
                        async for msg in ws:
                            if not self._running:
                                break
                            
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    data = json.loads(msg.data)
                                    await self._handle_ws_message(data, callback)
                                except json.JSONDecodeError:
                                    self.logger.warning(f"无效的 JSON: {msg.data[:100]}")
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
        """处理 Gate WebSocket 消息"""
        if data.get("event") != "update":
            return
        
        if data.get("channel") != "futures.candlesticks":
            return
        
        result_list = data.get("result")
        if not result_list or not isinstance(result_list, list):
            return
        
        for result in result_list:
            try:
                timestamp = int(result["t"]) * 1000
                is_closed = result.get("w", False)
                
                kline = Kline(
                    timestamp=timestamp,
                    open=float(result["o"]),
                    high=float(result["h"]),
                    low=float(result["l"]),
                    close=float(result["c"]),
                    volume=float(result.get("v", 0)),
                    quote_volume=float(result.get("a", 0)),
                    trades=0,
                    is_closed=is_closed,
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
                
            except (KeyError, ValueError) as e:
                self.logger.warning(f"解析 WS K线失败: {result}, {e}")
    
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
