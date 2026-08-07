> **历史归档 / 非实现基线**：本文记录早期讨论方案，可能包含已废弃的 `role`、`edge_id` 下发、`permission_profiles[]` / `rules_grants[]` 拆分、`extra_rules` 等设计。实现请以 `docs/superpowers/specs/2026-08-07-bcs-a2a-authz-final-design.md` 和 `docs/superpowers/specs/2026-08-07-bcs-a2a-authz-implementation-spec.md` 为准。

# BCS 边权限方案设计 — 详细 spec

> 状态: 评审中
> 日期: 2026-07-16
> 配套: `2026-07-16-bcs-edge-permission-design.html`(mentor 07-14 讨论稿 + 四点修正,可视化交互版) / `2026-07-16-bcs-edge-permission-briefing.md`(汇报)
> 上游: 07-14 TeamClaw 鉴权 briefing(第一层) + mentor 07-14 边权限讨论稿(点-边图雏形,本稿在其上做四点方向定调修正)

> **术语(全篇锁死)**
> - **caller**: 让 bot 做事的那一方(发起者)。
> - **bot**: 干活方(收请求、跑 LLM、返回 tool call 的那一方)。
> - **owner**: bot 的归属者(配角色、绑边的人)。
> - **边 `A → B`**: 读作 **"B 以这条边上的角色运作、为 A 做事"**。方向是"who 让 B 做";边上的角色描述的是 **B 的姿态**(B 用什么权限集干活),不是 A 的管理身份。
> - **role(角色)**: 一组 `(tool, specifier, allow/deny)` 权限集合的"友好标识符"。角色定义单独存(`bcs_role_defs`),边上挂 `role_name` 只是引用。具名示例: `被owner` / `Teacher` / `只读者` / `受限协作者`。
> - **permission 三元组**: `(tool, specifier, decision)`。decision 在本第一层只取二元 `allow/deny`(`ask` 归第二层 bot 自身鉴权)。

---

## 0. 背景与四点方向定调

07-14 briefing 把鉴权从 BCS 下沉到 bot 端、锚在 `before_tool_call`,产二元 verdict 喂第二层 bot 自身鉴权(两层 AND)。mentor 07-14 讨论稿在此基础上把权限画成 **点-边图**:点只有 human/bot,边 = 角色。本文在 mentor 稿上做四点方向定调修正:

1. **边方向定调**:`A → B` 始终是"B 以边上的角色运作、为 A 做事"。owner 这条"A 管 B"语义的关系逆转为 `被owner` 角色(B 被某人拥有、B 用全权运作),与 Teacher/只读者同构:一律是"B 以这套权限集运作"。**角色是 B 的姿态,不是 A 的管理身份。**
2. **env 键排版**:边键写 `(from A → to B, env)`,与图 `A --> B` 一致。
3. **role_name 引用、不重复存 rules**:角色定义单独存;边上挂 `role_name`(引用) + 若干 extra_rules。求值 = `[角色默认 rules] ∪ [extra_rules]`,冲突时 extra 覆盖。
4. **一时刻只走一个通道,删同源合并**:边一时刻只走一个"通道"——named_role / role_with_extra / custom。换通道 = 整体替换,**不是多权限集合并**。唯一叠加是 role_with_extra "角色+附带"(通道内部,extra 覆盖 role 默认)。mentor 稿"同 grantor 匿名边自动合并"经讨论判定为错(等于让 B 一直持有所有被授过的权限合集运作),已删。

---

## 1. 点-边图:点只有 human/bot

权限关系画成有向图(沿用 mentor §1):

- **点只有两种**: `human`(`human_{staff_no}`)、`bot`(bot uuid)。没有第三种点。对齐 `bcs-domain/src/actor.rs` 的 `ActorKind{Bot,Human}`。
- **有向边 = 一个角色 = 一组 `(tool, specifier, allow/deny)` 权限集合**。角色不是独立节点,就是边本身。
- **边 `A → B` 的语义**:"B 以这条边上的角色运作、为 A 做事"。角色 = B 的姿态。
- **env 是边键**:`(from A → to B, env)` 唯一,按环境隔离。
- **同一对 (A,B) 一时刻一条边、一个通道**:某角色 / 角色+附带 / 自定义集。换通道 = 整体替换(非合并)。

```
human_alice --> bot2   "被owner" 边  (B 以全权运作为 Alice 做)
human_bob   --> bot2   "只读者" 边  (B 以只读权限集运作)
bot2        --> bot3   "只读者" 边  (bot3 以只读权限集运作为 bot2 做)
```

---

## 2. 角色 = 一组 `(tool, specifier, allow/deny)` 三元组

