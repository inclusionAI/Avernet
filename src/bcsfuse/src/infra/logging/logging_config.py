"""
统一日志配置模块

职责：
1. 配置根 logger 的级别、格式、输出目标
2. 支持从 YAML 配置文件读取配置
3. 支持环境变量覆盖配置
4. 支持不同环境（dev/pre/prod）的差异化配置

配置优先级（从高到低）：
1. 环境变量（如 LOG_LEVEL）
2. YAML 配置文件（logging 段）
3. 默认值

使用方式：
    在应用入口（app.py/main.py）中调用：
    >>> from src.infra.logging.logging_config import configure_logging
    >>> configure_logging()

YAML 配置示例：
    logging:
      level: INFO
      format: "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
      date_format: "%Y-%m-%d %H:%M:%S"
      enable_file: false
      file_path: ""
      max_bytes: 104857600
      backup_count: 5
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Any, Optional

# 默认日志格式 — 使用 %(traceid)s 与 sofapy_base 日志格式一致
DEFAULT_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - [%(process)d] - [%(processName)s] - [%(traceid)s] - %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _load_yaml_config() -> dict[str, Any]:
    """
    从 YAML 配置文件加载日志配置

    Returns:
        日志配置字典，如果加载失败返回空字典
    """
    try:
        import yaml
    except ImportError:
        return {}

    # 尝试查找配置文件
    config_paths = []

    # 1. 从环境变量获取配置路径
    config_dir = os.getenv("CONFIG_PATH", os.getenv("BCSFUSE_CONFIG_PATH", ""))
    if config_dir:
        config_paths.append(Path(config_dir) / "application.yaml")

    # 2. 默认配置路径
    current_dir = Path(__file__).parent
    config_paths.extend([
        current_dir.parent.parent.parent.parent / "configs" / "application.yaml",
        Path("configs") / "application.yaml",
    ])

    for config_path in config_paths:
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)
                    if config and isinstance(config, dict):
                        logging_config = config.get("logging", {})
                        if logging_config:
                            return logging_config
            except Exception:
                continue

    return {}


def configure_logging(
    log_level: Optional[str] = None,
    log_format: Optional[str] = None,
    date_format: Optional[str] = None,
    enable_file_handler: Optional[bool] = None,
    log_file_path: Optional[str] = None,
    max_bytes: Optional[int] = None,
    backup_count: Optional[int] = None,
) -> None:
    """
    配置统一日志系统

    应在应用启动时调用一次，配置根 logger，所有子模块 logger 将继承此配置。

    配置优先级（从高到低）：
    1. 传入的参数
    2. 环境变量（LOG_LEVEL, LOG_FORMAT 等）
    3. YAML 配置文件（application.yaml 中的 logging 段）
    4. 默认值

    Args:
        log_level: 日志级别 (DEBUG/INFO/WARNING/ERROR)
        log_format: 日志格式
        date_format: 日期格式
        enable_file_handler: 是否启用文件日志
        log_file_path: 日志文件路径
        max_bytes: 单个日志文件最大字节数
        backup_count: 保留的备份文件数量
    """
    # 从 YAML 加载配置作为基准
    yaml_config = _load_yaml_config()

    # 1. 确定日志级别（优先级：参数 > 环境变量 > YAML > 默认值）
    level_str = (
        log_level
        or os.getenv("LOG_LEVEL")
        or yaml_config.get("level", "INFO")
    ).upper()
    numeric_level = getattr(logging, level_str, logging.INFO)

    # 2. 确定日志格式
    fmt = (
        log_format
        or os.getenv("LOG_FORMAT")
        or yaml_config.get("format", DEFAULT_FORMAT)
    )
    date_fmt = (
        date_format
        or os.getenv("LOG_DATE_FORMAT")
        or yaml_config.get("date_format", DEFAULT_DATE_FORMAT)
    )

    # 3. 确定文件日志配置
    should_enable_file_raw = (
        enable_file_handler
        if enable_file_handler is not None
        else os.getenv("ENABLE_FILE_LOG")
    )
    if should_enable_file_raw is not None:
        should_enable_file = str(should_enable_file_raw).lower() in ("true", "1", "yes", "on")
    else:
        should_enable_file = yaml_config.get("enable_file", False)

    file_path_str = (
        log_file_path
        or os.getenv("LOG_FILE_PATH")
        or yaml_config.get("file_path", "")
    )

    # 4. 确定轮转配置
    max_bytes_val = (
        max_bytes
        or int(os.getenv("LOG_MAX_BYTES", "0"))
        or yaml_config.get("max_bytes", 100 * 1024 * 1024)
    )
    backup_count_val = (
        backup_count
        or int(os.getenv("LOG_BACKUP_COUNT", "0"))
        or yaml_config.get("backup_count", 5)
    )

    # 5. 构建 handlers
    handlers: list[logging.Handler] = []

    # stdout handler - 始终启用
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(fmt, date_fmt))
    handlers.append(console_handler)

    # file handler - 可选
    if should_enable_file:
        if file_path_str:
            file_path = Path(file_path_str)
        else:
            # 默认日志目录（与 local_setup.sh 一致）
            file_path = (
                Path(__file__).parent.parent.parent.parent.parent.parent
                / "scripts"
                / ".dependences"
                / "logs"
                / "bcsfuse.log"
            )

        # 确保目录存在
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass  # 目录创建失败，跳过文件日志

        try:
            # 使用 RotatingFileHandler 防止日志无限增长
            file_handler = logging.handlers.RotatingFileHandler(
                file_path,
                maxBytes=max_bytes_val,
                backupCount=backup_count_val,
                encoding="utf-8",
            )
            file_handler.setFormatter(logging.Formatter(fmt, date_fmt))
            handlers.append(file_handler)
        except Exception:
            pass  # 文件日志初始化失败，不影响控制台日志

    # 6. 配置根 logger
    logging.basicConfig(
        level=numeric_level,
        format=fmt,
        handlers=handlers,
        force=True,  # 强制重新配置，即使已有配置
    )

    # 6.1 安装自定义 LogRecord 工厂，使所有日志记录自动携带 traceid/trace_id
    # 使用 RecordFactory 比 Filter 更健壮：字段在 LogRecord 创建时就注入，
    # 不会因 handler/filter 链变化导致 KeyError
    from src.infra.trace_context import install_trace_record_factory
    install_trace_record_factory()

    # 7. 设置第三方库的日志级别（避免过于 verbose）
    _configure_third_party_loggers()

    # 8. 记录配置完成日志
    logging.info(
        "Logging configured: level=%s, format=%s, yaml_config=%s",
        level_str,
        fmt,
        bool(yaml_config),
    )


def _configure_third_party_loggers() -> None:
    """降低第三方库的日志级别，避免干扰业务日志"""
    third_party_loggers = [
        "urllib3",
        "urllib3.connectionpool",
        "requests",
        "requests.packages.urllib3",
        "httpx",
        "httpcore",
        "asyncio",
    ]
    for name in third_party_loggers:
        logging.getLogger(name).setLevel(logging.WARNING)
