# 轻量可视化编辑器验收记录

日期：2026-10-29。范围：ClawWeb 公共 workflow 前端与现有宿主集成。

## 1. 验证边界

- 使用当前工作区代码，不以离线 HTML Demo 的测试结果代替正式 React 编辑器验收。
- 单元、组件与兼容性测试使用合成定义；没有把生产工作流原文写入测试 fixture。
- Chromium 运行正式构建产物；页面 API 使用拦截响应，未连接 PROD/PRE，未执行真实保存或部署。
- 生产样本统计仅用于字段优先级设计，不等同于生产工作流全量兼容保证。
- 本轮未提交、推送、执行预发流水线或发布；不包含引擎/API/数据库 schema 变更。

## 2. 已执行的验证

| 范围 | 命令/方法 | 实际结果 |
|---|---|---|
| 公共边界及类型 | `npm run ci:check` | 通过 |
| 工作区构建 | `npm run ci:build` | 通过；随后重新构建 workflow 与 clawweb 受影响包也通过 |
| Workflow 全模块 | `npm test --workspace=@avernet/workflow -- --maxWorkers=2` | 163 文件、1,759 测试通过；含前端 74 文件、780 测试 |
| 编辑器定向覆盖率 | core、组件及 Editor 保存测试，V8 覆盖选定编辑器源码 | 21 文件、363 测试通过；行 97.63%、分支 90.47%、函数 87.64% |
| 宿主语义主题 | `npm test --workspace=@avernet/clawweb -- semantic-theme.test.ts` | 1/1 通过 |
| 正式页面浏览器 | Chromium + `public/modules/workflow/e2e/visual_editor_browser.py` | 22/22 通过（离线资源加载；新增真实位移与 MiniMap 断言） |
| 浏览器网络策略 | `python3 -B -S -m unittest discover -s public/modules/workflow/e2e -p test_visual_editor_network.py` | 34/34 通过 |
| Git 空白检查 | `git diff --check` | 通过 |
| 源码体积与占位检查 | 对本轮全部新增/修改源码逐个计数并扫描 | 42 个源码文件，最大 842 行；没有新增 TODO/FIXME、skip/only 测试、未实现分支 |

最终全模块测试使用稳定源码和 `--maxWorkers=2`，1,759/1,759 通过（86.85 秒）。首次沙箱运行因回环监听 `EPERM` 失败并中止；获准允许本机测试端口后重跑。默认并发的一次完整运行中，本机 Git 服务用例因负载临时超时，单独复跑通过，随后限并发全量复跑整包通过。没有削弱门禁或新增跳过规则。覆盖率仅针对本次选定的编辑器 core/组件，不代表完整工作区。

## 3. 浏览器验收内容

1. 从真实工作流工作区载入合成定义，紧凑导航可访问。
2. 节点中文名称/任务文本编辑，JSON 形状的文本仍为字符串。
3. 未映射扩展字段和 outputContract 在显式业务字段编辑后保留。
4. YAML 草稿在切换画布/YAML 时保留，未应用草稿阻止保存。
5. YAML 校验后应用真实 store；非法 JSON 不污染文档。
6. 显式 JSON 参数应用不覆盖 executor 的兄弟字段。
7. 原生拖放新增节点，无伪造脚本/技能/审批人默认值。
8. 移动节点不向执行 spec 写入新坐标。
9. 从真实分支 Handle 拖出连线；`complete: true` 不被误当成禁止连接。
10. 连接撤销与重做；节点删除确认及撤销恢复。
11. 现有 DB→Git 顺序（两者均被拦截）；保存载荷保留隐藏字段。
12. 部署确认显示但不派发请求；1440px 桌面无横向溢出；无未捕获页面异常或非回环流量。

### 复现浏览器测试

在 ClawWeb 根目录构建后直接运行，不需要 Vite preview 或后端：

```bash
npm run build --workspace=@avernet/workflow
npm run build --workspace=@avernet/clawweb
# 依赖 Python Playwright/Chromium 及 PyYAML。
python3 -B public/modules/workflow/e2e/visual_editor_browser.py \
  --base-url http://127.0.0.1:5189 --artifacts /tmp/clawweb-editor-browser
# 网络策略单元测试只依赖 Python 标准库。
python3 -B -S -m unittest discover -s public/modules/workflow/e2e \
  -p test_visual_editor_network.py -q
```

