# W2 — 带防护的 fetcher 与归档流水线（#1470）

> 计划来源:`docs/superpowers/plans/2026-08-31-bot-config-manifest-implementation-plan.md` Phase B。
> 分工归属:lucas-xzp(机件实现项)。**依赖:— 阻塞:—**（第 1 天即可独立开工）。
> 验收:issue #1470 为唯一权威。参照引擎仓 `src/engine/.../plugins/resource_materialization.py`
> 抄结构不 import——后端此前无任何 SSRF 防护,这是新机件。

## 交付物

- `core/bot_config_manifest/fetch/limits.py`(schema §5 限额单一来源,deployment 白名单入口)
- `core/bot_config_manifest/fetch/guarded_fetcher.py`(SSRF 防护传输)
- `core/bot_config_manifest/fetch/unpack.py`(zip/tar.gz 安全解包)
- `CredentialInjector`/`AuthorizationPolicy` Protocol 声明——**只声明不绑定**(W3 绑定)
- 测试:安全矩阵(内网 IP 全家/重绑定/逐跳重定向/流式超限/坏 digest)+ 解包矩阵(zip-slip 变体/权限拍平/strip 精确)

## 关键验收 → 实现要点

| issue 验收 | 实现 |
| --- | --- |
| 仅 https;http 仅部署白名单 | scheme 校验 + `BCM_FETCH_TRANSPORT_ALLOW` host 白名单 |
| DNS 后拒内网/保留段 | 全量解析→`ipaddress.is_global` 矩阵(含 169.254.169.254) |
| 连到已校验地址 | 连接期 IP pinning(保留 Host 头与 SNI,防 check-then-rebind) |
| 逐跳重定向重校验+跳数上限 | 手动 follow_redirects 循环,每跳走同一套校验 |
| 流式中途强卡字节上限 | 分块累计,不看 Content-Length 谎报 |
| 超时/总预算/并发 | FetchBudget 值对象,W4 下发 |
| sha256 不匹配=拉取失败 | 流式同时哈希,坏 digest 绝不返回字节 |
| 只写或只哈希,绝不执行 | 输出仅 FetchedObject(bytes+digest) |
| 解包守卫 | zip-slip 全家、symlink 逃逸、设备成员、数量/总大小上限 |
| strip 无自动探测 | 恰好剥 N 层;成员层数不足=报错 |
| 权限拍平 | 解包后统一去可执行位 |

## 范围外

凭证存储(W3)、git 源(W7)、任何调用方(编排器属 W4)。

## 执行记录(2026-08-31)

- 组件就位:`fetch/limits.py`(限额单一来源+部署传输白名单 `BCM_FETCH_TRANSPORT_ALLOW`)、`fetch/guarded_fetcher.py`(五层防御)、`fetch/unpack.py`(双格式统一流式解包)。
- 测试:fetcher 安全矩阵 35 条(含 pinned-IP/Host 头钉线、组播显式拒绝、Content-Length 谎报流式卡、digest 不匹配即失败、逐跳 policy);解包矩阵 31 条(traversal 全家、链接类整类拒绝、设备成员、大小/成员上限、strip 精确无探测、权限拍平、拒绝不留半树、双格式同树)。
- 架构门禁:E3 豁免登记(tests/community/framework/flow_coverage.py,含排空条件)。
- 待办:CI 全量 + 批量终审 + 提交。
