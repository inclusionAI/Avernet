# 错误教训详细案例（按需查阅）

> 速查表见 memory.md 错误教训速查，本文件包含详细案例。

---

## 🔴 2026-04-27：值班表查询错误

**问题**：未核对当前日期就凭记忆报错值班人员。

**正确做法**：用 memory.md 中的公式计算，禁止凭直觉。

---

## 🔴 2026-04-16：工具名凭记忆调用

**问题**：log-queries.md 文档明确写了工具名，但习惯性用了错误的旧名 `queryLogs`。

**正确做法**：
1. 先查阅 skill/references/ 文档
2. 确认正确工具名
3. 确认参数格式
4. 有历史教训则先参考

**永远记住**：文档 > 直觉，流程 > 经验

---

## 🔴 2026-04-16：Langfuse 查询方式错误

**问题**：直接用 Python urllib 访问 Langfuse API 导致 DNS 解析失败。

**正确做法**：使用技能提供的脚本
```bash
/opt/conda/bin/python3 /home/admin/.openclaw/workspace/skills/skills-local/teamclaw-support/scripts/langfuse_query.py --user-id "103892" --days 0.02 --json
```

**关键**：必须用 `/opt/conda/bin/python3`（Python 3.12），不是 `/usr/bin/python3`

---

## 🔴 2026-04-13：DIMA content 格式混乱

**问题**：嵌套代码块导致 DIMA 平台显示混乱。

**正确格式**：使用【标题】+ 简单列表，禁止嵌套 ```、复杂表格、HTML标签。

---

## 🔴 2026-04-13：DIMA 空间ID错误

**问题**：使用了 W24001004413 空间，且错误传了 projectId。

**正确**：workspaceId="W26001113566"，不传 projectId。

---

## 🔴 2026-04-10：消息重复回复

**问题**：工具调用后再生成总结文本，导致重复。

**正确**：一条用户消息只对应一个最终输出。工具输出已包含结果时不重复总结。

---

## 🔴 2026-04-10：DIMA 未指定负责人

**问题**：创建缺陷时未指定 --processor-id。

**正确**：必须显式使用 `--processor-id` 参数指定模块负责人工号。

---

## 🟡 2026-04-09：DIMA 日期范围错误

**问题**：startDate=20250101 导致遗漏2024年的缺陷。

**正确**：统计类查询使用 startDate=20240101 或更早。

---

## 🟡 2026-04-08：DIMA 漏统计

**问题**：limit=100 导致分页截断。

**正确**：统计查询使用 limit=500 或更大值。

---

## ✅ 2026-04-18/03：语雀工具名错误（已修复）

**问题**：使用了不存在的 `skylark_doc_search` 等工具名。

**正确**：`mcporter call mcp.ant.faas.skylarkmcpserver.skylarkmcpserver.skylark_search q="关键词" book_id=xxx`