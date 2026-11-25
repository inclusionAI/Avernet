# 离线历史双份 Pack 修复

`repair-pack-layout.mjs` 只检查本地已有 Git 对象，不访问 DB 或网络。适用范围是
根 `workflow.pack.yaml` 与 `packs/{packId}/workflow.pack.yaml` 指向同一个
workflow、两份 workflow YAML 字节不同、资源只需保留在根 Pack 的已确认历史布局。
它不是通用迁移工具；不修改服务端、前端或发布流程。

## 输入与显式来源

需要 Node.js 20+、Git，以及 clawweb 已有的 `yaml` 2.x 依赖；不需要启动服务或
安装整套依赖。必须提供本地普通/裸仓库路径、完整 commit SHA、Pack/workflow ID，
以及**恰好一个**来源选项：

- `--workflow-source root`：使用输入 commit 的根 workflow YAML。
- `--workflow-source nested`：使用输入 commit 的嵌套 workflow YAML。
- `--workflow-source-file /absolute/draft.yaml`（或 `.yml`）：逐字节使用本地 YAML。
- `--workflow-source-file /absolute/db-draft.json`：使用本地**直接 WorkflowSpec JSON
  对象**。JSON 同时是合法 YAML，直接保留文件字节，避免数值重序列化失真。必须有匹配的顶层 `id`；不接受整条 DB row、JSON
  字符串或 `spec_json` 包装。请在工具之外只读导出/解码草稿为该文件；工具不读写 DB。

不比较版本号或时间，也不推断“更新”的副本。来源中的 `version`、`facade`、
`updatedAt`、`executor.args` 等字段一起保留；例如要保留 DB 草稿 v37，应明确
选择其导出的 JSON，而不是可能只有 v35 的 nested 文件。根 manifest 的 `facades`
及其他元数据和注释不重新序列化，保留原始 blob 与 mode。

## 调用

以下 `/srv/...` 为占位本地路径；SHA 必须替换为实际存在的完整 commit SHA，不能
传 `HEAD`、分支、tag、短 SHA 或 revision 表达式。

```sh
# 默认 dry-run：仅输出 JSON 路径、OID、mode、计数及 parent 恢复锚点，无文件内容。
node /srv/Avernet/apps/evolverun/clawweb/scripts/repair-pack-layout.mjs \
  --repo /srv/clawflows.git --commit "$INPUT_COMMIT_SHA" \
  --pack-id example-pack --workflow-id example-workflow \
  --workflow-source root

# 使用人工确认的本地 DB 草稿；只有此显式开关才写候选对象。
node /srv/Avernet/apps/evolverun/clawweb/scripts/repair-pack-layout.mjs \
  --repo /srv/clawflows.git --commit "$INPUT_COMMIT_SHA" \
  --pack-id example-pack --workflow-id example-workflow \
  --workflow-source-file /srv/exports/db-draft-v37.json --write-candidate
```

`--write-candidate` 使用临时独立 index、`hash-object --no-filters` 和
`commit-tree`。候选唯一 parent 是输入 SHA；仅替换根 manifest 声明的 workflow
文件内容（保留其 mode），并删除严格嵌套 manifest 和 workflow 两文件。
所有其他文件（包括二进制、空文件、可执行脚本、根 manifest）的 OID/mode 必须不变。
Git 作者/提交者来自仓库 `user.name`/`user.email` 或显式 `GIT_AUTHOR_*` /
`GIT_COMMITTER_*` 身份环境变量，不设置假身份。其他 Git 路由/index/config 环境覆盖
不会继承。不会运行内容过滤器、checkout hooks 或签名程序。

不会修改工作区、用户 index、任何 refs/reflogs；不会 push/fetch/checkout、移动
`wf`/saved 分支或历史 tag。输出 `candidateCommit` 只是**无 ref 的候选**，不是已经
发布或保存的版本。独立审查与任何后续发布不在本工具范围内。

## 拒绝与恢复

多 workflow、其他 manifest、错 ID、不安全/冲突路径、symlink/gitlink、任何额外
嵌套资源（包括空目录树）均拒绝，不通过删除未知资源“修复”。所有非允许变更的 tree
条目都要验证；不能证明无损就失败。错误不回显 YAML/JSON 或 Git 原始内容。

原始不同 YAML 均留在 parent 历史；报告的 parent SHA、原路径与 blob OID 可用于
只读恢复：`git -C /srv/clawflows.git show "$INPUT_COMMIT_SHA:原路径"`。该命令会输出
历史内容，应仅在可信本地终端使用。候选对象没有 ref 保护，可能被 GC；需长期保留时
由操作者另行执行授权的归档流程。失败的候选写入可能留下无 ref 对象，但不改变历史。

## 测试

```sh
node --test /srv/Avernet/apps/evolverun/clawweb/scripts/__tests__/repair-pack-layout.test.mjs
```

测试只用临时真实 Git 仓库，包括普通/裸仓库、SHA-1/SHA-256、不同 YAML、v37 JSON、
资源逐字节/mode 保留、错误布局，以及工作区/index/refs/reflogs 和离线命令审计。
