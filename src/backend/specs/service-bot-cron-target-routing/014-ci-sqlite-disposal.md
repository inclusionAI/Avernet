# PR #2587 Backend单测段错误修复

日期：2026-10-19。失败head5dcc8c0449，job118064005031/run39253793224。

## 证据与计划

远端22568 passed、12 skipped、1 worker crash。实际SIGSEGV当前线程在TaskHarness→TaskGraphRepository→SQLAlchemy do_execute，主线程同时在framework/fixtures.py:126 engine.dispose→do_close；同SQLite StaticPool连接跨线程关闭与查询重叠。现有SqliteDB.session/orm_session/reset_for_tests已使用_session_lock，fixture直接dispose未持锁。

最小修复限定测试基础设施：fixture关闭其捕获的engine时复用同一session锁，不改Cron业务、不跳过用例、不提高worker重启次数或降低门禁。先用受控并发回归复现fixture在session结束前尝试close，再加锁验证等待查询结束后再close；运行framework/endpoint/数据库相关及完整backend CI，远端当前head状态单独观察。背景线程生命周期更广问题不在本补丁扩张整改。

## 复现与局部验证

新增受控并发测试复用真实fixture/SqliteDB会话：查询持锁期间启动fixture teardown，记录dispose尝试；旧实现稳定失败，且用观测钩子等待查询结束后才实际关闭，避免测试自身触发segfault。修复后framework/数据库/重置契约155测试通过，ruff/diff-check通过。生产代码不改；fixture添加DEBUG等待/完成诊断。完整backend与远端CI待新head验证。
