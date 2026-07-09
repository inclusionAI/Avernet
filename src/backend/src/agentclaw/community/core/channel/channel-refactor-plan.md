# Channel 模块重构计划

## Context

Channel 模块用于管理钉钉等外部渠道的认证配置，当前实现存在架构问题：
- 目录结构违反 `api/` + `core/` 分层规范
- Repository Protocol 与实现混合在同一文件
- 缺少独立的 `api/channel/schemas.py`

本重构将简化 Channel 模块结构，符合基本分层规范。

---

## ⚠️ 重要约束：不改变前端交互

**以下内容必须保持不变，严禁修改：**

### API 路径
- 前缀：`/api/channels`
- 端点：
  - `GET /api/channels` - 查询列表
  - `POST /api/channels` - 创建
  - `POST /api/channels/{id}/delete` - 删除
  - `POST /api/channels/{id}/updateStatus` - 更新状态
  - `POST /api/channels/{id}/update` - 更新配置
  - `GET /api/channels/{id}` - 查询详情

### 请求参数格式（必须完全一致）
```python
# GET /api/channels - Query 参数
type: str, identity_id: str, bind_bot_id: str

# POST /api/channels - Body
{
  "type": str,
  "description": str | None,
  "identity_id": str,
  "bind_bot_id": str,
  "config": { ChannelConfig }
}

# POST /api/channels/{id}/updateStatus - Query 参数
status: str  # "1" 或 "0"

# POST /api/channels/{id}/update - Body
{
  "type": str,
  "description": str | None,
  "identity_id": str,
  "bind_bot_id": str,
  "config": { ChannelConfig },
  "status": str
}
```

### 响应格式（必须完全一致）
```python
# ChannelResponse 字段
{
  "id": int,
  "type": str,
  "description": str | None,
  "identity_id": str,
  "bind_bot_id": str,
  "config": dict,
  "status": str,
  "gmt_create": str | None,
  "gmt_modified": str | None
}

# 列表响应
{
  "success": bool,
  "data": [ChannelResponse],
  "message": str
}

# 创建/更新响应
{
  "success": bool,
  "data": {"id": int},
  "message": str
}

# 删除/状态更新响应
{
  "success": bool,
  "message": str
}
```

### 服务逻辑（必须完全一致）
- `ChannelService` 保持对全局 `bot_service` 变量的依赖
- 所有业务方法签名和行为不变
- 同步到 openclaw/moltis 的逻辑不变

---

## Target Architecture (简化版)

```
src/agentclaw/
├── api/channel/
│   ├── __init__.py
│   ├── router.py              # HTTP 端点
│   └── schemas.py             # Pydantic Request/Response 模型
└── core/channel/
    ├── __init__.py
    ├── channel_service.py     # 业务逻辑
    └── channel_repository.py  # Repository Protocol + 实现
```

**简化决策：**
- Repository 放在 `core/channel/` 下，不拆分到 plugins
- 不创建 BotConfigPlugin，Service 直接依赖 BotService
- 不创建独立的 dependencies 层

---

## Implementation Steps

### Phase 1: 创建 API 层

#### Step 1.1: 创建 `api/channel/schemas.py`
- 从 `routers/channels.py` 迁移 Pydantic 模型，**字段定义必须完全一致**
- 包含：`ChannelConfig`, `CreateChannelRequest`, `CreateChannelResponse`, `ChannelListRequest`, `ChannelResponse`, `ChannelListResponse`, `UpdateStatusResponse`, `UpdateChannelRequest`

```python
# api/channel/schemas.py - 关键模型示例（完整字段）

class ChannelConfig(BaseModel):
    client_id: str
    client_secret: Optional[str] = Field(None, description="秘钥")
    card_template_id: Optional[str] = Field(None, description="卡片模板ID")
    card_template_key: Optional[str] = Field(None, description="卡片模板Key")
    enable_streaming_cards: bool = Field(False, description="是否启用流式卡片")
    dm_policy: str = Field(default="open", description="私信策略")
    allowlist: list[str] = Field(default_factory=lambda: ["*"], description="允许列表")
    reply_to_message: bool = Field(default=True, description="是否回复原消息")
    robot_code: str = Field(default="", description="机器人编码")
    aix_enable: bool = Field(default=True, description="是否启用AIX")
    aix_preview_url: str = Field(default="http://local.teamclaw.net:8001/preview", description="AIX预览URL")
    include_sender_name: bool = Field(default=True, description="是否包含发送者名称")

class ChannelResponse(BaseModel):
    id: int
    type: str
    description: Optional[str]
    identity_id: str
    bind_bot_id: str
    config: dict
    status: str
    gmt_create: Optional[str]
    gmt_modified: Optional[str]
```

