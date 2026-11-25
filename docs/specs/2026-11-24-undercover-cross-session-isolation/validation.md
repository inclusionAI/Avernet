# 基线验证：同群连续会话、换座和换人

关联 [#2609](https://github.com/inclusionAI/Avernet/issues/2609)。结论用于 [RFC](rfc.md)和[威胁模型](threat-model.md)。

## 环境与判定

基线为 `oss-camp2026` 的 `86252c1e98d812f18166f930d9f7c2ceafd06f46`。在 WSL Ubuntu 中，用该提交构建的原版 BCS 启动独立的回环监听和数据目录。使用本地认证 mock 令 A、B、C 分别调用 `/me` 并取得三个不同的服务端 actor。主持人使用临时注册的合成 Bot。这里测试的是实际 BCS HTTP 认证映射与授权逻辑，浏览器真实登录、Workbench 网关和多人整局另列为后续验收。

同一个 manager-worker 协作群内先创建 J1，再创建 J2。两个会话都以 `seat_1` 和 `seat_2` 作为词条名，写入四个互不相同的合成标记。C 在 J1 缺席，B 在 J2 缺席。创建会话时群员自动进入名册，本次通过成员状态接口显式设置缺席并读回确认。座位是测试夹具，非现行单 Human profile 的产物。

| 会话 | 座 1 | 座 2 | 已设缺席 |
| --- | --- | --- | --- |
| J1 | A | B | C |
| J2 | C | A | B |

## HTTP 实测

### Group Context

用主持人 Bot 为下列五个条目逐条写入 `session` scope 和单一读取者 ACL。以 A、B、C 分别直接取词，共 15 次。5 次授权返回且命中对应标记，10 次非所有者请求返回 403。调换座位以后 A 仍可读自己在 J1 的旧词，J2 则按当前 ACL 返回新的座 2 词。

| 读取目标 | A | B | C |
| --- | --- | --- | --- |
| J1 / `seat_1` | 200，J1 座 1 词 | 403 | 403 |
| J1 / `seat_2` | 403 | 200，J1 座 2 词 | 403 |
| J1 / `legacy_entry` | 200，J1 额外标记 | 403 | 403 |
| J2 / `seat_1` | 403 | 403 | 200，J2 座 1 词 |
| J2 / `seat_2` | 200，J2 座 2 词 | 403 | 403 |

会话级 list 的 6 次调用均返回 200，且只枚举该 actor 能读的词条名。

| 会话 | A | B | C |
| --- | --- | --- | --- |
| J1 | `seat_1`、`legacy_entry` | `seat_2` | 空 |
| J2 | `seat_2` | 空 | `seat_1` |

A 在 J2 查询仅在 J1 写入的 `legacy_entry`，响应为 200 和 `{"items":[]}`。该探针证明了显式 session scope 下的会话隔离。BCS 根路径 `POST /groupcontext/get` 能执行上述授权请求。对本地 BCS 请求 `POST /api/v1/collaboration/groupcontext/get`，三个 actor 均收到 404。已核对[副屏契约](../../../apps/bcs/assets/panel/UNDERCOVER_GAME.md)和 [BCS 路由](../../../apps/bcs/crates/adapters/http/bcs-http/src/router.rs)。部署后的 Workbench 网关能否复用根路径仍待验证。

### HumanInput

将最小工作流提交到 `POST /sessions/{sessionId}/state-machine-runs`。绑定与并发分别测试，以免两条定义检查互相遮蔽。

| 探针 | 实际结果 |
| --- | --- |
| 有序前端 HumanInput，添加 `runtime_actor` assignee，保持无 IM 通知 | 400，定义校验拒绝前端 HumanInput 的 assignee |
| 同一入口扇出到两个无绑定的前端 HumanInput，再汇聚到同一终点 | 400，定义校验要求 HumanInput 之间存在明确的依赖顺序 |
| J1 单个未绑定前端 HumanInput | 运行启动 202。A、B 均在 pending 中看到 `input1`，缺席 C 的查询和提交均返回 403 |
| J2 单个未绑定前端 HumanInput | 运行启动 202。A、C 均在 pending 中看到 `input1`，缺席 B 的查询和提交均返回 403 |
| 夹具将 J1、J2 中的 `input1` 都视为 A 的动作，同局另一个在场人先提交 | B 在 J1、C 在 J2 均提交成功（200），响应的 `responded_by` 分别是 B、C，随后 A 提交均返回 409 |

现有接口能区分当局在场与缺席。未绑定节点接受任一有资格的在场 Human 提交，夹具中预期的座位归属无法约束服务端。相应定义规则见 [BCS 校验器](../../../apps/bcs/crates/services/bcs-collaboration-runtime/src/definition.rs)。

## 复现步骤

1. 在 Linux 或 macOS 的本地独立 BCS 测试实例上运行。使用项目 E2E fixture 的 `BCS_AUTH_MOCK=1` 方式，分别用三个本地 `X-Mock-User-Id` 请求 `GET /me`，记录响应的 `actor_uuid`，再各自 `POST /me/ensure-human`。[BCS E2E 初始化](../../../apps/bcs/crates/bootstrap/bcs/tests/helpers/mod.rs)
2. 以 A 调用 `POST /bots/connect`（`protocol_version: 3`），以签发的临时 Bot 凭证调用 `POST /bots/onboard`。以 A 调用 `POST /groups` 创建 `group_strategy: manager_worker` 的群，主持人为 `driver_bot` 和 manager，A/B/C 为成员，`create_initial_session: false`。在同一群依次调用 `POST /groups/{groupId}/sessions`，创建两个 `kind: chat` 会话。以各自认证身份 `PATCH /sessions/{sessionId}/members/{actorId}` 设置 `mode: present` 或 `absent`，再读会话确认表中的在场名单。
3. 对每个词条由合成 Bot 调用 `POST /groupcontext/add?groupId=...&sessionId=...`。下面展示 J1 的 A 词条。为其他条目替换会话、名称、标记和目标 actor。

   ```json
   {
     "name": "seat_1",
     "scope_level": "session",
     "content": "synthetic-j1-seat-1",
     "visible_to": {"userIds": ["<actor-A>"]},
     "collect_from": {"userIds": ["<referee-bot>"]}
   }
   ```

4. 以 A、B、C 分别 `POST /groupcontext/get` 和 `POST /groupcontext/list`，查询串传会话和群 ID。get 请求体为 `{"name":"seat_1","scope_levels":["session"]}`，list 请求体省略 `name`。检查 get 的 `items` 和 list 的 `contexts`，再将 J2 的读请求换成副屏路径前缀比对路由。
5. 每次以 Bot 启动状态机时，请求使用 `definition_yaml`、`input: {}` 及 `participant_bindings`，其中 referee 绑定为 `{"source":"manual","bot_ids":["<referee-bot>"]}`。下面是可运行的未绑定 HumanInput 夹具。

   ```yaml
   name: Synthetic HumanInput scope probe
   participants:
     referee: {required: false}
   runtime:
     kind: state_machine
     state_machine:
       version: 1
       graph_mode: acyclic
       nodes:
         input1:
           kind: human_input
           display_name: Synthetic prompt
           instruction: Synthetic prompt
           node_timeout_ms: 900000
   ```

   在两局分别启动后，以三个 actor 调用 `GET /state-machine-runs/{runId}/pending-human-nodes`，再以缺席者、A 之外的在场者、A 的顺序调用 `POST /state-machine-runs/{runId}/nodes/input1/respond`，提交合成 `content` 并检查结果。

   定义反例可从该夹具派生。绑定反例在 `input1` 添加 `assignee: {type: runtime_actor, actor: <actor-A>}`。并发反例使用一个入口 Bot 节点，把 `complete.targets` 设成 `[input1, input2]`，两个未绑定 HumanInput 各依赖入口并指向同一个 `final_output: true` 的 Bot 节点。Bot 节点使用 `assignee: {type: bot_binding, binding: referee}`。两种定义都核对错误来自预期校验。

## 自动复核换局词条权限

按复现步骤建立同一群内的两个会话。四个席位条目的内容分别使用 `synthetic-j1-seat-1`、`synthetic-j1-seat-2`、`synthetic-j2-seat-1`、`synthetic-j2-seat-2`，额外条目 `legacy_entry` 使用 `synthetic-j1-legacy`。以下脚本接收本地 BCS 地址、群及会话 ID，以及三个测试身份的 `X-Mock-User-Id` 原值。

```bash
python3 - "$BCS_URL" "$GROUP_ID" "$J1_SESSION" "$J2_SESSION" \
    "$A_MOCK_ID" "$B_MOCK_ID" "$C_MOCK_ID" <<'PY'
import json
import sys
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

base, group, j1, j2, a, b, c = sys.argv[1:]
url = urlsplit(base)
assert url.scheme == "http" and url.hostname in {"127.0.0.1", "::1"}
entries = (
    (j1, "seat_1", "synthetic-j1-seat-1", a),
    (j1, "seat_2", "synthetic-j1-seat-2", b),
    (j1, "legacy_entry", "synthetic-j1-legacy", a),
    (j2, "seat_1", "synthetic-j2-seat-1", c),
    (j2, "seat_2", "synthetic-j2-seat-2", a),
)
markers = {entry[2] for entry in entries}

def read(session, name, user):
    query = urlencode({"groupId": group, "sessionId": session})
    request = Request(
        f"{base.rstrip('/')}/groupcontext/get?{query}",
        data=json.dumps({"name": name, "scope_levels": ["session"]}).encode(),
        headers={"Content-Type": "application/json", "X-Mock-User-Id": user},
    )
    try:
        with urlopen(request, timeout=10) as response:
            return response.status, json.load(response)
    except HTTPError as response:
        return response.code, json.load(response)

allowed = denied = 0
for session, name, marker, owner in entries:
    for user in (a, b, c):
        status, body = read(session, name, user)
        if user == owner:
            assert status == 200 and [item["content"] for item in body["items"]] == [marker]
            allowed += 1
        else:
            assert status == 403 and all(value not in json.dumps(body) for value in markers)
            denied += 1

assert read(j2, "legacy_entry", a) == (200, {"items": []})
print(f"{allowed} 认证玩家本人读取成功，{denied} 次越权读取被拒，旧词条未回退到新局")
PY
```

复核脚本已在原版 BCS 上运行。正常夹具得到 5 次本人成功、10 次拒绝；随后由主持人在独立测试会话中临时把 J2 的 `seat_2` 读取范围改为 A 和旧玩家 B，脚本按预期失败。撤销 B 的授权后，复用同一脚本再次通过。此反例覆盖的是跨局词条授权。公开副屏参数注入和完整信道扫描仍列入多人实现验收。

## 现有基线与待验收

- 在 WSL Ubuntu 按 profile 的 README 运行 referee 的 `unittest discover`，51 项通过，含 `flow_baseline.json` 快照断言。直接以 Windows 默认编码运行会遇到 GBK 文件读取和 POSIX 路径假设，已在需求指定的 Linux 环境重跑。
- 当前 HTTP 证据覆盖 BCS 的会话与 ACL 语义、未绑定节点行为和定义校验。真实双浏览器对局、Workbench 网关验收、有意注入泄露的完整隔离测试、真实复盘、视频和 AI 使用报告仍属于 [RFC 的实现验收](rfc.md#合入与验收)。

## 2026-11-25 验收复测

启动同一提交的独立 BCS 实例，注册模拟 Human 身份和临时 Bot，按配置在 BCS 会话中核对在场人数与 worker 人数。运行原版 referee 的 `undercover.py begin`，通过实际 `bcs-cli session get` 读取名单。对多人配置另调用 `init`，传入同一会话、主持人和相应玩家 Bot 列表，验证发牌前会重新校验名单。

| 配置 | 开局入口 | 发牌入口 |
| --- | --- | --- |
| 1 Human + 5 玩家 Bot | `begin` 退出码 0，生成初始化命令 | 由单人流程快照覆盖 |
| 2 Human + 3 玩家 Bot | `begin` 退出码 2，`ROSTER_MISMATCH` | `init` 退出码 2，`ROSTER_MISMATCH`，未生成游戏状态 |
| 3 Human + 3 玩家 Bot | `begin` 退出码 2，`ROSTER_MISMATCH` | `init` 退出码 2，`ROSTER_MISMATCH`，未生成游戏状态 |

两个多人配置的错误正文均为「本游戏只支持一名人类玩家。停止，请核对会话成员。」阻断点是当前 profile 的 `session_roster`。因此两种配置在发牌前结束，未形成完整对局。[主持人脚本](../../../singlebox/agents/6bots_undercover_game_profile/referee/skills/undercover-game-referee/scripts/undercover.py)

- Referee `unittest discover` 在 WSL Ubuntu 重跑，51 项通过，包含单人流程快照。
- 以相同提交的副屏代码和共享 BCS 测试夹具运行 `npm run verify`，类型检查、构建、UMD 契约、组件、集成、视觉回归和公开资产扫描均通过。

### 名单校验改动试验

用合成会话名单在内存中比较现有实现、仅删除人数判断和多人名单原型。原型仅验证人数与名单，发牌继续受安全能力约束。

- 仅删除人数判断时，`begin` 在 2H+3 Bot 和 3H+3 Bot 中依旧只给首位 Human 生成 `--human` 参数。
- 原型读取完整名单，接受 2H+3 Bot 和 3H+3 Bot 的人数校验，分别拒绝 5 Human 和总数 9 人。两种有效多人配置均未生成发牌命令或落盘游戏状态。
- 原型的 1H+5 Bot 初始化命令与现行实现一致。此试验未部署多人开局和发牌实现。

### 正式入口的名单校验

同一测试方法复测已接入的名单校验。原版 BCS 使用本地模拟认证，`begin` 和 `init` 均通过真实 CLI 查询会话成员。

| 配置 | `begin` | `init` |
| --- | --- | --- |
| 1 Human + 5 玩家 Bot | 生成原有单人初始化命令 | 由单人流程快照覆盖 |
| 2 Human + 3 玩家 Bot | `roster_valid=true`，2 Human、3 Bot | 同样确认名单，发牌前返回 `MULTI_HUMAN_CAPABILITY_REQUIRED` |
| 3 Human + 3 玩家 Bot | `roster_valid=true`，3 Human、3 Bot | 同样确认名单，发牌前返回 `MULTI_HUMAN_CAPABILITY_REQUIRED` |

两种多人配置都未生成发牌命令或游戏状态。此次验收覆盖名单检查和发牌保护，真实多人整局仍依赖私有发词和绑定 HumanInput 的实现。Referee 53 项测试通过，含缺席成员、人数边界及单人流程快照。

### 私有写入原型复测

实现分支通过现有 BCS CLI 写入并以主持人身份回读，核对会话级作用域、唯一玩家的读取权限、词条所有者和内容。原版 BCS 上四条词写入并重试成功，4 次本人读取命中，8 次其他 Human 和 4 次其他 Bot 读取被拒。尝试以同名条目写入不同词时，原型拒绝且原词条内容仍可被本人读取。Referee 56 项测试通过，其中新增测试覆盖写入响应丢失、授权冲突和写入无法确认。

本节结果来自独立的本地实现验证。开局接入、Workbench 私有读词和两种完整多人对局仍待验收。
