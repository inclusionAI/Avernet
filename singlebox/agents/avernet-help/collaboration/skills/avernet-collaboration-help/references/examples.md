# 常见协作结构

这些是需求建模提示，不是可替代当前 schema 的完整 YAML 模板。字段写法必须以 Runtime 中
`bcs-coordination/references/custom-collaboration-schema.md` 为准，并通过当前服务端校验。

## 串行评审

适合“调研 → 起草 → 审核 → 最终交付”。为每个步骤分配一个逻辑 participant，审核结论进入
修订步骤，唯一 `final_output` 节点负责最终交付。

## 并行收集后汇总

适合多个专业角色独立分析同一输入。入口分出多个并行节点，随后进入单一汇总节点；不要让
多个分支各自产生最终出口。

## 固定轮次修订

仅在用户明确需要重复评审/修订时使用当前 schema 支持的 Fixed Loop。Loop 次数、body 和
outcome 按公共 schema 定义；多个 Loop 可以同级存在，也可以按 schema 的层级和作用域规则
嵌套。按当前服务端返回的诊断处理定义。

## 不应写入定义的内容

- 真实 Bot UUID、token、私密 URL 或个人敏感信息；
- 当前 schema 没有的猜测字段；
- 单次请求的秘密或一次性输入；
- 前端组件名、Tab 身份或执行授权。
