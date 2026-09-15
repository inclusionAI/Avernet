# Bot UPFS 第一期：个人 Bot 创建与重启

## 本次范围

- **仅 personal Bot 接入 UPFS**。创建入口和 payload 策略读取处增加 personal 判断；
  通用初始化、模板预检和 BaaS 挂载能力不删除，后续接服务草稿可放开判断并补验证。
- 新建个人 Bot：已有策略则复用；否则读取 `bot_storage/upfs_rollout`，
  命中后复用原模板详情接口检查当前环境 Volume 配置，有效才保存 policy，再按原链路申请设备。
- **NAS 不写 storage_policy**。开关关闭、未命中、模板无 Volume 或能力预检失败，
  均沿用 NAS 链路，不新增 NAS 记录；配额统一读取通用 storage 配置。
- 个人重启/设备补建：只读取已有 policy，不读创建灰度、不补写 policy。
  无策略使用原 NAS；手动插入 UPFS policy 后可读取该选择，但不负责迁移文件。
- 服务 Bot 创建、草稿重启、已发布服务、发布升级和 Caller 保持原链路，
  不读写本期 policy，但构建 Storage 时也读取通用配额。不是关闭接口或开放 UPFS。
- BaaS `/update` 在 ARCA 层可能销毁并重新申请容器；这不是首次创建 Bot，
  不能重新运行创建灰度，也不能称为 ARCA 容器原地重启。

本期接入现有 DeviceService → BaaS 设备申请路径；teclaw 独立预配等旁路保持原状，
不在本轮扩展其 UPFS 接入。

## 创建链路：准备与原申请分开

1. `BotService._provision_created_bot` 保留原 `apply_device` 调用，personal 分支在其前
   调用 `prepare_bot_storage`，没有重命名原有创建、申请或升级函数。
2. Router 按原规则选择 Provider；非 BaaS 的准备方法为空操作。
   准备结果复用原 `device_provider` 参数固定本次选择，原申请不再重复选 Provider。
3. BaaS 准备方法按原规则解析实际模板（含模板白名单覆盖），调用
   `BaasService.initialize_bot_storage`，然后返回原申请参数的覆写。此步骤不创建设备。
4. 准备结果通过已有 `template_config` 在本次请求内携带模板 UID/UUID 和存储选择。
   `_PreparedBotCreation` 只用于避免重复选模板和重复读策略，组装请求前移除；
   不入库、不发送到 BaaS、不修改调用者的原配置。没有新建一套 storage 透传参数。
5. 原 `apply_device` 继续执行校验、申请、绑定及状态推进；组装 payload 时修改已有
   Storage 的 type，Backend → BaaS 复用原 `storage` 字段。

重启/补建仍直接调用原申请或更新方法，不调用 `prepare_bot_storage`。
没有 `initial_storage_decision` 或 `initial_storage_user_id` 参数；创建灰度用户复用 owner_id，
不能用可能为团队 ID 的 entity_id 代替用户。

## 可复用策略方法与数据

`BotStoragePolicyService.initialize(...)` 独立负责策略读取、灰度判断和 UPFS 唯一初始化，
不创建设备。模板能力由回调提供，策略层不访问 HTTP，也不查 BaaS 数据库。
`BaasService.initialize_bot_storage(...)` 复用 `GET /api/v1/device-templates/{template_uuid}` 接入模板预检，不新增能力接口。

`ac_bot_common_config` 唯一键为 `(bot_id, entity_id, env, config_key)`。
`config_key=storage_policy` 的 `config_value` 示例：

```json
{"storage_type":"upfs","source":"rollout"}
```

- UPFS 必须在申请容器前成功入库；写失败直接报错，不发起创建。
- 并发 UPFS 初始化由事务和唯一键收敛到一条记录，不覆盖已有选择。
- NAS 不创建记录，也不为 NAS 占用初始化事务。历史/手工 NAS policy 可以读取，
  但本期不会主动生成 NAS policy。
- 已提交 UPFS 的创建重试和重启不重新灰度；关闭开关不会把它降成 NAS。
- **NAS 未保存决策，因此未完成的 NAS 创建再次重试时可能按当时的开关重新选择。**
  不承诺 NAS 创建重试固定选择；正常旧 Bot 重启不调用初始化，因此不受灰度变化影响。
- 不保存创建状态、失败原因或历史尝试；失败通过现有任务状态和关联日志排查。

## 旧链路兼容与错误边界

- 开关关闭时不查询模板能力、不写 policy；NAS 仅配额改为通用配置（未配默认 "1G"），
  其余 payload 与旧实现保持一致。服务创建/草稿重启不读取或初始化 policy，但读取通用配额。
