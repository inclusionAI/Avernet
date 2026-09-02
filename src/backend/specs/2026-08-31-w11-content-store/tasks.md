# W11 tasks

- [x] S.0 kickoff — spec 三件套、`feat/bot-config-manifest-w11-arc`(源自 origin/dev @ 0d85cafb3)
- [x] S.1 DDL + ORM model + schema.py 登记——`ac_manifest_content` 无唯一键、
      tenant guard、varchar 宽度对着 2048 URL 与 71 字节 digest 的显式理由
- [x] S.2 仓储协议/实现 + 真库测试(add、records_for 按时间序、append-only、
      跨租户不可见)
- [x] S.3 服务矩阵(TDD 先行)——store/read/records;digest 复算守门;原子写+同
      digest 幂等;读校验(同 pass sha256);缺块/坏块吵闹;URL 去 userinfo/query 两
      条;凭证名不落值;分片路径形态
- [x] S.4 settings 解析(默认/覆写/坏类型)+ application.yaml 键 + golden 重生
      (三 profile 各 +1 行 content_store_dir 中性默认)
- [x] S.5 门禁——flow_coverage 条目扩至 cover W11(核对 key 唯一)、模块 README
      (W11 节+Context Boundary provides)、repository README provides 补两行
      (test_repository_contracts 钉的)、boundaries/oversized/user_config AST/
      shipped-config/ruff 全过
- [x] S.6 全量 ci_test.sh 变行覆盖 ≥80% → 批量终审 → 提交 → PR
      - 首轮:CI 16205 passed/行 88.10%/变行 88.24%;批量终审 7 findings(1H+3M+3L)
        全部落回(台账见 spec.md),修后包内测试 215 过、架构门禁 380 过。
      - 期间 dev 前进 4 提交(#1756/#1559/#1745/#1734-W12 docs),零文件交集,
        rebase 后复跑全量:**16261 passed / 59 skipped、行覆盖 88.14%、变行覆盖
        97.84%**(来源:scripts/ci_test.sh --base origin/dev --head HEAD),门禁全绿。
