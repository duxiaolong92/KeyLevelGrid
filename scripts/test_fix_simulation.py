#!/usr/bin/env python3
"""
模拟测试修复后的逻辑

验证:
1. build_level_mapping 是否正确构建映射（使用 ID 集合判断，不依赖 role 字段）
2. build_recon_actions 是否正确分类买卖水位（不修改原对象）
3. sync_mapping 是否正确计算卖单目标
"""

import json
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Optional

# 模拟数据结构
@dataclass
class MockLevel:
    level_id: int
    price: float
    side: str
    role: str
    fill_counter: int = 0
    status: str = "IDLE"


def load_state():
    """加载当前状态文件"""
    state_file = Path(__file__).parent.parent / "state/key_level_grid/gate/btcusdt_state.json"
    with open(state_file, "r") as f:
        return json.load(f)


def build_level_mapping_old(support_levels: List[MockLevel], resistance_levels: List[MockLevel], min_profit_pct: float = 0.0001) -> Dict[int, int]:
    """
    旧版 build_level_mapping（依赖 role 字段）
    """
    all_levels = support_levels + resistance_levels
    sorted_levels = sorted(all_levels, key=lambda x: x.price)
    
    mapping = {}
    missing = []
    
    for i, level in enumerate(sorted_levels):
        if level.role != "support":  # ❌ 依赖 role 字段
            continue
        
        min_sell_price = level.price * (1 + min_profit_pct)
        target_level = None
        for j in range(i + 1, len(sorted_levels)):
            candidate = sorted_levels[j]
            if candidate.price > min_sell_price:
                target_level = candidate
                break
        
        if target_level:
            mapping[level.level_id] = target_level.level_id
        else:
            missing.append(level.price)
    
    return mapping, missing


def build_level_mapping_new(support_levels: List[MockLevel], resistance_levels: List[MockLevel], min_profit_pct: float = 0.0001) -> Dict[int, int]:
    """
    新版 build_level_mapping（使用 ID 集合判断，不依赖 role 字段）
    """
    # 获取支撑位 ID 集合
    support_level_ids = {lvl.level_id for lvl in support_levels}
    
    all_levels = support_levels + resistance_levels
    sorted_levels = sorted(all_levels, key=lambda x: x.price)
    
    mapping = {}
    missing = []
    
    for i, level in enumerate(sorted_levels):
        # ✅ 使用 ID 集合判断
        if level.level_id not in support_level_ids:
            continue
        
        min_sell_price = level.price * (1 + min_profit_pct)
        target_level = None
        for j in range(i + 1, len(sorted_levels)):
            candidate = sorted_levels[j]
            if candidate.price > min_sell_price:
                target_level = candidate
                break
        
        if target_level:
            mapping[level.level_id] = target_level.level_id
        else:
            missing.append(level.price)
    
    return mapping, missing


def simulate_recon_old(support_levels: List[MockLevel], resistance_levels: List[MockLevel], current_price: float):
    """
    旧版 build_recon_actions 的分类逻辑（会修改原对象）
    """
    all_levels = support_levels + resistance_levels
    
    # 模拟旧版：直接修改原对象
    for lvl in all_levels:
        if lvl.price < current_price:
            lvl.role = "support"
            lvl.side = "buy"
        elif lvl.price > current_price:
            lvl.role = "resistance"
            lvl.side = "sell"
        else:
            lvl.role = "neutral"
    
    buy_levels = [lvl for lvl in all_levels if lvl.role == "support"]
    sell_levels = [lvl for lvl in all_levels if lvl.role == "resistance"]
    
    return buy_levels, sell_levels


def simulate_recon_new(support_levels: List[MockLevel], resistance_levels: List[MockLevel], current_price: float):
    """
    新版 build_recon_actions 的分类逻辑（不修改原对象）
    """
    # ✅ 不修改原对象，只基于价格位置分类
    buy_levels = [
        lvl for lvl in support_levels 
        if lvl.price < current_price
    ]
    sell_levels = [
        lvl for lvl in resistance_levels 
        if lvl.price > current_price
    ]
    
    return buy_levels, sell_levels