- 新表尚未部署时，仅将明确的“表不存在”（MySQL 1146 / SQLite 对应错误）视为无策略；
  关闭灰度仍可创建 NAS。开启灰度且选择 UPFS 时，缺表会导致写入失败，禁止无策略创建。
- **数据库超时、权限错误及非法 policy 不等于“没有 policy”**，必须报错，不能静默改用 NAS。
  关闭灰度只停止新选择，不是禁用已有 UPFS 的策略读取；因此不能承诺数据库故障下所有
  个人 Bot 仍可创建/重启。这是防止误降级和数据目录切换的安全边界。
- 表部署并承载 UPFS 后不能删表、删策略或直接改为 NAS 来回滚。已有数据的存储迁移
  必须先完成独立的数据迁移流程。服务 Bot 如已实际使用试验版 UPFS，不能直接部署本期
  范围收紧版本，应先清点和制定迁移方案。

## BaaS 与 SDK

- Backend 不查 BaaS 数据库；命中灰度后复用原模板详情接口（租户隔离、ONLINE），检查
  模板 UUID、ARCA 类型和当前环境的 `upfs_volume_id_pre/prod`。不再新增能力接口或返回模型。
- 旧接口会返回完整 config（包含凭据相关字段）；此次模板调用关闭响应正文日志，错误消息也
  不带接口返回正文。Backend 只判断 Volume 配置有效性，不写入 policy/Storage，不持久化它。
  这不再是“Backend 从不获取 Volume ID”；实际挂载由 BaaS 传递模板配置，内部 ARCA plugin 校验并执行。
- BaaS 复用已有模板配置取当前环境 Volume ID，按原规则渲染 storage_id。
  Volume ID 写入已有 `metadata["upfs_volume_id"]`（保留键），不再新增 plugin 参数或配置字段。
  原 arca_metadata 不被就地修改，后续用户 metadata 合并不能覆盖此键；Facade 沿用原透传逻辑。
- Backend 的 `ac_common_config` 使用 `business_code=bot_storage`、`param_code=storage`、
  `env=pre/prod`，`param_value={"quota":"1G"}`。复用配置 JSON，不新增表/数据库列。
  NAS/UPFS 共用此配置。Backend 只读取原字符串填入 `storage.quota`，不转字节；缺失、非字符串、空白或读取失败默认
  `"1G"` 并记录日志。配额独立于灰度开关，重启读取配额不重新做灰度决策。
- BaaS 不再读取系统配额配置。UPFS plugin 将 quota 转成 SDK `subpath_size_bytes`；
  本方案 `1G = 1Gi = 1073741824` 字节，K/M/G/T 使用 1024 倍率，支持相应 i/B/iB 后缀及
  可精确转换为整数字节的小数。无单位整数串按字节解释。非法、非正、非整数字节或超出
  有符号 64 位上限时在内部 plugin 创建阶段失败，不回退 NAS。公开库不保留容量解析或挂载校验。
- 企业 `_arca_sdk.py` 按 `storage.type` 适配：NAS 原转换函数保持不变，配额改为读取通用配置；UPFS 从原
  Storage 的 storage_id/path/quota/permission 构造 SDK 自带 volume_mounts，不新增业务模型。
  `permission == "0777"` 映射 `read_only=false`，其他值（含空值）映射 `true`。
- plugin 从 metadata 读取并消费保留键，不将 Volume ID 留在 SDK metadata；其他 metadata
  原样保留，不修改调用方字典。公开层不判断 NAS/UPFS，模板有 Volume 时均透传；NAS 在内部插件忽略该保留键。plugin 不查库、不查询模板。
  SDK 升级为 `arca-sandbox==1.3.0`，公共核心不导入企业 SDK。
- 原 Storage 五字段及 Backend storage ID/目录规则不改；UPFS 挂载 `/home/admin`。
  `create_sync_sandbox` 保持原参数列表，没有新增 Volume ID/配额参数。
- 只修改企业 ARCA SDK plugin；stub、ACK、local Docker、local process 恢复基线，不新增 UPFS 判断。
  公开 core/service 撤回 UPFS 专属判断、Facade 复核和插件名称检查，不新增预检接口。
  重启恢复原 NAS 顺序：先销毁、再创建；配置错误可能在旧容器销毁后才由内部 plugin 检出。

## 日志与部署

- Backend：按 `bot_id + entity_id + env` 查询 `[upfs_rollout]`、`[storage_policy]`，
  可见命中原因、预检、NAS 跳过入库、UPFS 初始化/复用、策略读取/持久化失败。
