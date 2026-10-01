---
agent: tc-code
status: completed
created: 2026-09-14
iteration: 4
---
# 编码报告：任意认证应用访问已有 Caller 实例

## 最新需求和边界
用户明确批准：所有通过Principal校验且带app_id的应用均能操作同tenant已有Caller实例，允许跨用户、私有Bot、非成员Caller。无需app grant或用户owner/public/collaborator权限。新增路由仍保留Principal、tenant、Bot存在、有效既有实例限制；不赋管理员、不首次创建。

Worktree: /Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/feat-app-caller-connection-rel20260915
Branch: feat/app-caller-connection-rel20260915

## 本轮改动
- instance service删除本任务新增的app grant和当前caller权限gate，移除已无用途的grant/collaborator构造参数及import。
- 保留tenant mismatch在任何仓储访问之前拒绝，Bot不存在拒绝，复用get_authorized_caller_connection(operator_id=user_id,is_super_admin=False)的既有bot_uuid实例限制及原生命周期。
- owning Protocol、README、router docstring及001 spec/plan明确跨用户权限范围。原用户接口、其他app grant功能、Gateway/BaaS/Engine和schema未改。
- 服务测试证明两个不同app均可访问无grant、private非成员caller实例；Bot/tenant/实例拒绝无副作用。删除已不适用的本任务grant集成测试，不修改原grant功能测试。
- 框架真实DI/仓储成功用例改为无grant种子、private非owner Caller；ASGI实签增加第二个app身份。
- live acceptance更新为：无grant且缺实例403；已有实例进入原未发布错误语义；admin provision私有Bot非owner Caller后，两个APP-only无grant复用同实例/UUID，断言success/need_poll/connection及请求无Cookie/x-user-id。保留manifest新路由及原覆盖率阈值。

## 验证
- TDD红灯：两个任意app无grant成功断言在旧gate上失败；删除gate后通过。
- focused/API/framework/原grant回归及architecture compliance/conformance/module boundary/endpoint coverage gate：**318 passed**（18个存量Pydantic/Starlette warnings）。日志 /tmp/app-caller-policy-final.log。
- 验收collection：16 tests collected，日志 /tmp/app-caller-policy-collection.log。未本地拉完整live栈，远端Singlebox尚待复跑，不能沿用上一策略的CI状态。
- 修改源文件及API/live/framework测试Ruff F/E9 PASS；instance服务旧测试文件有16个原有F841，按HEAD与当前内容分别ruff比较，数量及code/message完全相同，无新增lint问题。
- manifest checker PASS；git diff --check PASS。

## 安全日志
保留authentication_request/request/success/denied/failed和application_authorized事件、system/direction/operation/method/route/request_id、可信tenant/app_id及目标参数、status/error_code/duration。递归凭据/URL/binary脱敏不变；不输出Principal/原异常凭据。此次删除权限gate不引入新外部边界。

## 后续
创建本地commit交主agent；不push。独立review、全量Backend及远端Singlebox/PR门禁需按最新head验证。未改003/008/009报告。
