# 服务 Bot 发布 ignore 运维接口：核心代码设计

日期：2026-09-16。状态：接口已实现，交付门禁验证中；未部署。源码基于 GitHub inclusionAI/Avernet 的 REL20260917，PR 同样以该分支为目标。

## 1. 范围与既有链路

现有安装脚本使用 cp，读取目标容器 `/home/admin/.service_bot_publish_ignore`。本次设计新增 Backend → Engine 的结构化增删接口，文件读改写完全由 Engine 承担。保持 cp，不增加 tar、重启、发布或文件删除。

源码依据：
- Backend `community/adapters/http/service_bot/router_publish.py:69` 是既有发布 router；L843 附近的 restart_for_others 是 super_admin 运维入口。
- `core/service_bot/services/bot_publish_service.py:279` 的 get_publish_by_bot_version 按 publish_bot_id、owner_id、version、env 查版本，但 L479 提示组织 Bot 的 owner 过滤可能漏查。采用 BotRepository.get_by_id_and_entity（implementations/bot/bot.py:255）取得准确源 Bot PK，再使用 publishing protocol 的 list_by_source_bot(source_bot_pk, env) 精确筛选版本并校验唯一性，不能用 latest stage 查询替代。
- `core/service_bot/services/baas_service.py:2721` 枚举设备，L3650 invoke_http 支持 bind_id 与 device_uuid 固定到副本。
- Engine `community/api/bot/router.py:10` 已有 /api/bot router，现有 /config 调用 service 写运行文件。
- start_service.sh 将 ENTITY_ID、STAGE、VERSION 写入运行凭证并 export；现有 Credentials 数据模型未暴露这些全部字段，身份读取需最小扩展或专用只读投影，不能假设已有属性。

## 2. API

Backend：POST `/api/service-bot/publish/ops/publish-ignore`，平放既有 router。

```json
{"bot_id":"service_bot_id","entity_id":"entity_id","version":3,"stage":"online","operation":"add","path":"workspace/project/node_modules"}
```

- bot_id 为发布服务 Bot 标识，不是 BaaS bot_uuid；必须核对与指定实体的关系。
- version 为正整数，Backend 环境 env 从当前认证/部署上下文获取，不把 stage 当 pre/prod 环境。
- 首期 stage=verify|online；draft 没有相同的发布物安装语义，eval 可能带额外业务实例标识，本期明确拒绝，不能映射到其他阶段。
- operation=add|remove，path 为单个精确相对路径，采用已有 Shell 相同规范；拒绝绝对路径、换行、NUL、dot traversal、glob、开头 #/!、反斜杠和重复斜杠。保留文件名内部空格，允许 ./ 前缀及尾 / 规范化。
- 平台管理员可操作任意目标 Bot；普通已登录用户可操作自己拥有或具备管理权限的 Bot，复用现有发布修改/重启的协作者权限服务（默认 PermissionLevel.ADMIN，包含 OWNER）。公开可聊天/只读权限不等于管理权限。entity_id 是定位条件而非授权凭据。授权在设备调用前完成，保留真实 operator。
- add 已存在/remove 不存在均成功，changed=false；remove 只删规则，不删除业务文件。

Engine：POST `/api/bot/publish-ignore`，平放 api/bot/router.py。请求包含 expected_target（bot/entity/version/stage）、operation、path 和 request_id；不接受调用者指定 ignore 文件路径。

Engine 验证可信 Backend 调用及目标身份；最终用户可以是平台管理员或已授权的普通用户。不能只凭 body.operator、来源 IP 或普通聊天设备 token 推断写权限。实施前落实对应 guard，测试未授权直连被拒，以及有权限普通用户经 Backend 调用成功。

## 3. Backend 核心服务

```python
class PublishIgnoreService:
    async def change(self, command, operator):
        self.authorize_bot_mutation(command, operator)
        target = self.resolve_exact_target(command)
        # 包含当前 env、实体、真实 owner、精确版本、指定阶段绑定校验
        provider = self.providers.require(target.binding.device_provider)
        devices = provider.list_targets(target)
        # 固定 device_uuid；有限并发逐副本调用，不采用随机活跃实例路由
        results = await self.apply_to_devices(provider, target, devices, command)
        return self.summarize(target, results)
```

