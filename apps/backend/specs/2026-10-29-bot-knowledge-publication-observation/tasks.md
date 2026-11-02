# Tasks: Unified stage selections

> 用户已确认整体流程并要求开始实现。旧实现完成记录见 Git 历史；以下为本次新增范围。

## Task 1 [x]: Stage storage and best-effort projection
- **Goal:** 有界版本读取、线上来源标记、原生部署完成后的辅助投影。
- **Done when:** 状态隔离、回滚、未知、CAS不变、失败隔离测试通过。
- **Verification:** 12个真实DB状态用例通过；任务回归及管理/会话相关581个用例通过，3个新HTTP用例修正外部Port绑定后单独通过。

## Task 2 [~]: Existing management and conversation HTTP contract
- **Goal:** 复用两个现有URL，删除独立发布读取URL，保留authorization和个人Bot兼容。
- **Done when:** HTTP/真实DI/会话与管理边界测试通过。

## Task 3 [ ]: Frontend stage propagation and readonly states
- **Goal:** 现有管理和会话界面接入stage-aware选择。
- **Done when:** 预发/线上只读、未知提示、会话懒加载和分页测试通过。

## Task 4 [ ]: Verification and independent review
- **Goal:** 更新文档、运行门禁、独立评审并修复。
- **Done when:** 记录实际测试结果和未验证范围，不复用旧绿灯。

## Groups
- Group A: Tasks 1, 2
- Group B: Task 3
- Group C: Task 4