decision 三态 `allow/ask/deny`,但 **本第一层(角色边)只用二元 `allow/deny`**(贴 briefing 第一层无 ask;ask 归第二层 bot 自身鉴权)。每个 tool 的 specifier 类型不同(命令/路径/域名/工具名),通配让组合既能粗(整类)又能细(精确)。各 tool 的 specifier/危险面/示例 rule 见下表(同 HTML §3.4):

| tool | specifier 类型 | 危险面 | 示例 rule |
|---|---|---|---|
| `Bash` | 命令模式(`*`/`?`,`&&`/`;`/`\|` 复合分段,每段独立匹配,任一段 deny→整条 deny;可按参数限定) | 高 | `allow(Bash(git status))`、`deny(Bash(rm *))` |
| `Read` | 路径(gitignore 语义;`*`单层、`**`递归) | 中 | `allow(Read(./docs/**))`、`deny(Read(./.env))` |
| `Edit`/`Write` | 路径(同 Read) | 高 | `allow(Write(/tmp/**))`、`deny(Edit(/prod/**))` |
| `Grep`/`Glob` | 路径/正则 | 低 | `allow(Glob(./*))`、`deny(Glob(./*.env))` |
| `WebFetch` | 域名(`domain:<glob>`) | 中 | `deny(WebFetch(domain:*.internal.company.com))` |
| `WebSearch` | 无 specifier,整工具一刀切 | 中 | `allow(WebSearch)`、`deny(WebSearch)` |
| `Agent`/`Task` | 子代理名(可按参数限定) | 中 | `allow(Agent(Explore))`、`deny(Agent(deploy))` |
| `Skill` | skill 名(通配 `skill *`) | 中 | `allow(Skill(review *))` |
| `MCP` (`mcp__<server>__<tool>`) | server.tool;`mcp__server__*` 通配整 server | 按挂载 | `deny(mcp__puppeteer__*)` |
| `exec` | 命令模式(同 Bash) | 高 | `deny(exec(rm *))` |
| 平台守卫(非角色边,不可取消) | — | — | `Bash(rm -rf /)` 熔断、`external_directory`→ask、`doom_loop`→deny |

角色示例(命名,与 HTML 第二部分场景一致):

```
只读者      = [ Read(./*) allow, Grep(./*) allow, Edit(*) deny, Bash(*) deny ]
受限协作者  = [ Bash(git *) allow, Read(./src/**) allow, Bash(rm *) deny, Bash(curl *) deny ]
```

---

## 3. 边的数据形态:一通道 + role_name 引用 + extra 覆盖

### 3.1 边的两个身份

一条有向边 `A → B` 同时是两件事(同一条边):

| 身份 | 含义 | 承载 |
|---|---|---|
| **关系身份** | A 和 B 是否有关系(friend / 被owner) | 边是否存在 + `is_creator` |
| **权限身份** | B 凭这条边以什么姿态运作为 A 做、能调哪些 tool | 绑在边上的 (tool,specifier,allow/deny) 集合 |

**关系身份是门票,权限身份是细度**。friend 管入场,边权限管入场细度。

### 3.2 边数据:channel + role_name 引用 + extra

```
Edge[from A → to B, env] = {
  edge_id      : 自增主键
  channel      : named_role | role_with_extra | custom   // 一时刻一个通道, 换通道=整体替换(非合并)
  role_name    : "被owner" | "只读者" | custom | null     // 对 bcs_role_defs 的引用, 不抄 rules; channel=custom 时可为 null
  extra_rules[]: 若干 (tool, specifier, allow|deny)        // 仅 role_with_extra; 与角色默认 rules 取并集, 冲突时 extra 覆盖
  custom_rules[]: 若干 (tool, specifier, allow|deny)       // 仅 custom; 不走任何角色, 独立选的一组
  delegation_depth : 0=不可再委托下游; >0=可传 N 跳
  expires_at   : 过期时间; null=永久
  grantor      : 谁授的这条边
}
```

**求值(对一次 tool call,这条边当前通道求出唯一一个权限集):**

```
channel = named_role        → 权限集 = bcs_role_defs[role_name].rules
channel = role_with_extra   → 权限集 = bcs_role_defs[role_name].rules ⊕ extra_rules (冲突 extra 覆盖)
channel = custom            → 权限集 = custom_rules
```

三种通道互斥:边一时刻只走一种。**换通道 = 整体替换这条边的权限集**(不残留旧 rules)。

### 3.3 被owner 边是特例吗?

不是特例。owner 关系天然读作"A 管 B"(A 是管理方),与本图"B 以角色运作、为 A 做事"反着——为统一方向,逆转为 `被owner` 角色(B 被某人拥有、B 以全权运作为 A 做)。token 简化上 `is_creator` 标它,图里它和其他角色边同构,只是 rules 是 `(*, *, allow)`(除了平台守卫)。**同一张图、同一套求值器,无特例。**

