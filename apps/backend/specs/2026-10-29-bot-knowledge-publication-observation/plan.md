# Plan: Unified stage selections

## Approach
保留独立构建采样，用现有 common-config 保存有界版本记录和一条线上投影来源标记。原生发布记录决定版本身份；投影标记必须匹配当前 publication 和原生 online workflow，失配返回未知。投影事务只包含辅助 common-config 和 binding 写，不持有原生发布锁。

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
默认 draft 保持个人 Bot 兼容；个人 Bot 非草稿拒绝。服务 Bot 顶层附加 selection_status / publication_id / captured_at，未知管理项 enabled=null，未知会话空项附 unknown 状态。当前 grant 与 Bot admission 先于任何快照内容披露。

## Key Files & Functions
```python
# core/bot_knowledge/stage_selection.py (new)
class BotKnowledgeStageSelection:
    def read(self, *, bot, runtime_stage): ...
    def project_online(self, *, publication_id): ...
# core/service_bot/services/publish_flow/tasks.py
# existing progress/restart polls: native terminal result first, isolated observer second
```
历史记录格式复用 publication_selection.py，线上标记使用相同 tenant/env/source_pk 坐标、config_key=projection:online。前后最多200 ID，原有草稿CAS元数据保持不变。绑定不存在时插入 disabled/revision0 的线上投影行，不伪造草稿编辑。

## Risks & Mitigations
- 原生 SUCCESS 在回滚准入时可提前出现：投影触发必须来自实际 progress SUCCESS，而非状态扫描。
- 晚到回调：锁辅助标记后重新核对 publication/source/status/online workflow；读取时再验证来源标记，不把旧投影视作新版本。
- 历史缺失/存储异常：明确 unknown/unavailable，不回退草稿、不默认 false。
- 快照不是授权：始终与当前 provider grant 页相交，保留会话原生 admission。
- 共享 DB 等待：不新增线程/重试，不声称硬超时隔离。

## Test Strategy
真实SQLite记录和服务入口覆盖空选择、缺失、损坏、跨tenant/space、草稿/预发/线上隔离、回滚、晚到回调、投影失败、CAS不变、当前授权撤销。保留个人Bot响应兼容；运行HTTP/DI/architecture与相关完整模块。前端额外验证stage参数、未知只读与lazy conversation。

## Rollout
仅本地代码/测试；未经新授权不推送、合并、部署、DDL或回填。原 #3177 不变。旧验证记录不代表修订代码通过。
