# Bot 列表 Engine Path 配置加载优化设计

日期：2026-07-17  
状态：已确认，进入实现

## 背景

生产 `GET /api/bots/by-owner-or-collaborator` Trace 显示，列表为每个
Bot/Engine 构造路径时都会调用 `_get_aidesktop_root()`。旧实现随后重新扫描、打开并
`yaml.safe_load` 配置文件。30 次路径解析可占用 2180ms，成为接口第一主瓶颈。

## 目标

- 一次进程启动只通过统一配置 Provider 加载配置，不在路径计算中重复解析 YAML。
- 保持 `AIDESKTOP_ROOT` 环境变量高于配置文件的现有优先级。
- 保持所有路径返回值与当前实现一致。
- 让所有调用路径工厂的入口受益，不引入列表接口专属缓存。

## 方案

`_get_aidesktop_root()` 先读取 `AIDESKTOP_ROOT`。未设置时，调用现有
`core.config.provider.load_config()`，从其 `user_config.aidesktop_root` 读取配置；字段
缺失时继续使用 `/aidesktop`。

`load_config()` 已在 composition root 注册 Provider，并对 `AppConfig` 做进程级缓存；
测试通过替换 Provider 或 `load_config()` 保持隔离。因此删除路径工厂中重复的 YAML
扫描逻辑，不增加第二套缓存及缓存清理 API。

不采用：

- `_get_config_value()` 上增加 `lru_cache`：会复制已有配置缓存，并使环境变量或测试
  Provider 切换产生额外失效语义。
- 仅在列表请求中预计算根路径：只能优化一个入口，并要求扩大 BotService 接口。

## 验证

1. 回归测试连续计算多个 Bot Engine 路径，断言配置 Provider 只读取一次。
2. 覆盖环境变量覆盖、配置值与默认值三种语义。
3. 运行 path factory、Bot 列表 endpoint 目标测试及完整 backend 测试。
4. 灰度后检查慢请求 Trace：`_get_config_value` 的逐 Engine 日志应消失，耗时不再随
   Engine 数量线性增加。

## 非目标

本次不改 `can_edit_bot` 的逐 Bot 发布记录查询；该 DB/OSS N+1 作为后续优化独立处理。