| 角色 | 边 `A → B` 读作 | 含义 |
|---|---|---|
| `被owner` | B 被 A 拥有、B 以全权运作 | B 用全权权限集运作;A 是 B 的 owner |
| `Teacher` | B 以 Teacher 角色运作、为 A 做事 | B 用 Teacher 权限集运作 |
| `只读者` | B 以只读者权限集运作、为 A 做事 | B 用只读权限集运作 |

### 3.4 删同源合并的理由(显式说明)

mentor 稿原写"同 grantor 匿名边自动合并",经讨论判定为错——那等于让 B 一直持有所有被授过的权限合集运作。本稿改为:**一时刻一个通道**。唯一允许的"叠加"是 role_with_extra 通道里"角色 + 附带"——此时 extra_rules 与角色默认 rules 在**同一通道内部**取并集,冲突时 extra 覆盖。这不是多权限集合并,而是一个通道的内部覆盖。

---

## 4. 求值:守卫 → 通道权限集 → bot default

```
对一次 tool call (caller A → bot B, tool, args):

  ① 平台守卫(最高,不可被任何边覆盖)
     命中守卫 deny(如 Bash(rm -rf /)) → deny · 熔断
     命中守卫 ask(external_directory 等) → 转 bot 第二层审批(本稿不展开)

  ② A→B 这条边的当前通道权限集(§3.2 求出的那一个,唯一一个)
     命中 allow → allow
     命中 deny  → deny
     未命中 → 走 ③

  ③ bot default 集(owner 配的对任何 caller 默认成立)
     命中 allow → allow ; 命中 deny → deny
     未命中 → deny(零信任)
```

**与 mentor 稿差异**: mentor 是"多边 allow 优先"(同一对点多条边、任一 allow 即放行)。本稿改为"一时刻一通道"——同一对 (A,B) 一时刻只有一个权限集在用,不再"多边合成"。role_with_extra 的覆盖是**一通道内部**(extra 覆盖 role 默认),不是多边并集。

两个边界:
- **想换结果就换通道,不是加边**:当前通道 deny 了 `Edit(/prod/**)`,想让 B 能改生产 → 换通道(换成含 `allow(Edit(/prod/**))` 的角色/custom,或 role_with_extra 用 extra 覆盖)。换 = 整体替换,旧 rules 不残留。
- **绝对禁止靠平台守卫**:通道里 `deny(Bash(rm -rf /))` 是可被换通道绕开的普通 rule。真正"谁都不能做"靠平台守卫层,最高优先级、不可取消。

---

## 5. 落点:边 = 现成关系表;角色/通道用扩展表

**点-边图 = 现成 `bcs_actor_relations` 表**(每条边一行)。`RelationEdge`(`bcs-domain/src/actor.rs:65`)已预埋 `kinds/allow/deny` 三个 64-bit 位图(V1 恒 0,见 `actor.rs:59` 注释 "V1 only consumes is_creator; kinds/allow/deny are bitmap"),本方案即"点亮位图"。

```rust
// actor.rs:65-85 (现成,不改结构)
pub struct RelationEdge {
    pub from_id: String,   // human_{staff_no} 或 bot uuid
    pub to_id:   String,   // bot uuid
    pub env:     String,   // 边键的一部分, 按环境隔离
    pub kinds:   u64,      // 预埋位图 V1 恒 0;本方案点亮(通道/整类摘要)
    pub allow:   u64,      // 预埋位图;点亮
    pub deny:    u64,      // 预埋位图;点亮
    pub is_creator: bool,  // 被owner 边标识
}
```

位图是这条边当前通道权限集的 **粗粒度摘要**(整类 tool 一个位),用于 BCS 准入期 O(1) 粗判;specifier 精确规则放扩展表。**位图挡大面、规则判细节。**

两张扩展表(待建):

| 表 | 存什么 | 怎么用 |
|---|---|---|
| `bcs_role_defs` | 角色定义:角色名 + rules(一组三元组) + owner_bot | 给边引用;role_with_extra 引用其默认 rules |
| `bcs_edge_grants` | 边的当前通道:channel + role_name(引用) + extra_rules/custom_rules + delegation_depth + grantor + version | bot 端 before_tool_call 细判,精确 specifier |

**写入**: `bcs-relation-store/src/lib.rs` 的 upsert(当前硬编码 0,0,0 需打开) + 写 `bcs_edge_grants`。**校验**复用 `bcs-group/src/application/management.rs` 的 `ensure_reachable`。

---

## 6. 真实场景九环节(图怎么动—mentor 第二部分)

