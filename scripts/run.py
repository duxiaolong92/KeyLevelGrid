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
    
    table.add_row("总余额", f"{account.get('total_balance', 0):.2f} USDT")
    table.add_row("可用", f"{account.get('available', 0):.2f} USDT")
    table.add_row("冻结", f"{account.get('frozen', 0):.2f} USDT")
    table.add_row("──────────", "──────────────────")
    table.add_row("网格配置", "")
    table.add_row("最大仓位", f"{grid_cfg.get('max_position', 0):.0f} USDT")
    table.add_row("杠杆", f"{grid_cfg.get('max_leverage', 0)}x")
    table.add_row("止损线", f"{format_price(grid_cfg.get('floor_price', 0))}")
    table.add_row("距止损", format_pct(grid_cfg.get('distance_to_floor', 0)))
    table.add_row("预计最大亏损", f"{grid_cfg.get('max_loss', 0):.0f} USDT ({grid_cfg.get('max_loss_pct', 0):.1%})")
    
    return Panel(table, title="💰 账户信息", border_style="blue")


def create_position_panel(data: dict) -> Panel:
    """创建持仓信息面板"""
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("项目", style="dim")
    table.add_column("数值", justify="right")
    
    pos = data.get("position", {})
    
    side = pos.get("side", "无")
    side_text = "[green]多头[/green]" if side == "long" else "[red]空头[/red]" if side == "short" else "无"
    
    table.add_row("方向", side_text)
    table.add_row("数量", f"{pos.get('contracts', 0):.6f} BTC")
    table.add_row("价值", f"{pos.get('notional', 0):.2f} USDT")
    table.add_row("均价", f"{format_price(pos.get('avg_price', 0))}")
    table.add_row("未实现盈亏", format_pct(pos.get('unrealized_pnl_pct', 0)))
    
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
    table.add_column("状态", justify="center")
    
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


def create_levels_panel(data: dict) -> Panel:
    """创建关键价位面板"""
    table = Table(box=None, padding=(0, 1))
    table.add_column("类型", style="dim")
    table.add_column("价格", justify="right")
    table.add_column("涨跌幅", justify="right")
    table.add_column("周期", justify="center")
    table.add_column("评分", justify="right")
    
    current_price = data.get("current_price", 0)
    
    # 阻力位
    table.add_row("阻力位", "", "", "", "", style="bold red")
    resistances = data.get("resistance_levels", [])[:10]
    for r in resistances:
        price = r.get("price", 0)
        pct = (price - current_price) / current_price if current_price > 0 else 0
        table.add_row(
            f"  {r.get('source', '')}",
            f"[red]{format_price(price)}[/red]",
            format_pct(pct),
            r.get("timeframe", ""),
            f"{r.get('strength', 0):.0f}"
        )
    
    table.add_row("──────────", "──────────", "───────", "────────", "─────")
    table.add_row("当前价格", f"[bold]{format_price(current_price)}[/bold]", "基准", "", "")
    table.add_row("──────────", "──────────", "───────", "────────", "─────")
    
    # 支撑位
    table.add_row("支撑位", "", "", "", "", style="bold green")
    supports = data.get("support_levels", [])[:10]
    for s in supports:
        price = s.get("price", 0)
        pct = (price - current_price) / current_price if current_price > 0 else 0
        table.add_row(
            f"  {s.get('source', '')}",
            f"[green]{format_price(price)}[/green]",
            format_pct(pct),
            s.get("timeframe", ""),
            f"{s.get('strength', 0):.0f}"
        )
    
    return Panel(table, title="📍 关键价位", border_style="cyan")


def create_display(strategy: KeyLevelGridStrategy) -> Layout:
    """创建显示布局"""
    data = strategy.get_display_data()
    
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
    )
    
    # 头部
    current_price = data.get("current_price", 0)
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


async def run_strategy(config_path: str):
    """运行策略"""
    load_dotenv()
    
    console.print(Panel.fit(
        "[bold magenta]🎯 Key Level Grid Strategy[/bold magenta]\n"
        f"配置文件: {config_path}",
        title="启动中"
    ))
    
    # 加载策略
    strategy = KeyLevelGridStrategy.from_yaml(config_path)
    
    console.print(Panel.fit(
        f"Symbol: {strategy.config.symbol}\n"
        f"Exchange: {strategy.config.exchange}\n"
        f"Mode: {'Dry Run' if strategy.config.dry_run else 'Live'}",
        title="✅ 策略已加载"
    ))
    
    # 启动策略（后台任务）
    strategy_task = asyncio.create_task(strategy.start())
    
    # 等待初始数据
    await asyncio.sleep(3)
    
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
    parser = argparse.ArgumentParser(description="Key Level Grid Strategy Runner")
    parser.add_argument(
        "--config", "-c",
        default="configs/config.yaml",
        help="配置文件路径"
    )
    args = parser.parse_args()
    
    # 检查配置文件
    config_path = Path(args.config)
    if not config_path.exists():
        # 尝试相对于项目根目录
        project_root = Path(__file__).parent.parent
        config_path = project_root / args.config
        if not config_path.exists():
            console.print(f"[red]❌ 配置文件不存在: {args.config}[/red]")
            sys.exit(1)
    
    asyncio.run(run_strategy(str(config_path)))


if __name__ == "__main__":
    main()
