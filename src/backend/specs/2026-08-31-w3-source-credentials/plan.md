# W3 实施计划

1. 建立租户隔离的 `ac_source_credential` ORM/DDL 与 repository Protocol/实现。
2. 在 core 中实现 URL 前缀规范化、授权策略、秘密注入和脱敏业务服务。
3. 复用 `TokenVault` 与 `SecretResolver`，在服务边界增加生产 fail-closed 检查；不新增密码学。
4. 通过 DI 暴露 repository/service，保持 core 不依赖 HTTP；后续路由只做薄适配。
5. 用单元、repository 和 W2 集成式测试覆盖轮换、租户隔离、端口/path 边界、重定向和脱敏。
