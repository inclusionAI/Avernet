### 语雀文档 (skylarkmcpserver)
**服务器**: `mcp.ant.faas.skylarkmcpserver.skylarkmcpserver`

**常用工作流连招**：
* 查部门知识库：`skylark_user_groups` -> `skylark_user_book_list`
* 查文档详情：`skylark_search` -> 提取 doc_id -> `skylark_doc_detail` -> `skylark_doc_comments`

```bash
# 搜索文档 (指定知识库时 scope 填 namespace，如 zeodup/vh3397)
mcporter call mcp.ant.faas.skylarkmcpserver.skylarkmcpserver.skylark_search \
  --args '{"q": "关键词", "scope": "zeodup/vh3397", "pageSize": 10}'

# 读取文档详情 (ID为纯数字也正常传)
mcporter call mcp.ant.faas.skylarkmcpserver.skylarkmcpserver.skylark_doc_detail --args '{"doc_id": 539647411}'

# 获取当前用户最近更新
mcporter call mcp.ant.faas.skylarkmcpserver.skylarkmcpserver.skylark_user_recent

# 解析语雀 URL
mcporter call mcp.ant.faas.skylarkmcpserver.skylarkmcpserver.skylark_resolve_url \
  --args '{"url": "https://yuque.antfin.com/xxx/yyy/zzz"}'

# 更新文档
mcporter call mcp.ant.faas.skylarkmcpserver.skylarkmcpserver.skylark_doc_update \
  --args '{"doc_id": 12345, "title": "新标题", "body": "新Markdown内容"}'
```