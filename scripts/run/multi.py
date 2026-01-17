#!/usr/bin/env python3
"""
多实例启动器：按 instances.yaml 启动多个策略进程

每个实例 = 1 交易所 + 1 币种 + 1 进程 + 1 Telegram Bot
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path
import yaml


def load_instances(config_path: Path):
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    launcher = data.get("launcher", {})
    instances = data.get("instances", []) or []
    return launcher, instances


def main():
    parser = argparse.ArgumentParser(description="Multi-instance launcher")
    parser.add_argument(
        "--config", "-c",
        default="configs/instances.yaml",
        help="多实例配置文件路径"
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"❌ 配置文件不存在: {config_path}")
        sys.exit(1)

    launcher_cfg, instances = load_instances(config_path)
    if not instances:
        print("❌ instances 列表为空")
        sys.exit(1)

    log_dir = Path(launcher_cfg.get("log_dir", "logs/instances"))
    log_dir.mkdir(parents=True, exist_ok=True)

    procs = []
    try:
        for inst in instances:
            name = inst.get("name")
            cfg = inst.get("config_path")
            if not name or not cfg:
                print(f"⚠️ 跳过实例（缺少 name/config_path）: {inst}")
                continue

            log_file = log_dir / f"{name}.log"
            env = os.environ.copy()
            env["LOG_FILE_PATH"] = str(log_file)

            cmd = [
                sys.executable,
                "scripts/run.py",
                "--config",
                cfg,
            ]
            print(f"▶️ 启动实例: {name}, config={cfg}, log={log_file}")
            proc = subprocess.Popen(cmd, env=env)
            procs.append((name, proc))

        # 等待子进程
        for name, proc in procs:
            ret = proc.wait()
            print(f"🔚 实例退出: {name}, code={ret}")
    except KeyboardInterrupt:
        print("⏹ 收到中断信号，停止所有实例...")
        for _, proc in procs:
            proc.terminate()
    finally:
        for _, proc in procs:
            if proc.poll() is None:
                proc.kill()


if __name__ == "__main__":
    main()

