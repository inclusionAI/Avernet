"""
Cron Lifecycle Constants

上次使用的引擎记录文件路径。
生命周期管理已移至 EngineManager。
"""

from pathlib import Path

# 上次使用的引擎记录文件
LAST_ENGINE_FILE = Path.home() / ".openclaw" / "last_engine"