resolve_exact_target 的约束：
1. 当前租户/env 内唯一解析 Bot，核对 entity_id 和 service 类型，再从真实记录取 owner_id；entity_id 不等于 owner_id。
2. 使用已存在的 list_by_source_bot(source_bot_pk, env)，按 version 精确筛选并要求唯一，取该发布单 ext.binding[stage]。如需后续优化为专用精确查询，应保留 source_bot_pk/version/env 三个条件，不能改成 latest 或 owner 猜测。
3. 校验绑定有效、所属对象匹配、实际运行版本匹配。历史发布单可能保留已被新版本复用的 binding，不能只检查 binding 存在。
4. 目标已退役、版本不符、无指定阶段绑定、发布/重启正在变更目标时返回冲突或不存在；禁止 fallback 到最新版本或草稿。
5. 枚举目标设备并固定快照；默认影响该版本阶段当前全部运行副本。未就绪副本记录失败，不静默跳过。

按选中 binding.device_provider 分派，不根据 Bot 默认 provider 或 device_id 外形猜测：
- baas：binding.device_id 是 BaaS bot_uuid，list_devices_by_bot_uuid 枚举副本；逐台 invoke_http(bind_id=..., device_uuid=..., path='/api/bot/publish-ignore', method='POST', json=..., port=既有adapter端口配置)。沿用对应认证头。
- arca：binding.device_id 为直接 ARCA 绑定，不能当成 BaaS bot_uuid。当前精确阶段 binding 对应一个目标；通过 DeviceContextResolver.resolve_for_binding(binding_id, operator_id, bot_id=...) 和 ARCA builder 得到 conn_info，沿用 ArcaDeviceSyncService 的注入 HTTP 客户端、URL 和 headers 调相同 Engine endpoint。保留完整绑定值给 builder；不调用 BaaS 列设备，不直接写文件。
- 未支持 provider 明确报不支持，不回退 BaaS。通过任务专用 delivery 协议分派：BaaS 复用现有设备传输，ARCA 使用已注册的连接 builder 与注入的通用 HTTP 接口；不引入 corp 包、不修改通用 HTTP 客户端。
- DeviceContextResolver 只负责连接解析，不替代业务授权。对非 owner 的管理员/协作者，从已授权 Bot 记录提供身份及 bot_type，保留真实 operator；若连接 builder 有 owner 假设，仅做必要适配，不能冒用 owner 绕过权限。
- 每目标结果增加 provider、binding_id、target_id；BaaS 另有 device_uuid，ARCA 不伪造 BaaS UUID。同步 SDK 使用线程卸载，不修改通用 HTTP 客户端。

每副本返回 device_uuid、status、changed、entry_count、revision、错误类别。汇总全部成功才 success=true；部分失败为 PARTIAL，结果未知的超时为 UNKNOWN，保留成功项。重试仍固定原设备，且版本校验必须通过；不承诺跨副本事务或自动回滚。操作期间扩缩容发生变化须报告目标变化，不能把快照成功说成新副本也已配置。

## 4. Engine 文件服务

```python
class PublishIgnoreFileService:
    def change(self, expected_target, operation, path):
        normalized = validate_ignore_path(path)
        with self.lock_fixed_file():
            self.runtime_identity.assert_matches(expected_target)
            lines = self.read_fixed_file_or_empty()
            updated, changed = edit_rule(lines, operation, normalized)
            if changed:
                self.atomic_replace(updated)
            return self.describe(updated, changed)
```

- 固定文件 `/home/admin/.service_bot_publish_ignore`；锁使用独立固定 `.lock` 文件，不能锁随后被 replace 的 inode。Linux 使用 flock，锁与文件均拒绝 symlink、非普通文件并限制大小；锁持有时间有界。
- 校验的是受管理运行身份的版本和阶段，不能信任请求自报。运行时 V3 与 API 3 做明确定义的规范化；缺少可靠身份时拒绝修改，不能猜测。部署替换进程/身份与本次操作有竞态时需冲突返回。
- 文件不存在视为空。读改写在锁内进行；保留注释、空行和未相关规则；add 规范化去重，remove 删除所有等价规则行。不覆盖其他规则，不执行 shell。
- 同目录安全创建临时文件，写入、flush/fsync、设置 admin 可读写的受限权限，再 os.replace；失败清理自身临时文件。写失败不返回 changed=true。NFS 锁与 rename 语义须在实际挂载验证。
- Shell 使用文件描述符读取清单，原子替换使单次读取拿到完整旧版或新版。已经开始并读完清单的发布不受本次写入追溯影响。
- 规则仍只控制后续 cp，chown/chmod、阶段配置覆盖、Pool 整理不受该文件保护。

