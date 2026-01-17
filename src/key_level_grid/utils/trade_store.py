import json
import os
import logging
from typing import List, Dict, Any, Optional

class TradeStore:
    """
    成交记录持久化存储 (Append-only JSON Lines)
    用于记录每一笔买入和卖出，作为系统的“原始账本”
    """
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.logger = logging.getLogger("TradeStore")
        self._cache: List[Dict[str, Any]] = []
        self._last_size = -1
        
        # 确保目录存在
        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
        
    def append_trade(self, trade_data: Dict[str, Any]):
        """追加一条成交记录"""
        try:
            with open(self.file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(trade_data, ensure_ascii=False) + "\n")
            # 同步更新缓存
            self._cache.append(trade_data)
            if os.path.exists(self.file_path):
                self._last_size = os.path.getsize(self.file_path)
        except Exception as e:
            self.logger.error(f"❌ 写入成交账本失败: {e}")

    def load_all_trades(self) -> List[Dict[str, Any]]:
        """加载所有成交记录 (带简单缓存)"""
        if not os.path.exists(self.file_path):
            return []
            
        current_size = os.path.getsize(self.file_path)
        if current_size == self._last_size and self._cache:
            return self._cache
            
        trades = []
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        trades.append(json.loads(line))
            self._cache = trades
            self._last_size = current_size
        except Exception as e:
            self.logger.error(f"❌ 读取成交账本失败: {e}")
            
        return trades

    def load_recent_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        """加载最近的 N 条记录"""
        all_trades = self.load_all_trades()
        return all_trades[-limit:]

    def clear(self):
        """清空账本 (仅用于重置系统时)"""
        if os.path.exists(self.file_path):
            try:
                os.remove(self.file_path)
                self.logger.info("🗑️ 成交账本已清空")
            except Exception as e:
                self.logger.error(f"❌ 清空成交账本失败: {e}")