`--base-url` 只提供浏览器页面的虚拟同源地址，不对该地址发起真实 HTTP。Context 级路由只从本地 `dist/web` 返回页面/静态文件，API 为精确 method/path 白名单；未知请求、WebSocket、重定向和非同源访问均阻止，Service Worker 禁用。保存仅在显式测试步骤开放一次 DB→Git 模拟序列，部署请求始终拒绝。

浏览器脚本以非零退出表示失败，输出 `results.json`、测量证据与四张页面截图；`editor-ready.png` 为修改测试文本前的中文示例。它不证明浏览器进程拥有操作系统级网络隔离；保证的是本测试的请求处理不转发到任何真实后端。

## 4. 兼容性与已修正问题

- 隐藏低频配置，不从保存载荷删除；保留 unknown、false、0、null、字符串及旧式分支。
- UI 坐标仅在当前编辑会话中保存；旧 `_x/_y` 输入保留但不更新，刷新后会重新布局。
- 校正 `complete` 语义，不以它强制终止分支；不伪造新的 branch/parallel executor。
- 无 ID 的兼容分支不再使具名分支引用保护失效；删除最后有效的直接/传递来源路径会被拒绝，仍有有效替代路径时允许。
- SPA 顶层导航/后退保留会话内 JSON/YAML 原文与 baseline/owner，返回后能继续应用或放弃；不同会话清除旧稿，明确放弃后草稿不会复活。
- MiniMap 的测量尺寸保留在 UI 状态，不能写入 spec 或历史；加入尺寸回传与浏览器矩形检查。
- YAML 使用独立草稿及过期请求检查；规范化发生字段变化时先确认。
- 其他 JSON 草稿尚未应用时，不能用 YAML/完整配置覆盖它们。
- 保存以请求发出时的文档建立基线，不能把后续修改误标为已保存；跨编辑会话的迟到保存不回写当前文档；部署 dispatch 时再次检查草稿和校验状态。
- 保存完成统一通过 `completeSave(savedSpec, specAtRequest, sessionId)` 合并：请求期间修改的节点、标题或命令配置仍保留为 dirty；保存窗口提交的 ID、标题和命令配置成为当前文档身份；保存重命名后正确切换到新 ID。
- 若保存结果相对请求快照丢失节点或隐藏字段、或请求期间又改了工作流标识，`completeSave` 拒绝并显式提示，不会把结果误标成已保存。

## 5. 限制与未验证项

- 没有运行真实 PROD/PRE 保存、部署、权限/身份服务联调，也未认证读取在线 BaaS 文档。
- 尚未验证全量生产定义往返；当前兼容性测试为合成代表性字段与真实服务端规范化函数的组合。
- 不承诺 YAML 注释/锚点/排版保真；数据库 JSON 已丢失的源码信息无法恢复。
- 完整循环子画布、动态模板设计、阶段折叠、真实技能/工具/人员选择器仍为后续项。
- 前端结构检查不是完整执行器校验；卡片“已填写”不表示该节点一定可执行成功。
- 保存重命名与请求期间继续编辑的并发合并已在本轮修复并覆盖测试；仍建议用户在保存请求完成后再切换工作流，以减少跨会话操作的认知负担。
- 当前 Node 23.11 与部分测试依赖声明的 engines 范围不一致，但本轮已执行的前端检查通过；未为此改依赖。
- Python 网络护栏及脚本的 Ruff、Black、mypy 已通过；该工作区没有本轮新增的 TS ESLint 门禁，未宣称完整 TS lint 已通过。
- 构建存在大于 500kB 的 chunk 提示；已有测试存在 React act、scrollTo/SVG 属性等告警，不等同于失败。没有弱化门禁或修改跳过规则。

## 6. 独立复审结论

作者与审查分开执行，发现问题均经补丁与回归验证后关闭：

| 审查范围 | 最终结论 | 关闭的问题 |
|---|---|---|
| core/store/graph/contracts、节点表单、画布 | APPROVE | 混合匿名分支引用、最后有效分支路径删除、MiniMap 尺寸、complete 语义 |
| Editor/YAML/settings/导航集成 | APPROVE | 放弃后旧稿复活、跨会话迟到保存、SPA 导航丢稿及残留 pending |
| Python 浏览器网络隔离与断言 | APPROVE | Context/SW/WebSocket 缺口、任意本机请求放行、未知 API 假成功、无实际位移仍通过 |

在上述本轮限定范围内，未关闭阻断项为 0；不等于生产认证或所有历史缺陷均已修复。第 5 节明确保留未验证项与既有边界。