1. owner 定义角色(写 `bcs_role_defs`,绑 owner_bot)+ Alice 的 `被owner` 边
2. Bob 与 bot2 建 friend → 关系边(无通道,位图恒 0)。friend 是入场券
3. 绑通道(写 `bcs_edge_grants`,关系边长权限身份 + 位图回写)
4. 一次调用 Bob → bot2 `Read(./src/main.rs)`:守卫未命中→位图粗判 Read allow→bot 细判通道→第一层 allow→喂第二层
5. **换通道**(named_role=只读者 → role_with_extra=只读者+Write(/tmp)允;让 Write(/tmp) 放行,extra 覆盖 onlyreader 的 deny;非加边、非合并)
6. 长链 `Bob → bot2 → bot3`:每跳一条独立边、逐跳独立判(`v1(看 Bob→bot2)` ∧ `v2(看 bot2→bot3)`)。不回溯发起者
7. 回环 `bot1 → bot2 → bot3 → bot2 → bot3…`:每跳看自己的边;跳3 看 `bot3→bot2` 边(本就在图上)。**不回溯发起者 → 死结①消除**。死循环另由守卫 `doom_loop` 兜
8. 平台守卫兜底(`Bash(rm -rf /)` 熔断、`external_directory`→ask、`doom_loop`→deny)。通道之上的安全闸,绕不开
9. 撤销/改约 = 删边/换通道(version+1);friend 关系边保留

## 7. 委托:只能收紧,不可升级(链上 ⊆ 发起 caller 第一跳)

caller 把自己部分权限向下传用 `delegation_depth`。下游边 = 收紧后的上游:

```
new_allow = 下游_req.allow & 上游.allow   // 子集
new_deny  = 下游_req.deny  | 上游.deny    // 更严
new_depth = 上游.delegation_depth - 1     // 衰减
```

逐跳 AND + 委托收紧 → 链上任意 bot 能做的 ⊆ 发起 caller 在第一跳被允许做的。**无需回溯发起者即得此界**——这正是 briefing 想要、旧方案做不到的性质。

---

## 8. 与 briefing 两层 AND 的衔接

- 本稿第一层(协作鉴权)只产二元 `allow/deny`:走 §4 求值(守卫→通道权限集→bot default)。
- 第一层 deny → 短路终局 deny,不走第二层。第一层 allow → 喂第二层(bot 自身鉴权,三态含 ask)。
- 第二层不在本稿范围;衔接契约:第一层 verdict ∈ {allow, deny} + 可选元信息(命中的 channel/role) → bot runtime 第二层。

---

## 9. 平台守卫(独立一层)

非角色边、不归 owner 配、不可取消、最高优先级:

```
Bash(rm -rf /)   → deny 熔断(删根/删家目录)
Bash(rm -rf ~)   → deny 熔断
external_directory → 转 bot 第二层 ask(触工作目录外)
doom_loop        → deny(同 call 重复 N 次)
```

先判守卫,过了才进通道权限集。守卫保安全底线,通道做协作灵活。

---

## 10. 测试(简,实现阶段扩)

- 单元: 通道求值器(named_role/role_with_extra/custom 三通道;role_with_extra 中 extra 冲突覆盖 role 默认)。
- 单元: 角色定义引用正确(extra 不抄 role rules,只引 role_name)。
- 单元: §4 三档(守卫 > 通道 > default)优先级、default 未命中 deny。
- 单元: 换通道 = 整体替换(换后旧 rules 不残留,version+1)。
- 集成: §6 九环节,每环节图状态正确(建 friend→绑通道→换通道→长链→回环→撤销)。
- 集成: 回环每跳看自己的边;死循环由 doom_loop 守卫兜。
- 集成: 委托收紧(下游 ⊆ 上游)。
- 集成: 第一层 deny 短路;allow 喂第二层。

---

## 11. 待讨论

- **D1 位图段分配**: 64-bit kinds/allow/deny 位含义(通道类型/整类 tool 位)如何分?动态注册 tool(MCP/Skill)是否占固定位?
- **D2 角色定义同步**: bot 侧定义角色后怎么同步到 BCS?推(owner API)还是拉(bot 启动拉取)?位图谁预渲染(通道切换时)?
- **D3 缓存与失效**: bot 端缓存边通道用 version + TTL 拉,还是 BCS 换通道后主动推送?singlebox 是否复用?
- **D4 委托 depth 上限**: 默认值与硬上限(防恶意长链放大)?全局 max_depth?
- **D5 换通道的并发与一致性**: 同一对点同时换通道时,version 怎么管?bot 读到的通道快照是否要求一致?
- **D6 与 briefing 二层衔接**: 第一层(通道权限集)产二元后的衔接契约细节。
- **D7 被owner 命名**: `被owner` 这名字是否最终采纳?是否保留旧 `owner` 作别名(向后兼容 is_creator)?
