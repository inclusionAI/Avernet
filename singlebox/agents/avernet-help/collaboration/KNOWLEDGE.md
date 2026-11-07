# KNOWLEDGE.md

优先知识源：
- 用户当前明确表达的协作目标和约束。
- 已安装 `avernet-collaboration-help` Skill 的工作流与参考。
- Runtime 可发现的公共 `bcs-coordination` Skill 中的自定义协作 schema、诊断和 CLI 说明。
- 当前 `bcs-cli` 返回的校验结果。

使用原则：
- 当前 Runtime 和服务端结果优先于历史示例。
- schema 按需读取，不凭记忆新增字段。
- `collaboration draft create/update` 上传原文件并校验已存固定版；随后显式调用该固定版的 `draft preview`，只回复 preview 生成的短引用。
- 副屏按固定版本读取原文、核对 hash 并重新校验；预览不表示已经执行。
