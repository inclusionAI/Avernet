# Plan: Current knowledge summary

## Approach
在知识 repository 内集中摘要编解码、作用域与唯一键查询。发布完成调用该组件，
不依赖会独立提交的通用配置 repository。Bot 锁先行是全局序列化点；各路径其余锁
按固定顺序获取且无反向重入：完成路径 Bot -> binding -> publication -> config，
恢复路径 Bot -> operation -> publication -> binding。

## Data Model Changes
None. 只使用已有列和唯一索引；不执行 DDL。

```python
# core/bot_knowledge/repository/online_selection.py (new)
def config_scope(source_pk, bot_id, entity_id, env): ...
def read_selection(session, bot) -> OnlineSelection: ...
class OnlineSelection:
    def unchanged(self, session): ...
def lock_summary(session, publish): ...
def write_summary(session, row, *, publish, source, outcome, ids, materialized): ...
```

## Key Files & Functions
```diff
# core/bot_knowledge/repository/store.py
- result = type_coerce(BotPublishModel.ext, JSON)[...]
+ selection = read_selection(session, bot)
+ ids = selection.ids  # bounded + scope checked
+ query.filter(Binding.kb_id.in_(ids), selection.unchanged(session))
```
```diff
# core/repository/implementations/publishing/knowledge_selection.py
- _newer_observed_completion(...)  # JSON historical query
+ summary = lock_summary(session, publish)
+ reject_newer_observed_workflow(summary, workflow_id)
- clear_query.filter(Binding.kb_id.not_in(ids)).update(...)
+ rows = _selected_bindings(session, source, previous_ids - ids)
+ _write_online_rows(session, source, rows, False, previous_space)
+ write_summary(...)  # same Session after CAS
```

## Dependencies
None. Existing BotCommonConfig ORM model only. Reserved key constant in kernel.

## Risks & Mitigations
- 旧摘要不存在时不能有界推断历史：读取 UNKNOWN；可信重新发布显式初始化，无扫描回退。
- 通用配置管理覆盖风险：保留专用键，大小写/重音/宽度/尾空格别名同样拒绝；持久层检查实际匹配行。
- 优化器可能把 100-ID IN+space 选成范围扫描：先按完整唯一键读行再校验 space/flags；批量写用带租户等范围条件的 PK 等值语句。
- 最长多字节 IDs：正常结果只序列化一份集合，UNKNOWN 才保留显式历史 materialized IDs。
- 双语句读取竞争：绑定查询同语句校验摘要原始值，变更时 fail closed。
- 部署前确认已有配置表；本地测试不能证明生产存在或性能。

## Alternatives Considered
发布单 ext 当前指针需要参与创建/删除/版本变更，不符合最小耦合；新增表/索引被排除。

## Test Strategy
先写有界工作量、缺失配置拒绝、差集唯一键写入、管理接口保护测试，再接入生产代码。
保留真实完成重试、rollback/restore、Owner A-B-A、外租户、故障事务与 artifact 回归。
