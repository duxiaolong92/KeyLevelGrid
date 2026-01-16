#!/usr/bin/env python3
"""
历史回放回测入口
"""

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

import aiohttp

# 添加 src 目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from key_level_grid.backtest_kline_feed import BacktestKlineFeed
from key_level_grid.executor.backtest_executor import BacktestExecutor
from key_level_grid.gate_kline_feed import GateKlineFeed
from key_level_grid.models import Kline, KlineFeedConfig, Timeframe
from key_level_grid.strategy import KeyLevelGridStrategy
from key_level_grid.utils.logger import get_logger, setup_file_logging


BASE_URL = "https://api.gateio.ws/api/v4"


def _convert_symbol(symbol: str) -> str:
    symbol = symbol.upper()
    if "_" in symbol:
        return symbol
    for suffix in ["USDT", "USD", "BUSD", "USDC"]:
        if symbol.endswith(suffix):
            base = symbol[:-len(suffix)]
            return f"{base}_{suffix}"
    return f"{symbol}_USDT"


def _parse_datetime(value: str) -> int:
    if not value:
        return 0
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(value, fmt)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    raise ValueError(f"无效时间格式: {value}")


async def _fetch_gate_klines_range(
    session: aiohttp.ClientSession,
    symbol: str,
    timeframe: Timeframe,
    start_ms: int,
    end_ms: int,
    limit: int = 2000,
) -> list[Kline]:
    gate_symbol = _convert_symbol(symbol)
    interval = GateKlineFeed.TIMEFRAME_MAP.get(timeframe, "4h")
    url = f"{BASE_URL}/futures/usdt/candlesticks"

    results: list[Kline] = []
    to_ts = int(end_ms / 1000) if end_ms else 0
    loops = 0

    while True:
        params = {
            "contract": gate_symbol,
            "interval": interval,
            "limit": min(limit, 2000),
        }
        if to_ts:
            params["to"] = to_ts

        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Gate API 错误: {resp.status} - {await resp.text()}")
            data = await resp.json()

        if not data:
            break

        batch: list[Kline] = []
        for item in data:
            ts_ms = int(item["t"]) * 1000
            if start_ms and ts_ms < start_ms:
                continue
            if end_ms and ts_ms > end_ms:
                continue
            batch.append(
                Kline(
                    timestamp=ts_ms,
                    open=float(item["o"]),
                    high=float(item["h"]),
                    low=float(item["l"]),
                    close=float(item["c"]),
                    volume=float(item["v"]),
                    quote_volume=float(item.get("sum", 0)),
                    trades=0,
                    is_closed=True,
                )
            )

        results = batch + results

        first_ts = int(data[0]["t"]) * 1000
        if start_ms and first_ts <= start_ms:
            break
        to_ts = int(first_ts / 1000) - 1
        loops += 1
        if loops > 2000:
            break

    results.sort(key=lambda x: x.timestamp)
    return results


def _calc_max_drawdown(equity_curve: list[float]) -> float:
    peak = 0.0
    max_dd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        if peak > 0:
            dd = (peak - v) / peak
            max_dd = max(max_dd, dd)
    return max_dd


async def run_backtest(args) -> None:
    logger = get_logger(__name__)
    strategy = KeyLevelGridStrategy.from_yaml(args.config)

    # 禁用 Telegram
    strategy.config.tg_enabled = False
    strategy._tg_bot = None
    strategy._notifier = None
    strategy._sl_synced_from_exchange = True

    # 替换为回测数据源
    kline_config: KlineFeedConfig = strategy.config.kline_config
    backtest_feed = BacktestKlineFeed(kline_config)
    strategy.kline_feed = backtest_feed

    # 回测执行器
    gate_symbol = _convert_symbol(strategy.config.symbol)
    contract_size = args.contract_size or getattr(strategy.config, "default_contract_size", 1.0)
    leverage = strategy.position_manager.position_config.max_leverage
    executor = BacktestExecutor(
        symbol=gate_symbol,
        initial_balance=args.initial_balance,
        contract_size=contract_size,
        leverage=leverage,
        min_contracts=args.min_contracts,
    )
    strategy._executor = executor
    strategy._contract_size = contract_size
    strategy.config.dry_run = False

    # 拉取历史数据
    start_ms = _parse_datetime(args.start) if args.start else 0
    end_ms = _parse_datetime(args.end) if args.end else 0

    async with aiohttp.ClientSession() as session:
        primary_klines = await _fetch_gate_klines_range(
            session,
            strategy.config.symbol,
            kline_config.primary_timeframe,
            start_ms,
            end_ms,
        )
        backtest_feed.set_klines(kline_config.primary_timeframe, primary_klines)

        for tf in kline_config.auxiliary_timeframes:
            aux_klines = await _fetch_gate_klines_range(
                session,
                strategy.config.symbol,
                tf,
                start_ms,
                end_ms,
            )
            backtest_feed.set_klines(tf, aux_klines)

    if not primary_klines:
        logger.error("未获取到主周期历史数据")
        return

    equity_curve: list[float] = []

    for kline in primary_klines:
        backtest_feed.advance_to(kline.timestamp)

        # 强制每根K线同步
        strategy._balance_updated_at = 0
        strategy._orders_updated_at = 0
        strategy._position_updated_at = 0
        strategy._trades_updated_at = 0

        await strategy._update_cycle()

        # 撮合本周期订单
        executor.match_with_kline(kline)
        equity_curve.append(executor.get_equity())

    trades = await executor.get_trade_history(gate_symbol, since=0, limit=100000)
    wins = [t for t in trades if t.get("side") == "sell" and t.get("realized_pnl", 0) > 0]
    losses = [t for t in trades if t.get("side") == "sell" and t.get("realized_pnl", 0) <= 0]

    total_pnl = sum(t.get("realized_pnl", 0) for t in trades if "realized_pnl" in t)
    max_dd = _calc_max_drawdown(equity_curve)

    logger.info("回测完成")
    logger.info("交易次数: %s", len(trades))
    logger.info("胜率: %.2f%%", (len(wins) / len(wins + losses) * 100) if (wins or losses) else 0)
    logger.info("总收益: %.2f USDT", total_pnl)
    logger.info("最大回撤: %.2f%%", max_dd * 100)


def main():
    parser = argparse.ArgumentParser(description="Key Level Grid Backtest Runner")
    parser.add_argument("--config", "-c", default="configs/config.yaml", help="配置文件路径")
    parser.add_argument("--start", default="", help="起始时间 (YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS)")
    parser.add_argument("--end", default="", help="结束时间 (YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS)")
    parser.add_argument("--initial-balance", type=float, default=10000.0, help="初始资金 (USDT)")
    parser.add_argument("--contract-size", type=float, default=0.0, help="合约大小（BTC/张）")
    parser.add_argument("--min-contracts", type=float, default=1.0, help="最小下单张数")
    parser.add_argument("--log-file", default=None, help="日志文件路径")
    args = parser.parse_args()

    log_file = setup_file_logging(log_file=args.log_file)
    print(f"📝 日志文件: {log_file}")

    config_path = Path(args.config)
    if not config_path.exists():
        project_root = Path(__file__).parent.parent
        config_path = project_root / args.config
        if not config_path.exists():
            raise SystemExit(f"配置文件不存在: {args.config}")
    args.config = str(config_path)

    asyncio.run(run_backtest(args))


if __name__ == "__main__":
    main()
