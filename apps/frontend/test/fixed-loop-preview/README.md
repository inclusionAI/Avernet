# Fixed Loop Definition preview

该 fixture 直接挂载生产 `CollaborationFlowPreview` 和 validation notices，复用
BCS 的 `tests/fixtures/fixed_loop_logical_view.json` 两节点循环体，以及
`fixed_loop_preview.json` 校验响应。Loop 描述来自服务端 `loops` 字段，
轮次与逻辑节点来自 execution metadata，不从执行 ID 猜测轮次。

在 `src/frontend` 下运行（使用已安装的开发依赖）：

```bash
npm test -- --runInBand src/pages/GroupChat
./node_modules/.bin/vite --config test/fixed-loop-preview/vite.config.mjs
```

打开 `http://127.0.0.1:4181/`，检查：

- Fixed Loop：显示一份“编写→评审”的循环体，外框标记 Loop；回边为 `continue`，两个出口只标 `approved` / `exhausted`。
- `approved` 连接独立角色 `polisher` 的润色节点，`exhausted` 连接写作者的重写节点，两条分支随后汇总。
- 定义预览没有执行历史，仅显示逻辑结构；没有“展开执行图”或节点次数标记。运行后的副屏可展开实际执行记录。
- 默认没有“最多 3 轮”或“2/3”进度式标记；展开“循环限制”后可查看 `max_iterations`。
- 点击或用键盘选择循环体节点后，底部显示该逻辑节点首轮的预览 ID。
- 此场景模拟已开放执行并配置 Judge 的服务；创建按钮可用。validation notices 的警告和禁用逻辑由组件测试覆盖。
- 切到普通 v1：没有空 Loop 信息或执行限制提示，普通节点仍可选择。
- 390px 窄屏：页面没有横向溢出，所有出口在画布内。
- 选择“自定义连线名称”（或访问 `/?names=1`）：回边显示“根据意见修订”，出口显示“评审通过”/“转入重写”，普通边也显示配置的名称；悬浮提示保留原始 outcome。

页面中的创建按钮只用于展示校验结果，没有创建请求。它不替代完整工作台或
真实 BCS 执行验收。资源配置上限不在 preview 响应中单独提供；界面展示定义的
`max_iterations`，并原样显示服务端资源校验错误、path 和 hint。

可单独检查生产组件的构建：

```bash
./node_modules/.bin/vite build --config test/fixed-loop-preview/vite.config.mjs
```

产物写入 `node_modules/.cache/fixed-loop-preview`，不进入应用入口或版本控制。