def simulate_sync_mapping(
    support_levels: List[MockLevel],
    level_mapping: Dict[int, int],
    all_levels_by_id: Dict[int, MockLevel],
    base_qty: float = 0.001,
    sell_quota_ratio: float = 0.7
) -> Dict[int, float]:
    """
    模拟 sync_mapping 的卖单计算
    """
    expected_sell_by_level = {}
    
    for support_lvl in support_levels:
        fill_count = support_lvl.fill_counter
        if fill_count <= 0:
            continue
        
        target_level_id = level_mapping.get(support_lvl.level_id)
        if not target_level_id:
            print(f"  ⚠️ 支撑位 L_{support_lvl.level_id}({support_lvl.price:.2f}) 无邻位映射")
            continue
        
        target_level = all_levels_by_id.get(target_level_id)
        if not target_level:
            print(f"  ⚠️ 映射目标 L_{target_level_id} 不存在")
            continue
        
        contrib_qty = fill_count * base_qty * sell_quota_ratio
        expected_sell_by_level[target_level_id] = (
            expected_sell_by_level.get(target_level_id, 0) + contrib_qty
        )
        print(f"  📍 L_{support_lvl.level_id}({support_lvl.price:.2f}) fill={fill_count} → "
              f"L_{target_level_id}({target_level.price:.2f}) qty={contrib_qty:.6f}")
    
    return expected_sell_by_level


