# Backend Cron 定向实现报告

日期：2026-10-11。Worktree：service-bot-cron-target；源码基于 GitHub Avernet dev 193256c75。未提交、未推送、未部署，PR由主编排 agent 处理。

## 既有链路与改动边界

既有 HTTP Cron router → CronRelayService → runtime target/context resolver → DeviceAdapterTransport 保留。新增 CronTargetedOperationsMixin 只承担显式单实例目标选择与单次请求；复用 RuntimeBindingResolutionService、DeviceService 与 CollaboratorService。没有改动通用传输、设备控制层、数据库、Engine 或 Relay 调度。

## 改动及行为

- cron/router.py：创建与列表/详情/修改/删除/触发/执行历史/运行中接收 runtime_stage、device_id（device_uuid兼容别名）、create_by_id；将真实登录者作为 operator_id 传递。原有权限 interceptor 保留。runtime target schema 实际接入原始JSON和query字段校验，别名冲突/无stage/非法类型拒绝。所有正常下游响应保留 error_code；运行中响应保留 success/message，避免下游失败误作空成功。
- cron/schemas.py：增加实际使用的 CronTargetRequest 和公开创建schema目标字段。
- cron_targeted_operations.py：已授权 owner-scoped Bot → creator委托权限（现有ADMIN/OWNER）→现有runtime binding解析→设备active与binding membership→唯一目标→一次transport。多实例缺device明确拒绝；恰好一个运行设备可固定该UUID。Caller显式目标仅online，现有Caller未就绪或错设备不回退父Bot，不创建容器。
- cron_relay.py：required DI依赖 RuntimeBindingResolutionService + CollaboratorService；定向列表/运行中接线。普通服务列表逐实例查询，避免遗漏新建容器本地任务；保留原按stage过滤和失败项聚合。
- cron_runtime_operations.py：创建支持指定stage；原无新参数继续草稿父服务路径。单实例管理统一定向helper，发布实例允许完整编辑、删除和单次运行；旧无实例广播/限制兼容。
- cron_runtime_targets.py：新增固定binding、Caller/creator/operator元信息；定向访问始终按binding解析。共享服务连接沿用owner授权代理（真实local builder拒绝非owner连接），Caller连接携带真实operator；真实operator始终独立用于日志与授权。列表行增加device_id/device_uuid、Caller目标create_by_id。
- cron/README.md：更新上下文依赖与单实例接口语义。
- 4个既有Cron构造测试增加required DI实参；2个旧“实例共享任务列表”测试改为逐实例和枚举失败真实行为。新的行为、HTTP注册用例与同namespace真实graph检查见 test_cron_targeted_operations.py / test_cron_target_routing.py。

## 日志

事件：cron.request、cron.target.resolved、cron.transport.request、cron.transport.success/failure、cron.response、cron.failure；HTTP schema校验失败为cron.create.failure。

记录system/direction、method/path、bot/stage/binding/device、creator/operator、任务业务入参、已知任务响应/列表字段、error_code、耗时。notify按实际Engine契约记录enabled/user_ids。未知认证扩展字段、连接headers、token、Authorization、Cookie不dump。sessionKey/session_key保持可见。解析失败只记录入站failure，不虚构出站失败。已知领域异常记录具体业务消息；未知传输异常记录类别与安全摘要，原异常继续抛出。

## 本轮验证

环境：Python 3.12，使用已有虚拟环境 `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/design-teclaw-caller-session-token-20261010/apps/backend/.venv/bin/python`；PYTHONPATH始终指本worktree apps/backend/src。

- 初始受影响Cron+HTTP现有单测与租户隔离：171 passed。
- 最新集中行为、实际注册HTTP runner、schema、同namespace DI：248 passed，1424个无关HTTP用例deselected。命令：`PYTHONPATH=apps/backend/src <python> -m pytest apps/backend/tests/community/core/cron apps/backend/tests/community/endpoints/test_cron_target_routing.py apps/backend/tests/community/endpoints/test_endpoint_runner.py -k 'cron or target_schema or same_namespace' -q --tb=short --cov=agentclaw.community.core.cron --cov=agentclaw.community.adapters.http.cron --cov-report=term-missing --cov-report=json:/tmp/service-bot-cron-backend-focused-coverage.json`。
- CronTargetedOperationsMixin 91/91可执行行覆盖100%；对本任务Cron相关生产改动行与新mixin统计169/170=99.41%。唯一路由非object拒绝分支已另补直接adapter行为测试，由主agent全量coverage再核验。
- HTTP注册用例实际执行由test_endpoint_runner驱动；单独case文件运行不等于实际HTTP场景已跑。
- ruff check Cron core/adapter和两个新测试 PASS；git diff --check PASS。所有修改生产源文件<1000行（最大cron_relay.py921行）。

## 边界及风险

创建人是路由/审计metadata，不新增引擎持久化schema。指定Caller列表请求必须携带create_by_id；历史父服务聚合列表保持原共享服务语义。此改动不清理既有重复任务、不保证请求重试幂等或容器替换后的任务迁移。真实ARCA部署/周期执行未验证，以上为单测、真实HTTP测试与实际DI证据。
