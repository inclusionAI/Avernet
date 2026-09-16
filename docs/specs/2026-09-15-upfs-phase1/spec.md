# Bot UPFS：普通与服务草稿创建/普通重启

## 本次只做什么

创建接口 `POST /api/bots` 对普通 Bot 和服务 Bot（新建草稿）共用：
**命中灰度且模板支持 → 保存 UPFS 选择 → BaaS 组装挂载 → SDK 创建。**
未命中或模板预检失败则保存 NAS。两种 Bot 不各写一套初始化逻辑。

普通 Bot 和服务草稿的普通重启走已有 BaaS Bot 的 `/update`，读取原策略，
不重新灰度、不补写旧 Bot 策略，并保持已有实例/存储身份。

本次不交付创建结果/失败原因回写、已发布服务版本的重启、服务发布/升级、
恢复草稿、个人转服务、扩容、Caller 或旧 NAS 数据迁移。创建失败继续使用原有
任务状态和日志排查，不在存储策略中再维护一套状态。

## 可复用的方法边界

- `BaasService.initialize_bot_storage(bot_id, entity_id, env, engine, user_id, template_uuid)`：
  普通/服务创建共同调用的入口，负责接入实际模板能力查询并返回存储类型。
- `BotStoragePolicyService.initialize(...)`：独立完成 `storage_policy` 的读取、
  灰度选择及唯一初始化；已有策略直接返回，不重跑灰度、不创建实例。
- `_resolve_storage_policy(bot_id, entity_id, env)`：按方案集中读取/解析策略，
  请求组装不重复解析 JSON，也不自行创建策略。
- 创建调用初始化方法；普通/草稿重启复用统一读取方法，不初始化缺失策略。
  因此没有策略的旧 Bot 仍使用 NAS，不因重新拉起容器参与首次灰度。

## 三个评审点

### 1. 创建时选对存储

- Backend 仅在普通/服务 Bot 的明确首次创建入口触发灰度；使用真实业务用户和实际模板。
- 配置为 `bot_storage/upfs_rollout`。仅 enable=1 生效；检查全放量、引擎及用户
  规则，非法规则不能扩大范围。BaaS 查询当前租户/环境的 ONLINE 模板能力，不泄露配置秘密。
- 预检失败使用 NAS；数据库读写失败必须抛出，不能把已确定的 UPFS 偷换为 NAS。
- 主要代码：`common_config/bot_config_service.py`、`baas_device_service.py`、
  BaaS 模板能力接口与 `_device_template_service.py`。

### 2. 记住选择，不因创建重试而切换

`ac_bot_common_config` 按 `(bot_id, entity_id, env, config_key)` 唯一。
`config_key=storage_policy` 的 `config_value` 只保存：

```json
{"storage_type":"upfs","source":"rollout"}
```

- NAS/UPFS 都保存。唯一键和事务保证并发初始化只有一个决定，失败回滚。
- 不保存 `creation_status`、`last_error` 或 `attempt_id`，不增加创建历史记录。
- 普通/服务 Bot 共享请求组装只读取已保存策略，避免创建重试或后续复用时丢失选择；
  普通重启保持原存储配置；旧 Bot 无策略仍走 NAS，不补写记录。
  这不包含 Bot/Device 丢失后新建兜底的数据保留，也不代表真实挂载验证。
- 主要代码：配置服务、`repository/implementations/config/bot_common_config.py`、共享 `baas_service.py`。

### 3. 真正传到 SDK 挂载

- Backend 保留原存储 ID/路径规则，UPFS 挂载 `/home/admin`；Backend 不存 Volume ID。
- BaaS 从模板当前环境读取 Volume，转换为项目 `VolumeMountSpec`，渲染原
  `{device_uuid}`，校验路径，禁止与 NAS 混用；不能跨环境回退 Volume。
- `upfs.subpath_size_bytes` 缺失/非法/不可读时用 1073741824。旧 Storage 的
  quota/permission 不作 UPFS 配置，统一转换 read_only=false。
- Facade 允许新字段并再次校验模板；保留共享更新路径的销毁前校验，避免已有
  UPFS 实例被错误参数先删后报错。普通与草稿重启使用同一条受保护路径。
- 企业插件转换为 ARCA SDK 的 `volume_mounts` 和 SDK DTO。核心不导入企业 SDK。
  NAS 调用保持兼容；stub 只记录，ACK/Docker/local_proc 明确拒绝 UPFS。
- 主要代码：BaaS `_device_service.py`、`paas/_facade.py`、`_arca_paas_service.py`、
  OCB 企业 `_arca_sdk.py`。其余 Protocol/DTO/DI 是让参数传递完整的配套。

## 部署与边界

- 公共 DDL：`src/backend/src/agentclaw/community/core/common_config/sql/2026_09_15_bot_common_config.sql`；
  OCB 部署副本：`src/backend/sql/20260915_ac_bot_common_config.sql`，两份须一致。
- 先部署表、Backend 和企业 BaaS（`arca-sandbox==1.3.0` 及 lock），配置当前环境
  Volume，再开启创建灰度。已有 Bot 使用后不得直接重绑模板 Volume。
- 关闭灰度仅停止新选择；不能删除策略作为回滚，否则没有迁移数据就切换了存储。
- 未执行生产配置、DDL 或数据迁移。真实 ARCA 挂载、数据保留和 MySQL/ZDAS
  事务仍需预发验证，不能仅凭模拟调用测试声明生产可用。

## PR 评论与 CI 修复

- 路由文件删去重复参数说明，恢复到 983 行；不修改 1000 行 CI 门禁或豁免。
- 部署组件通过 `supports_bot_storage_policy` 声明能力，核心不再判断组件名称。
- 模板服务的运行环境从 bootstrap 注入；Facade 使用同一次环境快照，不新增探测。
- 创建/重启/更新共用模板契约检查；Volume 查询仍全部在 BaaS，Backend 不访问其表。
- 四个社区 ARCA 插件共用契约测试：支持者保留挂载，不支持者在外部操作前明确拒绝。
- 真正的 `BotService.restart_bot → BaasService.upgrade_bot → /update` 测试覆盖
  普通/服务草稿 × UPFS/NAS/无策略，检查不重新灰度、不重新分配、存储身份不变。

## 验证（2026-09-15）

- 创建/重启专项和文件大小门禁：90 passed。
- Backend 配置/仓储/设备/服务 Bot/Bot 管理/DI 及相关架构回归：3299 passed、
  3 个此前复现的基线环境失败（缺 pymysql、AWS 代理、企业模块可见性）。
- 企业 BaaS 全部 unit/contract：656 passed、15 skipped。
- 公共 BaaS 全量 unit/architecture：9054 passed、3 skipped、8 xpassed；
  排除 1 项已知的全仓格式基线检查，本轮改动文件格式检查通过。
- GitHub CI 的最终结果见 PR 验证记录。

测试使用真实 SQLite 和 SDK DTO，远程调用为模拟。未验证真实挂载；
创建结果回写、已发布服务版本操作和完整发布/升级不作为本版验收项。