## 5. 生效与持久化边界

成功只表示目标实例文件已更新，下次安装读取时生效。version 是定址约束，不是文件中规则的版本命名空间；同一 home 复用后规则仍在。多副本 home 按设备隔离，必须逐副本写。

NAS home 可以跨保留该卷的重启保留文件；临时 home、新设备、重建或扩容不保证继承。本期不引入 Backend 规则数据库及自动下发，响应应明确 scope=current_instances；未来需要跨重建保证再增加持久化与启动播种。

## 6. Review / QA / Ship

- 允许新增：Backend 请求模型、router endpoint、PublishIgnoreService/DI、精确目标查询的最小接线；Engine 请求模型、既有 bot router endpoint、文件服务与管理授权 guard、运行身份最小投影和测试。
- 不触碰：Relay 引擎实现、Cron、发布状态机、通用 HTTP 客户端、制品构建和 cp 算法；不借运维接口触发发布重启。
- 日志：backend.publish_ignore.request / engine_request / engine_response / failure；engine.publish_ignore.request / success / failure。记录 request_id、真实 operator、业务输入、publish_id、binding_id、device_uuid、结果、耗时；token/认证头/凭证递归脱敏，整份文件只记长度与摘要。
- 测试：管理员/非管理员、实体错配、历史版本复用绑定、阶段不存在、多副本固定与部分失败、超时未知；add/remove 幂等、并发更新不丢规则、非法路径、CRLF/注释保留、symlink 拒绝、replace 失败旧文件完整；Engine 身份错配与普通用户直连拒绝；日志成功失败与脱敏；安装脚本实际读取修改后的清单且排除生效。
- Ship：先上线具备 ignore 的安装脚本与 Engine 新能力，再开放 Backend 运维入口；每副本核对代码和黑盒结果，未升级设备明确报不支持。本次授权范围为实现、自动评审与回归、以最新 GitHub REL20260917 为底 rebase、创建 PR 并修复检查失败；不部署或合并 PR。

## 7. 部署前提与独立交付边界

- Engine 新写接口必须验证 Backend 管理签名，不能复用普通聊天 token 作为写权限依据。Backend 完成最终用户的 Bot 管理权限判断后才签名；Engine 还须核对实际运行身份。
- 签名覆盖完整 `expected_target`、`operation`、`path`、`request_id` 和时间戳；接收方验证时效并使用常量时间比较。密钥缺失时 fail closed，不允许自动降级为匿名或普通设备 token。
- 使用 Ed25519：仅 Backend 配置 `SERVICE_BOT_PUBLISH_IGNORE_SIGNING_KEY`（PKCS8 PEM 私钥），Engine 仅配置 `SERVICE_BOT_PUBLISH_IGNORE_VERIFY_KEY`（SubjectPublicKeyInfo PEM 公钥）。两者通过受信任部署配置注入；私钥绝不能下发到 Bot 容器、工作区或日志。共享 HMAC 密钥方案已因跨 Bot 伪造风险放弃。轮换需协调 Backend/Engine，旧验签密钥失效时请求明确失败。
- Engine 在固定文件锁内持久化受限的已消费请求记录，拒绝重放。先消费请求再写规则；进程崩溃或文件写失败后，不把重发旧签名当作成功，应从 Backend 发起新请求并依据规则幂等语义重试。
- 按用户确认，不修改 `docker/agent/start_service.sh`。依赖实际部署使用的既有启动脚本提供完整运行身份（daas 启动脚本已保存这些字段）。未提供完整身份的实例仍拒绝变更，不使用请求中的目标填补身份字段。
- `agentclaw-daas-scripts` 中的 cp ignore 消费逻辑属于独立仓库，本 Avernet PR 不包含该仓库未提交变更。未升级安装脚本时接口更新文件不会产生复制排除效果；不能把接口测试通过等同于发布性能已验证。
- 固定 ignore 文件按实例 home 生效，不按 version 建命名空间；本次不会重启服务或删除业务路径。生产 NAS 的锁/rename 行为与滚动升级后的真实黑盒验证属于后续部署验证，不在本次本地测试中冒充完成。