#### Step 1.2: 创建 `api/channel/router.py`
- 从 `routers/channels.py` 迁移端点
- 使用 `Depends(get_channel_service)` 注入
- 保持现有 API 风格

### Phase 2: 创建 Core 层

#### Step 2.1: 创建 `core/channel/channel_repository.py`
- 定义 `ChannelRecord` dataclass
- 定义 `ChannelRepository` Protocol
- 实现 `SqliteChannelRepository` (SQLite)
- 实现 `ZdasChannelRepository` (ZDAS)
- 包含工厂函数 `get_channel_repository()`

```python
# core/channel/channel_repository.py
from dataclasses import dataclass
from typing import Protocol

@dataclass
class ChannelRecord:
    id: int
    type: str
    # ...

class ChannelRepository(Protocol):
    def insert_channel(...) -> int: ...
    def get_by_id(...) -> ChannelRecord | None: ...
    # ...

class SqliteChannelRepository:
    """SQLite 实现"""
    # ...

class ZdasChannelRepository:
    """ZDAS 实现"""
    # ...

def get_channel_repository() -> ChannelRepository:
    """工厂函数"""
    if _is_sqlite_mode():
        return SqliteChannelRepository(...)
    return ZdasChannelRepository(...)
```

#### Step 2.2: 创建 `core/channel/channel_service.py`
- 从 `services/channel/channel_service.py` 迁移业务逻辑
- 构造函数注入 `ChannelRepository`
- **保持对全局 `bot_service` 变量的依赖**（与现有代码一致）

```python
# core/channel/channel_service.py
from agentclaw.community.core.bot_management.services.bot_service import bot_service

class ChannelService:
    def __init__(self, repository: ChannelRepository) -> None:
        self._repository = repository
        # 注意：bot_service 使用全局变量导入，不通过构造函数注入

    # 业务方法保持不变...

def get_channel_service() -> ChannelService:
    """FastAPI Depends 入口"""
    return ChannelService(get_channel_repository())
```

### Phase 3: 更新路由挂载

修改 `servers/web/app.py`:
```python
# 旧导入
from agentclaw.services.openclawserver.server.routers.channels import router as channels_router
# 新导入
from agentclaw.community.api.channel.router import router as channel_router
app.include_router(channel_router)
```

新 `api/channel/router.py` 必须包含：
```python
router = APIRouter(prefix="/api/channels", tags=["channels"])
```

> 注：路由装饰器、路径参数、依赖注入方式与现有代码完全一致

### Phase 4: 测试迁移

#### Step 4.1: 迁移现有测试
```
tests/services/channel/ → tests/core/channel/
```

#### Step 4.2: 新增 API 层测试
- 创建 `tests/api/channel/test_router.py`

### Phase 5: 清理和向后兼容

#### Step 5.1: 添加 deprecation warning
在旧文件中添加警告

#### Step 5.2: Re-export 保持兼容

---

## Critical Files

| 文件 | 操作 |
|------|------|
| `api/channel/__init__.py` | 新建 |
| `api/channel/router.py` | 新建 |
| `api/channel/schemas.py` | 新建 |
| `core/channel/__init__.py` | 新建 |
| `core/channel/channel_repository.py` | 新建 |
| `core/channel/channel_service.py` | 新建 |

---

## Design Decisions

### 1. 不创建 plugin_api / plugins / dependencies

**理由:**
- 简化架构，减少文件数量
- Repository 实现数量少（仅 SQLite 和 ZDAS），无需独立目录
- 工厂函数直接放在 repository 文件中

### 2. 不抽象 BotService 为 Plugin

**理由:**
- BotService 已是稳定的服务，无需额外抽象
- 减少间接层，代码更直观

### 3. Repository Protocol + 实现放在同一文件

**理由:**
- 代码量不大，放一起便于阅读和维护
- 类似现有 `channel_repository.py` 的组织方式

---

## Verification

1. **单元测试**
   ```bash
   uv run pytest tests/core/channel/ -v
   uv run pytest tests/api/channel/ -v
   ```

2. **集成测试**
   ```bash
   ./scripts/local_setup.sh --local start backend
   curl http://localhost:8888/api/channels?type=dingding&identity_id=test&bind_bot_id=bot1
   ```

3. **向后兼容验证**
   - 确保旧导入路径仍可用
   - 前端调用正常