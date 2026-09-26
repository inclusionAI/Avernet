# W3 tasks

- [x] C.1 DDL(无 env 轴 + 轮换即替换注释)+ ORM(Row 记录脱离安全)
- [x] C.2 前缀段边界 TDD(24:content-negative/双编码=等价写法/点段/端口/host 大小写)
- [x] C.3 服务(47:fail-closed 两 profile、掩码、预留类型、轮换、空库留白)
- [x] C.4 绑定:每跳现读(轮换下次生效)、名字-only 错误、前缀拒
- [x] C.5 公开面:4 路由 + REFUSED 全组(挂 refuse_app_only) + ADMISSION/AUTHORIZATION
      ×4 + errors_source_credentials(子类在基类前)+ gateway 新前缀 spec + 本仓 yaml
- [x] C.6 endpoint-framework 9 案(不能失败的路由用 pre-handler 401 作 error 侧)
- [x] 修收编途中产生的四处误(裸 404 具名、error dict 顺序、`if True` 残段、Row to_row
      交接)
- [ ] 全量 CI → 终审 → 提交 → push → PR(分支纯净,单一提交)

## 与既有 PR 的合并序预告

W1/W2(另两条 PR)同改 admission.py / responses.py / _ALLOWLIST / _PAIRS / flow_coverage
/ api 与 repository README。同文件落点冲突轻度、语义相容;后合并者 rebase 时两侧
条目并取即可。W3 的 duck 绑定让 W2 接线在任一合并序下都成立。

## 待 ocb 侧(REFUSED 前缀必做)

`/openapi/v1/source-credentials/**` 的 route_security(本仓 application.yaml 已落)
需在 ~/IdeaProjects/ocb 同步一行——记忆规则:REFUSED 才需要双仓写,本组恰好是。
