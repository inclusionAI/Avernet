# 运行目录成功标记补充方案

用户最新确认：运行目录曾完整发布启动成功即可，不再限定当前容器实例。

- 固定标记 `/home/admin/.service_bot_publish_succeeded`；home挂载NAS时允许随运行目录持久化，不绑定容器实例ID，不复用每次清除的 `.starting_done`。
- 普通 service 模式完整启动成功后才写入 `SUCCEEDED`，覆盖主启动出口与 aicoding/claude_code finalize 出口。草稿运行时为 personal，不产生标记。
- 原地 service 模式在参数解析后、系统初始化/配置写入之前检查内容；缺失或内容非 SUCCEEDED 时写当前启动 FAILED 并退出1，提示普通发布/重启。bootstrap reexec 再次检查，成功标记不在启动时清除。
- 普通失败不首次创建标记；已有成功历史保留，原地成功不重新创建标记。该标记是启动生命周期凭据，不是防止容器内用户篡改的鉴权机制。
- 历史运行目录没有新标记时不自动补发；须先完成一次新脚本的普通启动。容器重建后若复用home并保留有效标记，允许原地模式；标记缺失仍拒绝。Backend原有重建流程不改。
- 此处“发布成功”指容器脚本的完整启动成功，不是平台所有副本发布事务最终提交；不因单个容器标记推断整体发布单成功。

测试计划：真实Shell区块测试无标记/错误标记拒绝、有效标记放行、默认普通模式不受影响、失败零标记、两种成功出口写标记、原地保留标记、重入保留标记；随后全量daas测试、bash语法和diff检查。仅改start_service.sh及测试，不部署。

## 实现和验证

- 已实现start_service.sh中的独立标记、早期检查及两个成功出口写入；标记写入失败会输出诊断并返回失败，不静默成功。
- TDD：新增行为测试实现前5 failed/17 passed，实现后新增及既有原地重启套件23 passed。
- daas全量：232 passed/3 failed，27.39秒。3个失败都在既有test_claude_clawmind_guard.py，要求禁止clawmind安装，但HEAD中的install_deps.sh和start_service.sh已启用相关逻辑；关联脚本/测试未被本轮改变，不扩大修改范围。日志：/tmp/service-bot-container-marker-tests.log。
- bash -n、git diff --check、测试文件flake8指定语法/未使用/格式检查通过。
- 此补充方案覆盖001及旧报告中的“无运行目录检查”约束：不新增目录/NAS检查，但按最新用户要求增加容器成功标记门禁。此前Backend测试结论为前轮结果，本轮未改Backend，不重复声称重跑全量。
- 未提交、推送或部署。不从旧/var/run标记自动迁移，/home/admin标记缺失时需一次普通启动。

## 最新路径调整计划

将固定路径移到/home/admin，同步诊断文案和测试名称；补充真实Shell路径声明测试，执行原地重启套件、bash语法与diff检查。上面的全量统计是前轮结果，本轮不重复声称已跑全量。

已完成：路径测试先1 failed/23 passed，再24 passed；bash -n与git diff --check通过。未提交或部署。
