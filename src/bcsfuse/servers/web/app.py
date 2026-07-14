"""
BCS Fuse - FastAPI Web 应用适配器

职责：
- 复用现有 FastAPI 应用实例
- 可选：挂载公司标准中间件（当前不启用）

不改动：
- 现有路由
- 现有中间件
- 现有业务逻辑
"""
from src.interfaces.api.app import app as original_app

# 直接复用现有的 FastAPI 应用
app = original_app


# 可选：挂载公司标准中间件（当前不启用，避免引入不必要的依赖）
# 如需启用，请确保已安装 ant-sofapy-base 且验证无冲突
#
# try:
#     from sofapy_base.tracer.tracer import SofaTracerMiddleware, install_tracer_patches
#     install_tracer_patches()
#     app.add_middleware(SofaTracerMiddleware)
# except ImportError:
#     pass  # 中间件可选，不强依赖

__all__ = ["app"]