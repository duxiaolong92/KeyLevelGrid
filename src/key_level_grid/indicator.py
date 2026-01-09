"""
关键位网格指标计算模块

计算辅助指标: MACD, RSI, ATR, ADX, 量比等
不再使用 EMA 144/169 通道指标
"""

from dataclasses import dataclass
from typing import List, Optional

from key_level_grid.utils.logger import get_logger
from key_level_grid.models import Kline, KeyLevelGridState


@dataclass
class IndicatorConfig:
    """指标配置"""
    # MACD
    macd_enabled: bool = True
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    
    # RSI
    rsi_enabled: bool = True
    rsi_period: int = 14
    
    # ATR
    atr_enabled: bool = True
    atr_period: int = 14
    
    # ADX (趋势强度)
    adx_enabled: bool = True
    adx_period: int = 14
    
    # 成交量
    volume_ma_period: int = 20


class KeyLevelGridIndicator:
    """
    关键位网格指标计算器
    
    计算辅助指标用于交易决策:
    - MACD: 动量和趋势
    - RSI: 超买超卖
    - ATR: 波动率 (用于止损计算)
    - ADX: 趋势强度 (用于网格模式选择)
    - 量比: 成交量异常检测
    """
    
    def __init__(self, config: Optional[IndicatorConfig] = None, symbol: str = ""):
        self.config = config or IndicatorConfig()
        self.symbol = symbol
        self.logger = get_logger(__name__)
    
    def calculate(self, klines: List[Kline]) -> KeyLevelGridState:
        """
        计算市场状态
        
        Args:
            klines: K线列表 (至少需要 macd_slow + adx_period 根)
            
        Returns:
            KeyLevelGridState 对象
        """
        min_required = max(self.config.macd_slow, self.config.adx_period) + 20
        if len(klines) < min_required:
            raise ValueError(
                f"K线数量不足: 需要 {min_required} 根，实际 {len(klines)} 根"
            )
        
        closes = [k.close for k in klines]
        highs = [k.high for k in klines]
        lows = [k.low for k in klines]
        volumes = [k.volume for k in klines]
        latest = klines[-1]
        
        # MACD
        macd, macd_signal_val, macd_histogram = None, None, None
        if self.config.macd_enabled and len(closes) >= self.config.macd_slow:
            macd, macd_signal_val, macd_histogram = self._calculate_macd(closes)
        
        # RSI
        rsi = None
        if self.config.rsi_enabled and len(closes) >= self.config.rsi_period + 1:
            rsi = self._calculate_rsi(closes, self.config.rsi_period)
        
        # ATR
        atr = None
        if self.config.atr_enabled and len(klines) >= self.config.atr_period + 1:
            atr = self._calculate_atr(klines, self.config.atr_period)
        
        # ADX
        adx = None
        if self.config.adx_enabled and len(klines) >= self.config.adx_period + 1:
            adx = self._calculate_adx(highs, lows, closes, self.config.adx_period)
        
        # 成交量均值和量比
        volume_ma = None
        volume_ratio = None
        if len(volumes) >= self.config.volume_ma_period:
            volume_ma = sum(volumes[-self.config.volume_ma_period:]) / self.config.volume_ma_period
            volume_ratio = latest.volume / volume_ma if volume_ma > 0 else 0
        
        return KeyLevelGridState(
            timestamp=latest.timestamp,
            symbol=self.symbol,
            open=latest.open,
            high=latest.high,
            low=latest.low,
            close=latest.close,
            volume=latest.volume,
            macd=macd,
            macd_signal=macd_signal_val,
            macd_histogram=macd_histogram,
            rsi=rsi,
            atr=atr,
            adx=adx,
            volume_ma=volume_ma,
            volume_ratio=volume_ratio,
        )
    
    def _calculate_ema(self, prices: List[float], period: int) -> float:
        """
        计算指数移动平均线 (EMA)
        
        EMA = Price(t) × k + EMA(y) × (1 − k)
        其中 k = 2 / (N + 1)
        """
        if len(prices) < period:
            return prices[-1] if prices else 0
        
        multiplier = 2 / (period + 1)
        
        # 使用 SMA 作为起始值
        sma = sum(prices[:period]) / period
        ema = sma
        
        # 计算 EMA
        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema
        
        return ema
    
    def _calculate_ema_series(self, prices: List[float], period: int) -> List[float]:
        """
        计算 EMA 序列
        
        返回与 prices 等长的 EMA 列表
        """
        if len(prices) < period:
            return [prices[-1]] * len(prices) if prices else []
        
        multiplier = 2 / (period + 1)
        ema_list = []
        
        # 前 period-1 个点用 SMA
        for i in range(period - 1):
            ema_list.append(sum(prices[:i+1]) / (i + 1))
        
        # 第 period 个点开始用真正的 EMA
        sma = sum(prices[:period]) / period
        ema_list.append(sma)
        ema = sma
        
        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema
            ema_list.append(ema)
        
        return ema_list
    
    def _calculate_macd(self, prices: List[float]) -> tuple:
        """
        计算 MACD 指标
        
        Returns:
            (MACD线, 信号线, 柱状图)
        """
        ema_fast = self._calculate_ema(prices, self.config.macd_fast)
        ema_slow = self._calculate_ema(prices, self.config.macd_slow)
        macd_line = ema_fast - ema_slow
        
        # 计算 MACD 序列用于信号线
        ema_fast_series = self._calculate_ema_series(prices, self.config.macd_fast)
        ema_slow_series = self._calculate_ema_series(prices, self.config.macd_slow)
        
        # MACD 序列 (从 ema_slow 周期开始才有意义)
        macd_series = []
        for i in range(len(prices)):
            if i >= self.config.macd_slow - 1:
                macd_series.append(ema_fast_series[i] - ema_slow_series[i])
        
        if len(macd_series) < self.config.macd_signal:
            return macd_line, macd_line, 0.0
        
        # 信号线 = MACD 的 EMA
        signal_line = self._calculate_ema(macd_series, self.config.macd_signal)
        histogram = macd_line - signal_line
        
        return macd_line, signal_line, histogram
    
    def _calculate_rsi(self, prices: List[float], period: int) -> float:
        """
        计算 RSI 指标
        
        RSI = 100 - (100 / (1 + RS))
        RS = 平均涨幅 / 平均跌幅
        """
        if len(prices) < period + 1:
            return 50.0
        
        # 计算价格变化
        changes = [prices[i] - prices[i-1] for i in range(1, len(prices))]
        
        # 分离涨跌
        gains = [max(0, c) for c in changes]
        losses = [max(0, -c) for c in changes]
        
        # 取最近 period 个
        recent_gains = gains[-period:]
        recent_losses = losses[-period:]
        
        avg_gain = sum(recent_gains) / period
        avg_loss = sum(recent_losses) / period
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def _calculate_atr(self, klines: List[Kline], period: int) -> float:
        """
        计算 ATR (平均真实波幅)
        
        TR = max(H-L, |H-C_prev|, |L-C_prev|)
        ATR = TR 的 EMA
        """
        if len(klines) < period + 1:
            return 0.0
        
        tr_list = []
        for i in range(1, len(klines)):
            high = klines[i].high
            low = klines[i].low
            prev_close = klines[i - 1].close
            
            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            tr_list.append(tr)
        
        # ATR = TR 的 EMA
        return self._calculate_ema(tr_list, period)
    
    def _calculate_adx(
        self,
        highs: List[float],
        lows: List[float],
        closes: List[float],
        period: int
    ) -> float:
        """
        计算 ADX (平均趋向指数)
        
        ADX 用于衡量趋势强度:
        - ADX < 20: 无趋势/震荡
        - ADX 20-40: 弱趋势
        - ADX 40-60: 强趋势
        - ADX > 60: 极强趋势
        """
        if len(highs) < period + 1:
            return 0.0
        
        # 计算 +DM 和 -DM
        plus_dm = []
        minus_dm = []
        tr_list = []
        
        for i in range(1, len(highs)):
            high_diff = highs[i] - highs[i-1]
            low_diff = lows[i-1] - lows[i]
            
            # +DM
            if high_diff > low_diff and high_diff > 0:
                plus_dm.append(high_diff)
            else:
                plus_dm.append(0)
            
            # -DM
            if low_diff > high_diff and low_diff > 0:
                minus_dm.append(low_diff)
            else:
                minus_dm.append(0)
            
            # TR
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1])
            )
            tr_list.append(tr)
        
        if len(tr_list) < period:
            return 0.0
        
        # 平滑 +DI, -DI, TR
        smoothed_plus_dm = self._calculate_ema(plus_dm, period)
        smoothed_minus_dm = self._calculate_ema(minus_dm, period)
        smoothed_tr = self._calculate_ema(tr_list, period)
        
        if smoothed_tr == 0:
            return 0.0
        
        # +DI 和 -DI
        plus_di = 100 * smoothed_plus_dm / smoothed_tr
        minus_di = 100 * smoothed_minus_dm / smoothed_tr
        
        # DX
        di_sum = plus_di + minus_di
        if di_sum == 0:
            return 0.0
        
        dx = 100 * abs(plus_di - minus_di) / di_sum
        
        # ADX = DX 的 EMA (简化计算，直接返回 DX)
        # 完整版应该计算 DX 序列的 EMA
        return dx
    
    def is_golden_cross(self, klines: List[Kline], lookback: int = 3) -> bool:
        """
        检查是否发生 MACD 金叉
        
        Args:
            klines: K线列表
            lookback: 回看K线数 (在这几根内发生金叉)
            
        Returns:
            是否金叉
        """
        if not self.config.macd_enabled:
            return False
        
        if len(klines) < self.config.macd_slow + lookback:
            return False
        
        # 计算历史和当前 MACD
        for i in range(lookback):
            current_idx = len(klines) - i
            prev_idx = current_idx - 1
            
            if prev_idx < self.config.macd_slow:
                continue
            
            current_closes = [k.close for k in klines[:current_idx]]
            prev_closes = [k.close for k in klines[:prev_idx]]
            
            _, curr_signal, curr_hist = self._calculate_macd(current_closes)
            _, prev_signal, prev_hist = self._calculate_macd(prev_closes)
            
            # 金叉: 柱状图从负变正
            if prev_hist < 0 and curr_hist > 0:
                return True
        
        return False
    
    def is_death_cross(self, klines: List[Kline], lookback: int = 3) -> bool:
        """
        检查是否发生 MACD 死叉
        
        Args:
            klines: K线列表
            lookback: 回看K线数
            
        Returns:
            是否死叉
        """
        if not self.config.macd_enabled:
            return False
        
        if len(klines) < self.config.macd_slow + lookback:
            return False
        
        for i in range(lookback):
            current_idx = len(klines) - i
            prev_idx = current_idx - 1
            
            if prev_idx < self.config.macd_slow:
                continue
            
            current_closes = [k.close for k in klines[:current_idx]]
            prev_closes = [k.close for k in klines[:prev_idx]]
            
            _, _, curr_hist = self._calculate_macd(current_closes)
            _, _, prev_hist = self._calculate_macd(prev_closes)
            
            # 死叉: 柱状图从正变负
            if prev_hist > 0 and curr_hist < 0:
                return True
        
        return False
    
    def calculate_atr(self, klines: List[Kline], period: int = 14) -> float:
        """
        计算 ATR (公开方法，用于外部调用)
        
        Args:
            klines: K线列表
            period: ATR 周期
            
        Returns:
            ATR 值
        """
        return self._calculate_atr(klines, period)
    
    def is_trending(self, klines: List[Kline]) -> bool:
        """
        判断当前是否为趋势行情
        
        基于 ADX 判断:
        - ADX > 25: 趋势行情
        - ADX <= 25: 震荡行情
        """
        if not self.config.adx_enabled or len(klines) < self.config.adx_period + 1:
            return False
        
        highs = [k.high for k in klines]
        lows = [k.low for k in klines]
        closes = [k.close for k in klines]
        
        adx = self._calculate_adx(highs, lows, closes, self.config.adx_period)
        return adx > 25
    
    def is_overbought(self, klines: List[Kline], threshold: float = 70) -> bool:
        """
        判断是否超买
        
        基于 RSI > threshold
        """
        if not self.config.rsi_enabled or len(klines) < self.config.rsi_period + 1:
            return False
        
        closes = [k.close for k in klines]
        rsi = self._calculate_rsi(closes, self.config.rsi_period)
        return rsi > threshold
    
    def is_oversold(self, klines: List[Kline], threshold: float = 30) -> bool:
        """
        判断是否超卖
        
        基于 RSI < threshold
        """
        if not self.config.rsi_enabled or len(klines) < self.config.rsi_period + 1:
            return False
        
        closes = [k.close for k in klines]
        rsi = self._calculate_rsi(closes, self.config.rsi_period)
        return rsi < threshold
