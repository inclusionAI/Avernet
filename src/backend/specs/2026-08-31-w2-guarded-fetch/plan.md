# W2 plan — 拆解与顺序

> 源:执行计划 Phase B;单人;每组件测试先行(RED→GREEN);CI 全绿后提交;提交后再跑一次
> `ci_test.sh` 取真实变行覆盖(未提交的新文件不进 gate 的 diff——先提交后复跑)。

1. **骨架** — 模块 README(Context Boundary:W2=传输安全边界)、`fetch/limits.py`(§5 限额单一
   来源、部署白名单、Resolver seam 类型)。部署白名单走 application.yaml
   (`user_config.bot_config_manifest.fetch_transport_allowlist`),由组合根解析注入——
   初版实现用了环境变量,PR 评审(totalfrank)按仓库规则(AGENTS.md:裸环境访问只属于
   配置装载/组合根)打回,已改造。
2. **fetcher(TDD)** — 安全矩阵测试(35 条)先行;`guarded_fetcher.py` 五层防御:URL 形状 →
   全量解析+公网判定(含显式组播) → pinned 连接(Host/SNI 保留) → 逐跳重定向重校验 →
   流式字节上限+同时哈希;digest 不匹配=失败。Protocol 声明 `CredentialInjector`/
   `AuthorizationPolicy`(W3 绑定)。
3. **unpack(TDD)** — 矩阵 31 条先行;双格式统一路径:名称规则(遍历/绝对/盘符/反斜杠)→
   成员枚举(链接类整类拒绝、设备拒绝)→ strip 精确(文件层数不足=拒;目录壳归零=根)→
   逐字节流式写入计数 → 权限拍平(0644/0755)→ 拒绝清场(不留半树)。
4. **门禁** — E3 模块豁免(flow_coverage,带排空条件);DCL/oversized/boundaries 均
   原生通过。
5. **验证交付** — 全量 CI;批量终审意见落回;提交(单一 feat commit)。
