# 桌面聊天连接方案（待评审）

配套 [迁移清单](./README.md) 和 [源码证据](./evidence.md)。用户确定的目标是默认 localhost、本期预留 relay 配置、不提供模式设置 UI、不改桌面 App；下文接口字段与配置落点均为设计提案，尚未实施。

## 1. 链路分工

| 操作 | 本期路径 | 说明 |
| --- | --- | --- |
| 设备、创建、授权、进度、重启、删除、打开目录 | 新 Web → Backend OpenAPI → 既有 Service → BaaS → App/容器 | 复用已有业务与桌面协议，补缺少的 OpenAPI adapter |
| 会话管理、历史、收藏、模型、资源、配置和能力管理 | 新 Web → Backend OpenAPI → 对应服务/设备路由 → BaaS/运行时 | 各接口真实下游链按实现核验；涉及本地文件或引擎执行的结果须真机读回。不能把服务器的 localhost 当用户设备 |
| 连接发现 | 新 Web → Bot connection OpenAPI → owner/权限/绑定校验 → BaaS WS info | 解析当前 Bot 的真实机器、容器、端口和引擎路径 |
| 聊天、流式事件、停止和重连 | 新 Web → `ws://localhost:{实际映射端口}/{实际引擎路径}` → 本地容器 | 默认 direct，沿用旧 Web 本地通信能力；不硬编码容器端口、不替换为网页部署 host |
| 预留 relay | 同一连接发现 API → relay 模式的 WS info → 既有中继链 | 显式配置才启用，产品页面本期不暴露开关；是否能在当前环境工作需验收 |
| 唤起客户端 | 既有 `teamclaw://open` | 客户端协议操作，不伪装成 HTTP API；保留旧无设备/离线引导 |

这里将“localhost 聊天”落实到聊天 WS 数据面；会话管理等业务 HTTP 按用户要求走 OpenAPI。若真机发现个别旧对话操作只能由浏览器直接 HTTP 访问容器，应先补齐 Backend/BaaS 对应能力或提请确认，不能偷偷回退旧业务接口。

## 2. 已有基础和必须补的地方

1. BaaS 已有 direct/relay 实现。direct 实时向管理 daemon 查询映射端口，返回 localhost WS；relay 创建中继会话并调用现有 open_ws_relay。可复用，不列为从零开发 App 协议。
2. 当前 runtime gate 只允许 personal/service；普通 connection、sessions、models 等 desktop 请求被拒。需要为桌面运行态定义准确的准入/权限/设备解析，不能只改允许类型集合。
3. 当前 OpenAPI connection 固定请求 relay，并有 gateway 地址组装逻辑。应增加仅 desktop 生效的模式分支，保留现有云端行为。
4. LocalDeviceService 的通用连接信息会合并 HTTP info，且其 path 入参并不用于产出完整 WS URL。需通过合适的 Service/Protocol 获取并保持纯 WS 信息，不把 HTTP URL/token 误作为 WS 地址/凭证；禁止直接从 OpenAPI Router 调用 legacy handler。
5. N 的 BotChatProvider 实际直接使用 connection 返回的 socket.url；相关“重建 host”注释不是当前实现证据。它在初始化、发送前、自动重连刷新时统一获取 IAM token，这与旧 desktop 不使用云端 Caller 换签的分支不同，需按模式/契约分流。
6. N 另一条 support/private 连接工具已有 local/desktop target 组装，但不能据此认为 Bot 单聊已经支持桌面。尽量复用可靠的协议处理，仍需让 Bot 单聊消费正式新合同。

## 3. 建议的合同与配置

| 合同点 | 建议 | 兼容约束 |
| --- | --- | --- |
| 配置落点 | Backend 配置加载/组合根增加 desktop 默认 WS 模式，取值 `direct`/`relay`，默认 `direct` | 名称在 Spec 中冻结；不修改 BaaS 全局默认影响旧调用；不在核心随处读取环境变量 |
| 后续 Bot 设置 | 当前模式解析保留可扩展位置；未来 Bot 级设置优先于部署默认 | 本期无需新增产品 UI，也不提前引入无需求的 Bot 数据库字段；后续设置字段另做合同评审 |
| 连接响应 | 保留 engine/expires_at/sockets 基本形状；桌面需要明确有效模式及凭证策略，可通过兼容的附加字段或专门桌面合同表达 | 字段名/是否 required 须经 schema 和客户端兼容检查；不改变现有云端消费者语义 |
| WS 地址 | 优先采用可信设备服务返回的完整 WS 信息；direct 使用真实 loopback 端口和对应引擎路由 | OpenClaw/Hermes 分别验收；不复制固定 20003，不用 HTTP base URL 拼错 WS，不允许客户端任意指定机器地址 |
| 凭证 | 保留 OpenAPI 登录/委托鉴权；WS 握手与逐消息 Caller 身份分别定义 | BaaS direct 源码返回空 token 不等于取消所有权限校验；local 不强制云端 IAM；relay 凭证按真实协议验收，不凭空断言需或不需 IAM |
| stage | 桌面使用自身运行绑定，拟按 draft 语义支持；不误进入 service online/verify 发布链 | 对已有 desktop 调用检查默认值；禁止无意义的 stage 穿透；云端语义不变 |
| 错误 | 明确设备离线、映射信息不可用、权限拒绝、浏览器连接失败与 relay 不可用 | 不用假地址/假成功；建议不做 local 失败自动切 relay，避免在未选中继时改变路径 |

配置切换只影响 desktop WS 的连接发现/数据面，不改变管理 API、Bot ID、会话 ID、数据目录或旧端默认模式。浏览器无法访问所选机器的 localhost 时应提示本地连接失败及既有唤起入口；不能声称已经可靠识别“浏览器与机器同机”，该判断本身需证据。

## 4. 工作分解和验收

| 顺序 | 后端 | 新前端 | 真实验证 |
| --- | --- | --- | --- |
| C1 合同冻结 | desktop 准入、绑定、模式与凭证合同；核对 LocalDeviceService/WS info 边界 | DTO、desktop 类型、stage 与 provider 接口 | 确认现有 App/BaaS/OC/Hermes 版本，不修改 App |
| C2 默认本地闭环 | OpenAPI connection 返回真实 direct WS；sessions/models/history 使用正确 BaaS 路由 | local provider 分支、模型/会话、停止、断线重连；避免统一 IAM 阻断 | 两引擎真实发送、流式、历史、模型切换、停止；新旧互见同一历史 |
| C3 预留 relay | 模式配置及 relay 分支复用；完整 URL/引擎路径/凭证传递 | 消费有效模式，不加产品设置 UI | 显式 relay 配置可用时做真实测试；不可用则登记部署阻塞，不当完成 |
| C4 兼容与失败 | 旧 Router、云端 personal/service、owner/委托/绑定隔离回归 | 切 Bot/会话时取消过期请求；重启后重新发现端口；错误提示及资源释放 | 容器重启端口变化、App 离线、过期凭证、浏览器 HTTPS 页面连接 localhost、错误引擎路由、权限撤销 |

自动化契约测试用于防回归；最终本地和 relay 可用性依赖真实设备与真实网络结果。若 relay 需要当前 App 没有的能力，本期记录为预留配置的启用前置条件并请用户确认，不将 App 开发偷偷加入任务。
