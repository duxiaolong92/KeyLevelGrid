"""
简化日志模块
"""

import logging
import sys
import os
from typing import Optional
from pathlib import Path


# 全局日志文件路径
_LOG_FILE_PATH: Optional[str] = None
_LOG_FILE_HANDLER: Optional[logging.FileHandler] = None


def setup_file_logging(
    log_dir: str = "logs",
    log_file: str = "key_level_grid.log",
    log_path: Optional[str] = None,
    env_key: str = "LOG_FILE_PATH",
) -> str:
    """
    设置日志文件输出
    
    Args:
        log_dir: 日志目录
        log_file: 日志文件名
        log_path: 完整路径优先级最高（可覆盖 log_dir/log_file）
        env_key: 允许通过环境变量覆盖日志路径（默认 LOG_FILE_PATH）
    
    Returns:
        日志文件完整路径
    """
    global _LOG_FILE_PATH, _LOG_FILE_HANDLER
    
    # 优先级: 显式参数 log_path > 环境变量 > log_dir/log_file
    env_path = os.getenv(env_key, "").strip()
    if log_path:
        _LOG_FILE_PATH = log_path
    elif env_path:
        _LOG_FILE_PATH = env_path
    else:
        if not log_file:
            log_file = "key_level_grid.log"
        path_obj = Path(log_dir)
        path_obj.mkdir(parents=True, exist_ok=True)
        _LOG_FILE_PATH = str(path_obj / log_file)
    
    # 创建文件处理器
    _LOG_FILE_HANDLER = logging.FileHandler(_LOG_FILE_PATH, encoding='utf-8')
    _LOG_FILE_HANDLER.setFormatter(logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    _LOG_FILE_HANDLER.setLevel(logging.DEBUG)  # 文件记录所有级别
    
    return _LOG_FILE_PATH


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """
    获取一个配置好的 logger
    
    Args:
        name: logger 名称
        level: 日志级别
    
    Returns:
        配置好的 logger 实例
    """
    logger = logging.getLogger(name)
    
    if not logger.handlers:
        # 终端输出
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))
        logger.addHandler(stream_handler)
        
        # 文件输出（如果已配置）
        if _LOG_FILE_HANDLER:
            logger.addHandler(_LOG_FILE_HANDLER)
        
        logger.setLevel(level)
    
    return logger
