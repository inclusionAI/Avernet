# Editor / AutoHeal 保存与部署

- 仍使用原来的保存、部署按钮；文件视图默认选择草稿。修改文件或画布不会自动保存。
- 保存同步数据库文档与完整 Pack（工作流 YAML、脚本、资源及文件模式）到 `wf/<id>`。
  保存不会移动当前生效 tag。保存请求发出之后的新本地改动继续保留，需要再次显式保存。
- 部署审阅比较**当前生效 tag**与**已保存的草稿(wf)**，发布 tag 并更新 history/active，
  不改写草稿分支。未保存的本地改动不会随部署发布。

## 现有读取与回执字段

| 场景 | `baseRef` | commit / head 语义 |
| --- | --- | --- |
| 正常草稿 | `origin/wf/<id>` | `baseCommit === draftHead` |
| 生产 current | `refs/tags/deploy/<id>/#N` | 数据库 active tag 的 peeled commit；没有 active history 返回 404 |

`current` 不能用 wf 或旧草稿冒充；生产 tag 丢失不是首次部署的证明。

写请求继续使用 `sourceVersion`、`sourceCommit`、`expectedSavedHead`：
`sourceCommit` 指读取来源，`expectedSavedHead` 指 wf 写入目标 tip。
已有不同 wf 草稿时，禁止以 current 覆盖它。服务器负责 CAS、完整 Pack 保存与授权。
包含分支身份的保存回执必须指向 `wf/<id>`；后置读取也必须确认 wf，而不是旧草稿。

部署保留 `savedCommit`、`snapshotProtocol: saved-tree-v2` 等 wire 字段/协议，
其 saved 语义仍为已保存快照，不是分支名。前端内部 `activeRef` 仅供展示，不加入请求。
失败、未知结果、审阅超时或过期响应不清空本地草稿，不自动重试、恢复、授予权限或降级写入。

ClawWeb 不读取、比较、回退或写入 saved/。草稿只取 wf；没有 wf 时返回未保存/快照不存在，不自动采用其他分支。