def main():
    print("=" * 60)
    print("🔬 修复验证模拟测试")
    print("=" * 60)
    
    # 加载状态
    state_data = load_state()
    grid_state = state_data["grid_state"]
    
    # 构建模拟数据
    support_levels = [
        MockLevel(
            level_id=lvl["level_id"],
            price=lvl["price"],
            side=lvl["side"],
            role=lvl["role"],
            fill_counter=lvl["fill_counter"],
            status=lvl["status"]
        )
        for lvl in grid_state["support_levels_state"]
    ]
    
    resistance_levels = [
        MockLevel(
            level_id=lvl["level_id"],
            price=lvl["price"],
            side=lvl["side"],
            role=lvl["role"],
            fill_counter=lvl["fill_counter"],
            status=lvl["status"]
        )
        for lvl in grid_state["resistance_levels_state"]
    ]
    
    current_price = 89500.0  # 模拟当前价格
    
    print(f"\n📊 当前状态:")
    print(f"   当前价格: {current_price}")
    print(f"   支撑位数量: {len(support_levels)}")
    print(f"   阻力位数量: {len(resistance_levels)}")
    
    # 打印支撑位状态
    print(f"\n📋 支撑位状态 (修复后的 state.json):")
    for lvl in support_levels:
        marker = "✅" if lvl.side == "buy" and lvl.role == "support" else "❌"
        print(f"   {marker} L_{lvl.level_id}: {lvl.price:.2f} | side={lvl.side}, role={lvl.role}, fill={lvl.fill_counter}")
    
    # ============================================
    # 测试 1: build_level_mapping
    # ============================================
    print("\n" + "=" * 60)
    print("🧪 测试 1: build_level_mapping")
    print("=" * 60)
    
    # 创建副本用于旧版测试（因为旧版会修改对象）
    support_copy_old = [MockLevel(**vars(lvl)) for lvl in support_levels]
    resistance_copy_old = [MockLevel(**vars(lvl)) for lvl in resistance_levels]
    
    # 先模拟旧版 recon 对 role 的修改
    print("\n--- 旧版行为（模拟 recon 修改 role 后）---")
    simulate_recon_old(support_copy_old, resistance_copy_old, current_price)
    
    print("  支撑位 role 被修改后:")
    for lvl in support_copy_old:
        marker = "❌" if lvl.role == "resistance" else "✅"
        print(f"   {marker} L_{lvl.level_id}: {lvl.price:.2f} | role={lvl.role}")
    
    mapping_old, missing_old = build_level_mapping_old(support_copy_old, resistance_copy_old)
    print(f"\n  旧版映射表 ({len(mapping_old)} 个):")
    for src, dst in sorted(mapping_old.items()):
        src_price = next((l.price for l in support_copy_old if l.level_id == src), 0)
        dst_price = next((l.price for l in support_copy_old + resistance_copy_old if l.level_id == dst), 0)
        print(f"    L_{src}({src_price:.2f}) → L_{dst}({dst_price:.2f})")
    print(f"  无邻位: {len(missing_old)} 个")
    
    # 新版
    print("\n--- 新版行为（使用 ID 集合判断）---")
    mapping_new, missing_new = build_level_mapping_new(support_levels, resistance_levels)
    print(f"  新版映射表 ({len(mapping_new)} 个):")
    
    all_levels_by_id = {lvl.level_id: lvl for lvl in support_levels + resistance_levels}
    for src, dst in sorted(mapping_new.items()):
        src_price = support_levels[src - 1].price if src <= len(support_levels) else 0
        dst_lvl = all_levels_by_id.get(dst)
        dst_price = dst_lvl.price if dst_lvl else 0
        print(f"    L_{src}({src_price:.2f}) → L_{dst}({dst_price:.2f})")
    print(f"  无邻位: {len(missing_new)} 个")
    
    # ============================================
    # 测试 2: build_recon_actions 分类
    # ============================================
    print("\n" + "=" * 60)
    print("🧪 测试 2: build_recon_actions 分类")
    print("=" * 60)
    
    # 新版分类（不修改原对象）
    buy_levels_new, sell_levels_new = simulate_recon_new(support_levels, resistance_levels, current_price)
    
    print(f"\n新版分类结果 (current_price={current_price}):")
    print(f"  买入候选 ({len(buy_levels_new)} 个):")
    for lvl in buy_levels_new:
        print(f"    L_{lvl.level_id}: {lvl.price:.2f}")
    
    print(f"\n  卖出候选 ({len(sell_levels_new)} 个):")
    for lvl in sell_levels_new:
        print(f"    L_{lvl.level_id}: {lvl.price:.2f}")
    
    # 验证原对象未被修改
    print("\n验证原对象未被修改:")
    unchanged = all(
        lvl.side == "buy" and lvl.role == "support" 
        for lvl in support_levels
    )
    print(f"  支撑位 side/role 保持不变: {'✅ 是' if unchanged else '❌ 否'}")
    
    # ============================================
    # 测试 3: sync_mapping 卖单计算
    # ============================================
    print("\n" + "=" * 60)
    print("🧪 测试 3: sync_mapping 卖单计算")
    print("=" * 60)
    
    # 模拟有成交的场景
    print("\n模拟场景: 假设 L_4, L_5, L_6 各有 1 次成交")
    test_support = [MockLevel(**vars(lvl)) for lvl in support_levels]
    for lvl in test_support:
        if lvl.level_id in [4, 5, 6]:
            lvl.fill_counter = 1
    
    print("\n使用新版映射表计算卖单分布:")
    expected_sell = simulate_sync_mapping(
        test_support,
        mapping_new,
        all_levels_by_id,
        base_qty=0.001,
        sell_quota_ratio=0.7
    )
    
    print(f"\n卖单分布汇总:")
    for level_id, qty in sorted(expected_sell.items()):
        lvl = all_levels_by_id.get(level_id)
        if lvl:
            print(f"  L_{level_id}({lvl.price:.2f}): {qty:.6f} BTC")
    
    # ============================================
    # 对比分析
    # ============================================
    print("\n" + "=" * 60)
    print("📊 对比分析总结")
    print("=" * 60)
    
    print(f"\n映射表对比:")
    print(f"  旧版映射数量: {len(mapping_old)}")
    print(f"  新版映射数量: {len(mapping_new)}")
    print(f"  差异: {len(mapping_new) - len(mapping_old)} 个映射")
    
    if len(mapping_new) > len(mapping_old):
        missing_in_old = set(mapping_new.keys()) - set(mapping_old.keys())
        print(f"\n  旧版缺失的映射 (因 role 被污染):")
        for level_id in missing_in_old:
            src_lvl = next((l for l in support_levels if l.level_id == level_id), None)
            dst_id = mapping_new[level_id]
            dst_lvl = all_levels_by_id.get(dst_id)
            if src_lvl and dst_lvl:
                print(f"    L_{level_id}({src_lvl.price:.2f}) → L_{dst_id}({dst_lvl.price:.2f})")
    
    print("\n✅ 修复验证完成!")
    print("=" * 60)


if __name__ == "__main__":
    main()
