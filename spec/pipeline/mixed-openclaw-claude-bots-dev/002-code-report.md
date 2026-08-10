---
agent: tc-code
status: completed
created: 2026-08-08T18:30:00+08:00
---

# 编码报告：混合 Claude Code Bot 可用性修复

## 修复范围

- Provider 生命周期：运行时状态保存 Provider 管理权限和三个 bot 引用；重启/停止只清理这三个受控历史记录，并把新记录标为 `（当前）`。
- CLI 选择：relay 在无副作用 `--version` 探测失败时跳过异常 native CLI，自动选择健康的既有候选；显式路径仍 fail closed。
- 前端协议：人的发送者身份由 session transport 写入 Workbench `bot_id`，不再写入 target-only 的 `bot_uuid`；无 mention 时仍让 BCS 选择群 Driver。
- 前端存活：stdin keeper 与 npm dev server 同处一个 nohup shell，防止脚本返回后 Tailwind/Umi 退出。
- 状态与日志：BCSFuse status 先加载 standalone runtime；页面发送日志改为长度/存在性/计数，避免将完整 params 输出到 DevTools。
- 诊断：新增日志只包含角色、状态、端口、相关性元数据和是否存在身份字段；不输出令牌、凭据或完整聊天内容。

## 本地实现验证

- 混合 Provider bridge 与生命周期契约测试通过。
- 真实页面中，当前 Claude Developer 对一次无副作用消息产生 final 回复。
- 实际 macOS Terminal 中，frontend 重启完成后仍监听 8000。

完整命令和结果见 `003b-regression-report.md`，浏览器验收见 `005-qa-report.md`。

## 迭代 3：首条群消息并发超时（已撤回）

- 根因：普通 Chat 群创建时，BCS 会把 `SessionContext` 以 `chat.send`
  投递给 Driver。对于 Provider-downlink Claude，这会启动一个用户不可见的
  推理；用户紧接着的第一条消息会争用同一 relay session lock，最终在 30 秒后
  返回并发超时。
- 该方案曾仅在普通 Chat、未显式设置 `driver_delivery`、且群内存在
  Provider-downlink Bot 时，将 Driver 初始化改为 `chat.inject`。按当前
  产品语义，该改动已撤回：普通 Chat 始终保留 Driver `chat.send`，显式策略
  与 ManagerWorker 的 Manager `chat.send` 语义保持不变。
- 前端：本地页面的 Bot tab 与新群候选列表只隐藏精确匹配的旧 Claude 三角色名，
  保留带 `（当前）` 的受管卡片；既有群成员不被自动改写。
- 验收：当前基线已恢复普通 Chat 群的默认语义：Driver 收到 `chat.send`，其余
  成员收到 `chat.inject`；不再保留 Provider-downlink 专用的初始化改写。

## 迭代 4：五个 OpenClaw Bot 启动阻塞

- 根因：`start_bcs_bots.sh` 将“TypeScript 源文件比 `dist` 新”误判为运行时
  必须重建，并在启动路径同步执行 `npm install && npm run build`。依赖解析没有
  超时，五个 gateway 尚未启动时，顶层界面只会停在 `Starting 5 local OpenClaw bots`。
- 修复：运行时只验证 `dist/esm/index.js` 是否存在；存在即复用，并在源码较新时
  给出不含依赖 URL、凭据或消息正文的重建提示。缺少产物则快速失败，要求显式
  执行 `singlebox setup bcs` 进行安装和重建。
- 回归：新增 shell 场景以会失败的假 `npm` 验证运行时绝不调用 npm，且仍完成
  plugin link。同步移除已撤回 BCS 行为与 BCS 日志改造的静态 guard，保持 BCS 源码和诊断
  与用户指定的基线一致。
