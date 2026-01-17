# 📚 Key Level Grid 规格文档导航

> **最后更新**: 2026-01-17  
> **维护者**: KeyLevelGrid Core Team

---

## 🏛️ 文档层级

```
spec/
├── README.md                      # 📌 本文件：目录导航
├── CONSTITUTION.md                # 🔒 项目宪法（最高准则）
│
├── core/                          # 核心架构
│   └── OVERVIEW.md                # 系统架构与核心逻辑
│
├── features/                      # 功能规格
│   ├── LEVEL_LIFECYCLE.md         # 水位生命周期
│   ├── SELL_MAPPING.md            # 卖单映射与配额对账
│   ├── LEVEL_GENERATION.md        # V3.0 水位生成引擎
│   └── TELEGRAM.md                # Telegram 交互规格
│
├── plans/                         # 开发/重构计划
│   ├── LEVEL_LIFECYCLE_REFACTOR.md
│   └── PROGRESSIVE_MAPPING.md
│
├── tech/                          # 技术实现
│   └── TECH_STACK.md              # 技术栈与架构决策
│
└── tasks/                         # 任务清单
    └── progressive-mapping.md
```

---

## 📖 文档优先级

| 优先级 | 文档 | 说明 |
|--------|------|------|
| 🔴 **最高** | `CONSTITUTION.md` | 项目宪法，所有代码必须遵守 |
| 🟠 **高** | `features/*.md` | 功能规格，开发时的主要参考 |
| 🟡 **中** | `core/OVERVIEW.md` | 系统架构概览 |
| 🟢 **参考** | `plans/*.md` | 重构计划，了解演进方向 |
| 🔵 **参考** | `tech/TECH_STACK.md` | 技术选型记录 |

---

## 🔗 快速链接

### 核心文档
- [📜 项目宪法](./CONSTITUTION.md) - 逐级邻位止盈、动态仓位保留等核心原则
- [🏗️ 系统架构概览](./core/OVERVIEW.md) - 双轨异步驱动、状态机设计

### 功能规格
- [🔄 水位生命周期](./features/LEVEL_LIFECYCLE.md) - ACTIVE/RETIRED/DEAD 三态管理
- [💹 卖单映射规则](./features/SELL_MAPPING.md) - 逐级邻位映射算法
- [📊 水位生成引擎](./features/LEVEL_GENERATION.md) - V3.0 多时间框架评分
- [📱 Telegram 交互](./features/TELEGRAM.md) - Bot 命令与通知规格

### 开发计划
- [📋 水位生命周期重构](./plans/LEVEL_LIFECYCLE_REFACTOR.md)
- [📋 逐级邻位映射重构](./plans/PROGRESSIVE_MAPPING.md)

### 任务追踪
- [✅ 逐级映射任务清单](./tasks/progressive-mapping.md)

---

## 📝 文档更新规范

1. **版本号**: 每个文档头部维护版本号和更新日期
2. **关联引用**: 使用相对路径引用其他文档
3. **状态标记**: Draft / Review / Published
4. **变更记录**: 重大变更需在文档底部记录

---

## 🔍 旧文件映射表（重构参考）

| 原文件 | 新位置 | 状态 |
|--------|--------|------|
| `spec.md` | `core/OVERVIEW.md` | 已合并到 spec2.0.md |
| `spec2.0.md` | `core/OVERVIEW.md` | ✅ 迁移 |
| `plan.md` | `tech/TECH_STACK.md` | ✅ 迁移 |
| `SPEC_LEVEL_LIFECYCLE.md` | `features/LEVEL_LIFECYCLE.md` | ✅ 迁移 |
| `SPEC_SELL_MAPPING.md` | `features/SELL_MAPPING.md` | ✅ 迁移 |
| `SPEC_V3_LEVEL_GENERATION.md` | `features/LEVEL_GENERATION.md` | ✅ 迁移 |
| `telegram_notification_v3.2.1.md` | `features/TELEGRAM.md` | ✅ 迁移 |
| `PLAN_LEVEL_LIFECYCLE_REFACTOR.md` | `plans/LEVEL_LIFECYCLE_REFACTOR.md` | ✅ 迁移 |
| `PLAN_PROGRESSIVE_MAPPING.md` | `plans/PROGRESSIVE_MAPPING.md` | ✅ 迁移 |

> ⚠️ 完成迁移验证后，可安全删除根目录下的旧文件。
