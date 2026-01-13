#!/usr/bin/env python3
"""
Key Level Grid Strategy Runner

基于支撑/阻力位的网格交易策略启动脚本
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

# 添加 src 目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv
from rich.console import Console, Group
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from key_level_grid.strategy import KeyLevelGridStrategy, KeyLevelGridConfig


console = Console()


def format_price(price: float) -> str:
    """格式化价格显示"""
    if price >= 10000:
        return f"{price:,.2f}"
    elif price >= 100:
        return f"{price:.2f}"
    else:
        return f"{price:.4f}"


def format_pct(pct: float) -> str:
    """格式化百分比"""
    if pct > 0:
        return f"[green]+{pct:.2%}[/green]"
    elif pct < 0:
        return f"[red]{pct:.2%}[/red]"
    return f"{pct:.2%}"


def create_account_panel(data: dict) -> Panel:
    """创建账户信息面板"""
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("项目", style="dim")
    table.add_column("数值", justify="right")
    
    account = data.get("account", {})
    grid_cfg = account.get("grid_config", {})
    
    # 获取止损价格 (兼容多种字段名)
    floor_price = grid_cfg.get("grid_floor", 0) or grid_cfg.get("stop_loss_price", 0) or grid_cfg.get("floor_price", 0)
    
    # 计算距止损距离
    current_price = data.get("current_price", 0)
    if current_price > 0 and floor_price > 0:
        distance_to_floor = (floor_price - current_price) / current_price
    else:
        distance_to_floor = grid_cfg.get("distance_to_floor", 0)
    
    # 获取最大亏损百分比
    max_loss_pct = grid_cfg.get("max_loss_pct", 0)
    # 如果是百分比形式 (如 15.5 表示 15.5%)，需要转换
    if max_loss_pct > 1:
        max_loss_pct = max_loss_pct / 100
    
    table.add_row("总余额", f"{account.get('total_balance', 0):.2f} USDT")
    table.add_row("可用", f"{account.get('available', 0):.2f} USDT")
    table.add_row("冻结", f"{account.get('frozen', 0):.2f} USDT")
    table.add_row("──────────", "──────────────────")
    table.add_row("网格配置", "")
    table.add_row("最大仓位", f"{grid_cfg.get('max_position', 0):.0f} USDT")
    table.add_row("杠杆", f"{grid_cfg.get('max_leverage', 0)}x")
    table.add_row("止损线", f"{format_price(floor_price)}")
    table.add_row("距止损", format_pct(distance_to_floor))
    table.add_row("预计最大亏损", f"{grid_cfg.get('max_loss', 0):.0f} USDT ({max_loss_pct:.1%})")
    
    return Panel(table, title="💰 账户信息", border_style="blue")


def create_position_panel(data: dict) -> Panel:
    """创建持仓信息面板"""
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("项目", style="dim")
    table.add_column("数值", justify="right")
    
    pos = data.get("position", {})
    
    side = pos.get("side", "无")
    side_text = "[green]多头[/green]" if side == "long" else "[red]空头[/red]" if side == "short" else "无"
    
    # 获取持仓数据 (兼容两种字段名)
    qty = pos.get("qty", pos.get("contracts", 0))
    value = pos.get("value", pos.get("notional", 0))
    avg_price = pos.get("avg_entry_price", pos.get("avg_price", 0))
    unrealized_pnl = pos.get("unrealized_pnl", 0)
    grid_floor = pos.get("grid_floor", 0)
    
    # 计算盈亏百分比
    if value > 0 and unrealized_pnl != 0:
        pnl_pct = unrealized_pnl / value
    else:
        pnl_pct = pos.get("unrealized_pnl_pct", 0)
    
    table.add_row("方向", side_text)
    table.add_row("数量", f"{qty:.6f} BTC")
    table.add_row("价值", f"{value:.2f} USDT")
    table.add_row("均价", f"{format_price(avg_price)}")
    
    # 未实现盈亏：USDT + 百分比
    if unrealized_pnl > 0:
        pnl_text = f"[green]+{unrealized_pnl:.2f} USDT ({pnl_pct:+.2%})[/green]"
    elif unrealized_pnl < 0:
        pnl_text = f"[red]{unrealized_pnl:.2f} USDT ({pnl_pct:+.2%})[/red]"
    else:
        pnl_text = "0.00 USDT (0.00%)"
    table.add_row("未实现盈亏", pnl_text)
    if grid_floor > 0:
        table.add_row("网格底线", f"{format_price(grid_floor)}")
    
    return Panel(table, title="📊 当前持仓", border_style="green")


def create_orders_panel(data: dict) -> Panel:
    """创建挂单面板"""
    orders = data.get("pending_orders", [])
    
    # 处理两种格式：列表格式和字典格式
    if isinstance(orders, dict):
        buy_orders = orders.get("buy_orders", [])
        sell_orders = orders.get("sell_orders", [])
    else:
        # 列表格式：根据 side 字段分类
        buy_orders = [o for o in orders if o.get("side") == "buy"]
        sell_orders = [o for o in orders if o.get("side") == "sell"]
    
    table = Table(box=None, padding=(0, 1))
    table.add_column("档位", style="dim", justify="center")
    table.add_column("价格", justify="right")
    table.add_column("BTC", justify="right")
    table.add_column("USDT", justify="right")
    table.add_column("距当前", justify="center")
    
    current_price = data.get("current_price", 0)
    
    # 卖单（降序）
    table.add_row("卖单", "", "", "", "", style="bold red")
    sell_orders_sorted = sorted(sell_orders, key=lambda x: -x.get("price", 0))
    for i, order in enumerate(sell_orders_sorted[:10], 1):
        price = order.get("price", 0)
        btc = order.get("contracts", 0)
        usdt = order.get("amount", 0)
        distance = (price - current_price) / current_price if current_price > 0 else 0
        table.add_row(
            f"#{i}",
            f"[red]{format_price(price)}[/red]",
            f"{btc:.6f}",
            f"{usdt:.0f}",
            f"+{distance:.1%}"
        )
    
    table.add_row("───", "──────────", "──────────", "────────", "──────")
    
    # 买单（降序）
    table.add_row("买单", "", "", "", "", style="bold green")
    buy_orders_sorted = sorted(buy_orders, key=lambda x: -x.get("price", 0))
    for i, order in enumerate(buy_orders_sorted[:10], 1):
        price = order.get("price", 0)
        btc = order.get("contracts", 0)
        usdt = order.get("amount", 0)
        distance = (price - current_price) / current_price if current_price > 0 else 0
        table.add_row(
            f"#{i}",
            f"[green]{format_price(price)}[/green]",
            f"{btc:.6f}",
            f"{usdt:.0f}",
            f"{distance:.1%}"
        )
    
    return Panel(table, title="📋 当前挂单", border_style="yellow")


def translate_source(source: str) -> str:
    """将来源标识转换为中文"""
    if not source:
        return ""
    
    # 处理复合来源 (如 "swing_5+fib_0.236")
    if "+" in source:
        parts = source.split("+")
        return "+".join(translate_source(p) for p in parts)
    
    # 单一来源映射
    source_map = {
        "swing_5": "摆动点",
        "swing_13": "摆动点",
        "swing_21": "摆动点",
        "volume_node": "密集区",
        "round_number": "心理关口",
    }
    
    # 直接匹配
    if source in source_map:
        return source_map[source]
    
    # 斐波那契
    if source.startswith("fib_"):
        ratio = source.replace("fib_", "")
        return f"斐波{ratio}"
    
    # 摆动点 (通用)
    if source.startswith("swing_"):
        return "摆动点"
    
    return source


def translate_timeframe(tf: str) -> str:
    """将周期转换为中文"""
    tf_map = {
        "1m": "1分钟",
        "5m": "5分钟",
        "15m": "15分钟",
        "30m": "30分钟",
        "1h": "1小时",
        "2h": "2小时",
        "4h": "4小时",
        "6h": "6小时",
        "8h": "8小时",
        "12h": "12小时",
        "1d": "日线",
        "1D": "日线",
        "D1": "日线",
        "1w": "周线",
        "1W": "周线",
        "W1": "周线",
        "1M": "月线",
        "multi": "多周期",
    }
    return tf_map.get(tf, tf)


def create_levels_panel(data: dict) -> Panel:
    """创建关键价位面板"""
    table = Table(box=None, padding=(0, 1))
    table.add_column("类型", style="dim")
    table.add_column("价格", justify="right")
    table.add_column("涨跌幅", justify="right")
    table.add_column("周期", justify="center")
    table.add_column("评分", justify="right")
    
    current_price = data.get("current_price", 0)
    
    # 阻力位（按价格降序，高价在上）
    table.add_row("阻力位", "", "", "", "", style="bold red")
    resistances = sorted(data.get("resistance_levels", []), key=lambda x: -x.get("price", 0))[:10]
    for r in resistances:
        price = r.get("price", 0)
        pct = (price - current_price) / current_price if current_price > 0 else 0
        source_cn = translate_source(r.get("source", ""))
        tf_cn = translate_timeframe(r.get("timeframe", ""))
        table.add_row(
            f"  {source_cn}",
            f"[red]{format_price(price)}[/red]",
            format_pct(pct),
            tf_cn,
            f"{r.get('strength', 0):.0f}"
        )
    
    table.add_row("──────────", "──────────", "───────", "────────", "─────")
    table.add_row("当前价格", f"[bold]{format_price(current_price)}[/bold]", "基准", "", "")
    table.add_row("──────────", "──────────", "───────", "────────", "─────")
    
    # 支撑位（按价格降序，高价在上，靠近当前价的在前）
    table.add_row("支撑位", "", "", "", "", style="bold green")
    supports = sorted(data.get("support_levels", []), key=lambda x: -x.get("price", 0))[:10]
    for s in supports:
        price = s.get("price", 0)
        pct = (price - current_price) / current_price if current_price > 0 else 0
        source_cn = translate_source(s.get("source", ""))
        tf_cn = translate_timeframe(s.get("timeframe", ""))
        table.add_row(
            f"  {source_cn}",
            f"[green]{format_price(price)}[/green]",
            format_pct(pct),
            tf_cn,
            f"{s.get('strength', 0):.0f}"
        )
    
    return Panel(table, title="📍 关键价位", border_style="cyan")


def get_current_price(data: dict) -> float:
    """从 data 中获取当前价格（兼容多种格式）"""
    # 优先尝试直接的 current_price
    price = data.get("current_price")
    if price and price > 0:
        return float(price)
    # 尝试 price.current 格式
    price_obj = data.get("price", {})
    if isinstance(price_obj, dict):
        price = price_obj.get("current", 0)
        if price and price > 0:
            return float(price)
    return 0.0


def create_display(strategy: KeyLevelGridStrategy) -> Layout:
    """创建显示布局"""
    data = strategy.get_display_data()
    
    # 统一获取当前价格并注入到 data 中
    current_price = get_current_price(data)
    data["current_price"] = current_price
    
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
    )
    
    # 头部
    symbol = strategy.config.symbol
    timeframe = strategy.config.kline_config.primary_timeframe.value
    aux_tfs = [tf.value for tf in strategy.config.kline_config.auxiliary_timeframes]
    
    header_text = (
        f" Key Level Grid Strategy | {symbol} | ${format_price(current_price)} | "
        f"周期: {timeframe} + {', '.join(aux_tfs)} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    layout["header"].update(Panel(Text(header_text, style="bold magenta"), style="magenta"))
    
    # 主体
    layout["body"].split_row(
        Layout(name="orders", ratio=1),
        Layout(name="middle", ratio=1),
        Layout(name="levels", ratio=1),
    )
    
    layout["orders"].update(create_orders_panel(data))
    
    layout["middle"].split_column(
        Layout(name="account"),
        Layout(name="position"),
    )
    layout["middle"]["account"].update(create_account_panel(data))
    layout["middle"]["position"].update(create_position_panel(data))
    
    layout["levels"].update(create_levels_panel(data))
    
    return layout


async def run_strategy(config_path: str, force_rebuild: bool = False):
    """运行策略"""
    load_dotenv()
    
    console.print(Panel.fit(
        "[bold magenta]🎯 Key Level Grid Strategy[/bold magenta]\n"
        f"配置文件: {config_path}",
        title="启动中"
    ))
    
    # 加载策略
    strategy = KeyLevelGridStrategy.from_yaml(config_path)
    
    # 显示配置信息（包括周期）
    kline_cfg = strategy.config.kline_config
    primary_tf = kline_cfg.primary_timeframe.value
    aux_tfs = [tf.value for tf in kline_cfg.auxiliary_timeframes]
    
    console.print(Panel.fit(
        f"Symbol: {strategy.config.symbol}\n"
        f"Exchange: {strategy.config.exchange}\n"
        f"Mode: {'Dry Run' if strategy.config.dry_run else 'Live'}\n"
        f"主周期: {primary_tf}\n"
        f"辅助周期: {', '.join(aux_tfs)}",
        title="✅ 策略已加载"
    ))
    
    # 启动策略（后台任务）
    strategy_task = asyncio.create_task(strategy.start())
    
    # 等待初始数据
    await asyncio.sleep(3)

    # 可选：启动后立即强制重建一次网格
    if force_rebuild:
        console.print("[yellow]⏳ 强制重建网格中...[/yellow]")
        try:
            ok = await strategy.force_rebuild_grid()
            if ok:
                console.print("[green]✅ 已强制重建网格[/green]")
            else:
                console.print("[red]⚠️ 强制重建网格失败（可能是数据不足或 DryRun）[/red]")
        except Exception as e:
            console.print(f"[red]❌ 强制重建网格异常: {e}[/red]")
    
    # 实时显示
    try:
        with Live(create_display(strategy), console=console, refresh_per_second=1) as live:
            while True:
                await asyncio.sleep(1)
                live.update(create_display(strategy))
    except KeyboardInterrupt:
        console.print("\n[yellow]⏹️ 正在停止策略...[/yellow]")
        await strategy.stop()
        strategy_task.cancel()
        console.print("[green]✅ 策略已停止[/green]")


def main():
    # 初始化日志文件
    from key_level_grid.utils.logger import setup_file_logging
    parser = argparse.ArgumentParser(description="Key Level Grid Strategy Runner")
    parser.add_argument(
        "--config", "-c",
        default="configs/config.yaml",
        help="配置文件路径"
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="日志文件路径（可选，未提供则使用默认 logs/key_level_grid.log 或环境变量 LOG_FILE_PATH）"
    )
    parser.add_argument(
        "--force-rebuild",
        action="store_true",
        help="启动后立即强制重建当前网格"
    )
    args = parser.parse_args()

    log_file = setup_file_logging(log_file=args.log_file)
    console.print(f"[dim]📝 日志文件: {log_file}[/dim]")
    
    # 检查配置文件
    config_path = Path(args.config)
    if not config_path.exists():
        # 尝试相对于项目根目录
        project_root = Path(__file__).parent.parent
        config_path = project_root / args.config
        if not config_path.exists():
            console.print(f"[red]❌ 配置文件不存在: {args.config}[/red]")
            sys.exit(1)
    
    asyncio.run(run_strategy(str(config_path), force_rebuild=args.force_rebuild))


if __name__ == "__main__":
    main()
