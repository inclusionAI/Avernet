# W2 tasks

- [x] B.0 kickoff — spec 目录、模块 README、feat 分支(feat/bot-config-manifest-w2-arc,源自 origin/dev)
- [x] B.1 `fetch/limits.py` — §5 常量、部署传输白名单、`FetchBudget`/`Resolver` 类型
      (初版白名单读 `BCM_FETCH_TRANSPORT_ALLOW` 环境变量;PR 评审改造为 application.yaml
      配置,见 R1)
- [x] B.2 fetcher 安全矩阵(35:内网 IP×11、多解析全验、resolve 失败→拒、重定向×4、
      Content-Length 谎报流式卡、逐类目限额、digest 不匹配、坏 digest 格式线前拒、
      注入头、逐跳 policy、非 2xx、http 白名单两向)
- [x] B.3 `guarded_fetcher.py` — 五层防御 + pinnned 连接 + `is_global`+组播显式拒绝
      (Python 把全球组播算 global 的坑,测试钉住)
- [x] B.4 解包矩阵(31)+ `unpack.py` — 链接类整类拒绝(超出 issue 原文,README 记权衡)、
      设备/遍历/上限、strip 无魔法、权限拍平、拒绝清场、双格式同树
- [x] 门禁 — E3 豁免登记;DCL/oversized/boundaries/api-layer/repository-native 通过
- [x] CI 全量 #1 — 15863 passed / line 88.02%(变行覆盖因未提交显示 N/A)
- [x] 批量终审(9 findings 全闭环):
      F1 高危 跨主机名复用已验证书连接 → **每跳新建 httpx.Client**(seam 改 BaseTransport
      注入),解析按次重跑钉测试;F2 传输错误归化 FetchFailedError(+负例);F3 解包写盘
      异常归化 UnpackError(路径冲突/中央目录 CRC 翻坏两条负例);F4 Host 头带非默认端
      口(+两条钉);F5 日志只出 host/digest 不出 URL;F6 zip symlink 负例补齐;F7
      ZipFile/TarFile 真关闭(去 finally:pass);F8 超时注释改 per-network-operation;
      F9 spec 表格环境变量名对齐实现。
- [ ] 提交 → CI 复跑(真实变行覆盖)→ PR

## PR 评审与变基(2026-08-31 晚)

- [x] R1 totalfrank(limits.py:66):环境变量方式"涉及镜像变更且过于隐式"→ 配置改走
      application.yaml。改造:`user_config.bot_config_manifest.fetch_transport_allowlist`
      (中性默认空)+ `limits.transport_allowlist_from_config` 纯解析(类型校验,坏块
      ValueError)+ `GuardedFetcher(transport_allowlist=…)` 构造注入;core 不再碰
      `os.environ`(AGENTS.md:73 裸环境访问只属于配置装载/引导/组合根/测试);
      测试 3 条 monkeypatch.setenv 改构造参数,新增解析/中性默认/shipped-yaml 5 条;
      golden 快照三 profile 按故意的配置变更重生。
- [x] R2 冲突处理 — dev 已合 W1(#1727),同模块双方都动:README add/add 合并为
      "W1 存储/校验 + W2 fetch 边界"双波叙事(Context Boundary 并入 fetch 公开面);
      flow_coverage 的 `bot_config_manifest` 条目 auto-merge 造出重复 key
      (后者静默覆盖前者),人工合并为一条并顺手去掉已失实的"feature switch/404"
      说法(W1 终版无开关,路由无条件挂载)。按仓库规范 rebase 不 merge。
