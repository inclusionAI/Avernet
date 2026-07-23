"""OpenClaw 兼容导入；实现已迁移到中性 Skills Pool 模块。"""

from __future__ import annotations

import sys

from engine.community.plugins.skills_pool import layout_sync as _implementation

sys.modules[__name__] = _implementation
