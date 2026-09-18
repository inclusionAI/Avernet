# 数字员工接入前置核验

日期：2026-09-15。状态：前置尚未接齐，未开始数字员工业务实现。

本次授权以 AgentPass 调用改造和执行身份解耦准备就绪为前提。以下区分
源码实现、实际组合基线和未验证事项，不能将源码存在等同于部署可用。

## 已核实的源码能力

- Avernet HEAD `105257bbeaa8a85aab65ed1f41201be77649dd3c` 包含执行身份服务、
  持久化模型、SQL 和运维接口。
- `src/backend/src/agentclaw/community/core/execution_identity/service.py`：
  仅服务 Bot 可重签；保持 owner 不变；先保存 PENDING，调用重签，查询 token
  并核对执行工号，运行实例更新成功后才激活身份。
- `src/backend/src/agentclaw/community/core/bot_management/services/bot_service.py`
  的 token 热更新涵盖草稿、线上、验证及 ACTIVE caller binding，并聚合失败。
- OCB HEAD `565f45f28a` 的企业 Passport 插件已有 `reissueAgentCredentials`
  序列化及 `entityId` / `executionWorkno` 支持。
- 本地凭证服务实现的重签路径会从历史 Passport 恢复 MCP、CLI、Skill 等能力
  再重签。因此不能仅凭 Python 重签 DTO 的空 MCP 列表推断它会清空能力。
  此结论是源码核验，未验证远端当前部署版本。

## 明确的组合缺口

OCB 当前提交记录的 `ocb-public` gitlink 和本地子仓库 HEAD 均为
`49b808f2bd4bf36c8fb95325a7895c9ebe280b01`，未包含上述执行身份改造。
OCB `Dockerfile.backend` 从 `ocb-public/src/backend/src/agentclaw/community`
复制公共后端，不会读取旁边独立的 Avernet 工作目录。

因此，独立 Avernet 中的运维接口、身份服务、持久化及新 Passport 契约，
目前不会随该 OCB 组合构建交付。需先将 OCB 的公共子仓库依赖对齐到含前置
改造的提交，再在这一组合上验证，不能仅验证两个独立目录各有部分代码。

## 实际执行的验证

1. 三个工作目录的初始 `git status --short` 均为空。
2. 对比公共仓库提交：后端差异包括执行身份服务、DI、运维接口及插件契约。
3. 使用 Avernet 根虚拟环境运行身份单测：失败于缺少 `pytest`，未执行用例。
4. 使用已有 OCB 后端虚拟环境、将 `PYTHONPATH` 指向当前 Avernet `src`，运行：

   ```sh
   DEPLOY_PROFILE=test python -m pytest \
     tests/community/core/bot_management/services/test_execution_identity_change.py \
     -q -o cache_dir=/tmp/digital-employee-pytest-cache
   ```

   首次被 SDK 用户日志目录写权限阻断；获得提升权限后重试，失败于测试
   conftest 初始化：`Space Skill multipart limits require compatibility review
   for Starlette 1.2.1`。当前源码要求 1.3 系列，当前锁文件为 1.3.1。
   **这不是身份单测断言失败，也不是测试通过；用例尚未执行。**

未执行：完整后端回归、企业插件组合回归、数据库迁移验证、真实重签与容器
注入、SOFAMQ 消费、数字员工 HTTP 联调、前端测试及构建。

## 接入需求已明确，后续实现应保持的边界

- 绑定归属 `ac_bots` 元数据，仅服务 Bot，存在可运行实例即可；不能要求先发布。
- 注册消息以事件 ID 幂等，可信详情校验平台、Bot、工号关联；绑定处理须可恢复，
  不能重试就反复重签，也不能在 token 注入失败时标记全链路成功。
- 非公开 MCP 新增需代申请并展示查询到的进度；公开性必须取真实数据字段。
- 现有 MCP 同步会根据激活列表重建 Passport scope；接入时必须为已绑定 Bot
  保留线上仍需的资源。草稿移除不能直接缩减共用凭证的能力。
- 最终审批关联不可变能力快照、发布记录及外部任务；重复、过期或其他任务的
  审批消息不能触发上线。成功后复用现有发布流程，并同步最终能力集。
- 仅修改指定老前端，依据 PRD 增加入口、标识和审批交互；未绑定 Bot 保持原流程。
- 数字员工平台、SOFAMQ 和企业鉴权的实现放企业层，公共核心使用明确契约，
  不把私有端点和凭证写入开源默认配置。

## 接入前还需验证的真实环境事项

- 对齐后的 OCB/Avernet 组合及锁定依赖能够执行身份、Passport 和运行实例回归。
- 执行身份表迁移已在目标环境执行；真实凭证服务已部署对应重签契约。
- 数字员工平台分配的平台编码、服务端鉴权配置和独立消费者 Group；不从文档
  示例猜测生产配置，不在报告或聊天中记录凭证值。
- 数字员工侧回调列表/详情使用的可信鉴权和 Bot 寻址约定。
- 真实服务 Bot 完成重签、授权完成、全部目标容器 token 更新，以及失败重试验证。

本轮只记录核验结果；未修改业务代码、依赖版本或 OCB 子仓库指针。
