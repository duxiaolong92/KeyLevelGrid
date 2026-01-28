#!/usr/bin/env python3
"""
清理历史数据脚本

清理内容：
1. settled_inventory - 清空
2. 所有支撑位/阻力位状态重置为 IDLE
3. 清空 trades.jsonl
4. 重置持仓相关字段
"""

import json
from pathlib import Path


def clean_state():
    state_file = Path(__file__).parent.parent / "state/key_level_grid/gate/btcusdt_state.json"
    trades_file = Path(__file__).parent.parent / "state/key_level_grid/gate/btcusdt_trades.jsonl"
    
    print("=" * 60)
    print("🧹 清理历史数据")
    print("=" * 60)
    
    # 读取状态文件
    with open(state_file, "r") as f:
        data = json.load(f)
    
    grid_state = data["grid_state"]
    
    # 清理前统计
    print(f"\n📊 清理前状态:")
    print(f"   active_inventory: {len(grid_state.get('active_inventory', []))} 条")
    print(f"   settled_inventory: {len(grid_state.get('settled_inventory', []))} 条")
    
    # 1. 清空 inventory
    grid_state["active_inventory"] = []
    grid_state["settled_inventory"] = []
    print(f"\n✅ 清空 active_inventory 和 settled_inventory")
    
    # 2. 重置支撑位状态
    for lvl in grid_state.get("support_levels_state", []):
        lvl["status"] = "IDLE"
        lvl["active_order_id"] = ""
        lvl["order_id"] = ""
        lvl["target_qty"] = 0.0
        lvl["open_qty"] = 0.0
        lvl["filled_qty"] = 0.0
        lvl["fill_counter"] = 0
        lvl["last_action_ts"] = 0
        lvl["last_error"] = ""
        # 确保 side 和 role 正确
        lvl["side"] = "buy"
        lvl["role"] = "support"
    print(f"✅ 重置 {len(grid_state.get('support_levels_state', []))} 个支撑位状态")
    
    # 3. 重置阻力位状态
    for lvl in grid_state.get("resistance_levels_state", []):
        lvl["status"] = "IDLE"
        lvl["active_order_id"] = ""
        lvl["order_id"] = ""
        lvl["target_qty"] = 0.0
        lvl["open_qty"] = 0.0
        lvl["filled_qty"] = 0.0
        lvl["fill_counter"] = 0
        lvl["last_action_ts"] = 0
        lvl["last_error"] = ""
        # 确保 side 和 role 正确
        lvl["side"] = "sell"
        lvl["role"] = "resistance"
    print(f"✅ 重置 {len(grid_state.get('resistance_levels_state', []))} 个阻力位状态")
    
    # 4. 重置持仓相关字段
    grid_state["total_position_usdt"] = 0.0
    grid_state["total_position_contracts"] = 0.0
    grid_state["avg_entry_price"] = 0.0
    grid_state["unrealized_pnl"] = 0.0
    print(f"✅ 重置持仓字段")
    
    # 5. 清空 trade_history
    data["trade_history"] = []
    print(f"✅ 清空 trade_history")
    
    # 保存状态文件
    with open(state_file, "w") as f:
        json.dump(data, f, indent=2)
    print(f"\n💾 已保存: {state_file}")
    
    # 6. 清空 trades.jsonl
    if trades_file.exists():
        # 备份原文件
        backup_file = trades_file.with_suffix(".jsonl.bak")
        trades_file.rename(backup_file)
        print(f"📦 已备份: {backup_file}")
        
        # 创建空文件
        trades_file.touch()
        print(f"✅ 清空 trades.jsonl")
    
    print("\n" + "=" * 60)
    print("🎉 清理完成！可以重新运行系统了")
    print("=" * 60)


if __name__ == "__main__":
    clean_state()