- `event=payload_prepared` 关联 request_id、模板 UUID、类型/阶段、实际存储和挂载路径。
  BaaS 沿用原创建/重启日志；内部 `[arca_sdk]` 记录创建失败/成功，Backend 记录配额回退。
- ARCA 创建参数日志屏蔽 metadata 中的 upfs_volume_id；内部参数校验错误不回显原始 Volume，SDK 异常沿用原日志处理。
- 公共 DDL：`src/backend/src/agentclaw/community/core/common_config/sql/2026_09_15_bot_common_config.sql`；
  非公开库部署副本：`src/backend/sql/20260915_ac_bot_common_config.sql`，两份需一致。
- 先部署表、Backend 和企业 BaaS/SDK，配置当前环境 Volume，再开启灰度。
  非公开库代码与公共库类型有版本依赖；单独提交不代表整条 UPFS 链路已完成配套部署。

## 验证状态

本次以真实 SQLite、实际创建/重启入口和模拟 BaaS 调用验证范围及错误边界。
最终测试结果随提交说明记录。尚未执行生产 DDL、真实 ARCA 挂载、端到端数据保留或
生产 MySQL/ZDAS 验证，不能仅凭本地测试宣称生产已验证。

本轮本地验证（2026-09-15）：
- Backend 配置、设备、服务 Bot、Bot 管理和架构回归：**3512 passed**。
- 其中存储专项及真实创建范围/错误边界：**120 passed**。
- 公开 BaaS UPFS 专项/架构：**136 passed，1 deselected**（排除整仓格式检查）。
- 变更 Python 文件 Ruff lint、diff whitespace 检查通过；原 Backend 函数名未删除或改名。
- 本轮未跑完整 Backend/BaaS 全仓测试、企业 BaaS 全量或真实容器 E2E；
  以上结果不替代远端 CI，也不将之前版本的全量测试结果当作本轮结果。

## 本轮精简修正（2026-09-15）

- 删除自定义 `VolumeMountSpec` 和 `Storage.upfs`，原 Storage 定义恢复为基线版本。
- 删除 `get_storage_capability`、专用路由/返回模型/协议方法和模板服务环境注入。
- 复用原模板详情 GET；添加当前环境、模板身份/状态、非法 Volume 与响应凭据不落日志测试。
- 按最新要求复用 metadata 的保留键传 Volume ID，复用 storage.quota 传容量字符串，不新增函数参数或 Storage 字段。
- 函数签名保持原样，但 UPFS 语义和 Volume 参数传递仍要求企业/公共库版本对齐，内部插件不再导入公共库的 UPFS 校验函数。

前轮验证（不含本次 metadata/容量字符串整改）：Backend 相关回归/架构 **3523 passed**（其中存储专项 **131 passed**）；
公开 BaaS unit/architecture **9075 passed、3 skipped、8 xpassed、1 deselected**（整仓格式检查）；
企业 BaaS unit/contract/architecture **665 passed、15 skipped**。尚未验证本轮远端 CI 或真实容器挂载。

## 通用存储配额更正（2026-09-15）

配置键统一为 `bot_storage/storage`，不再读取 `upfs_storage`。
NAS 与 UPFS 共用 `quota`，默认 "1G"；不挂 storage 的部署不读取此配置。
此项是对 NAS 配额来源的明确调整，不代表 NAS 请求逐字节不变；目录、权限、类型和 SDK 转换不变。
服务/Caller 读取通用 NAS 配额不等于开放 UPFS；灰度配置仍为 `bot_storage/upfs_rollout`。
本节与插件范围收敛一并提交，最终提交/推送状态见提交说明及语雀记录。

## 最终 BaaS 分层收敛（2026-09-15）

- 公开 BaaS 仅保留模板 Volume 属性、按环境选择并经 metadata 透传；原 Storage、设备 ID 替换、创建函数参数不变。
- 回滚公开 core/service 的 UPFS 解析、预检、特殊重启分支、插件名称判断与 Facade 复核。
- `_validate_upfs_storage`、`_parse_upfs_quota` 与 SDK 适配全部位于非公开 `_arca_sdk.py`。
- 不新增销毁前插件预检。插件创建校验失败仍不调用 SDK，但旧容器可能已经被原重启流程销毁；原流程记录重建失败，不会自动恢复旧容器。
- Backend 灰度、policy、通用 quota 配置保持不变。其他 sandbox plugin 保持基线。

本轮验证：公开 BaaS unit/architecture 9036 passed、3 skipped、8 xpassed、1 deselected；随后调整专项断言复测 27 passed。企业 BaaS unit/contract/architecture 718 passed、15 skipped。真实挂载未验证；提交/推送状态见语雀记录。
