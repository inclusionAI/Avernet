# Plan: Unified stage selections

## Approach
保留独立构建采样，用现有 common-config 保存有界版本记录和一条线上投影来源标记。原生发布记录决定版本身份；投影标记必须匹配当前 publication 和原生 online workflow，失配返回未知。投影显式使用 transactional_orm_session，事务只包含辅助 common-config 和 binding 写，不持有原生发布锁。

## Data Model Changes
无 DDL；ORM 映射已有列：
```python
# core/bot_knowledge/repository/models.py
online_enabled = Column(Boolean, nullable=False, default=False, server_default=false())
```

## API / Interface Changes
```http
GET /api/knowledge/bots/{bot_id}/available-knowledge-bases?owner_id=...&runtime_stage=draft|verify|online
GET /api/knowledge/bots/{bot_id}/conversation-knowledge-bases?owner_id=...&runtime_stage=draft|verify|online
```
默认 draft 保持个人 Bot 兼容；个人 Bot 非草稿拒绝。服务 Bot 顶层附加 selection_status / publication_id / captured_at，未知管理和会话均返回正常空页 {items: [], next_cursor: null}，不含未知状态元数据或底层错误。当前 grant 与 Bot admission 先于任何快照内容披露。

## Key Files & Functions
```python
# core/bot_knowledge/stage_selection.py (new)
class BotKnowledgeStageSelection:
    def read(self, *, bot, runtime_stage): ...
    def project_online(self, *, publication_id): ...
# core/service_bot/services/publish_flow/tasks.py
# existing progress poll: pre-sync identity, native terminal result, isolated projection
```
历史记录格式复用 publication_selection.py，线上标记使用相同 tenant/env/source_pk 坐标、config_key=projection:online。前后最多200 ID，原有草稿CAS元数据保持不变。绑定不存在时一次有界 Core bulk insert 插入 disabled/revision0 的线上投影行，显式从受 tenant guard 的源 Bot 盖章租户，不伪造草稿编辑。online读取以完整唯一键 tenant/env/bot_pk/kb_id IN 定位，再按space/online_enabled过滤，避免扫描禁用历史；相同投影重放不写DML。辅助marker更新使用旧值CAS；若SQLite不支持FOR UPDATE而产生跨版本竞争，CAS失败回滚全部辅助写入，不锁原生表。

## Risks & Mitigations
- 原生 SUCCESS 在回滚准入时可提前出现：投影触发必须来自实际 progress SUCCESS，而非状态扫描。来源标记还匹配原生 rollback_restored_from 与被降级版本已有 rollback.rolled_back_at/target_publish_id，避免旧标记在准入时提前复活；不增加原生ext字段。
- 晚到回调：online poll在原生sync前捕获有界workflow/rollback身份（Publication主键读；回滚时追加1次origin主键读），失败仍执行原生sync；辅助事务锁标记后重新核对原身份/current publication/source/status/workflow/token。不允许旧回调在缺marker时为新回滚补签。仅版本发布progress poll触发投影；普通restart不复制知识配置，也不为回滚签新token，已有有效版本marker保持不变。读取时再验证publish.online及回滚来源标记，不把旧投影视作新版本。
- 历史缺失/存储异常：内部保留 unrecorded/unavailable 用于诊断，对外正常空页；不回退草稿、不默认 false、不写回关闭。普通权限/参数/provider错误继续按原契约处理。
- 快照不是授权：始终与当前 provider grant 页相交，保留会话原生 admission。
- 共享 DB 等待：不新增线程/重试，不声称硬超时隔离。

## Restart boundary
普通同版本in-place restart不是新发布，不新增知识observer调用、不重采样、不改变已有有效选择。真正rollback由execute_rollback写新publish.online并进入ONLINE_PUB，实际成功后由progress poll投影。若recreate/retry改变身份却没有该发布完成观测，按best-effort保守空页；不为其新增补偿链路。

## Test Strategy
真实SQLite记录和服务入口覆盖空选择、缺失、损坏、跨tenant/space、草稿/预发/线上隔离、回滚、晚到回调、投影失败、CAS不变、当前授权撤销。保留个人Bot响应兼容；运行HTTP/DI/architecture与相关完整模块。前端额外验证stage参数、未知空列表无提示与lazy conversation，以及team entity与真实Owner分离。

## Rollout
用户最新指定目标 REL20261103，授权新建该目标公共 PR。其当前基线与已验证 dev 基线完全一致，仅更新交付文档，不改变功能代码；保留 dev PR #3439，不擅自关闭或合并。原 #3293 已由其他操作合入旧 Release，本轮不撤销、不导入整个旧 Release。不合并、部署、DDL或回填，不创建配套私有前端 PR。
