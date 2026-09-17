/* Edit slide copy, duration (seconds), and speaker notes, then run build.py. */
const REPO_URL = "https://github.com/inclusionAI/Avernet";
const SOURCES = {
  "overview": "README.zh-CN.md",
  "shop": "docs/small-shop-dream-team-live-demo-tutorial.zh-CN.md",
  "game": "docs/undercover-game-tutorial.zh-CN.md",
  "rules": "scripts/6bots_undercover_game_profile/CONTEXT.md",
  "profile": "scripts/6bots_undercover_game_profile/README.md",
  "start": "docs/quick-start.zh-CN.md",
  "arch": "docs/arch/arch.rules.md",
  "hybrid": "scripts/4bots_merchant_operations_profile_for_claude/README.md",
  "routing": "src/bcs/crates/services/bcs-routing/src/core/router.rs",
  "session": "src/bcs/crates/plugins/openclaw-channel-bcn/src/inbound-handler.ts",
  "context": "src/bcs/crates/plugins/openclaw-channel-bcn/src/types.ts",
  "history": "src/bcs/crates/services/bcs-message-flow/src/group_history.rs",
  "fusion": "src/bcs/crates/services/bcs-fusion/CONTEXT.md",
  "fusionContract": "src/bcs/crates/contracts/bcs-domain/src/fusion.rs",
  "singlebox": "scripts/singlebox.sh",
  "services": "scripts/modules/all.sh",
  "gamePreview": "src/bcs/assets/panel/test/undercover-game/visual-preview.tsx",
  "logo": "src/frontend/public/Avernet-logo.png"
};
const slides = [
  {
    title: "当产品开始替用户组队", section: "目标与协作", duration: 30, theme: "dark", layout: "cover",
    html: "<div class=\"cover-top\"><span class=\"brand-icon\"><img src=\"__AVERNET_ICON__\" alt=\"Avernet 图标\"></span>AVERNET <span>PRODUCT KEYNOTE</span></div><h1>当产品开始<br><em>替用户组队</em></h1><p class=\"cover-sub\">AI 运营从单点智能走向协作交付</p><div class=\"cover-bottom\"><span>产品实践 / 多智能体协作网络</span><span>50 MIN</span></div>",
    notes: ["今天从一个用户目标出发，讨论产品怎样接住目标背后的组织工作。先看店主如何委派经营目标，再和 Agent 玩一轮谁是卧底，随后拆解上下文和协作体验设计，最后介绍上手入口与产品增长。技术部署细节放在问答之后的附录。","正式分享 50 分钟。问答在 50 分钟之外。封面停留约半分钟，直接进入店主的故事。"], sources: ["logo"]
  },
  {
    title: "店主只说了一句话", section: "目标与协作", duration: 60, theme: "light", layout: "quote",
    html: "<p class=\"eyebrow\">一个经营目标</p><blockquote>帮我做好这次店庆：<br>品质不变，<br>让更多客人进店，<br><em>让更多到店客人消费。</em></blockquote><p class=\"quote-caption\">客流优先，兼顾转化；活动贡献毛利率不低于 10%。</p>",
    notes: ["把这句话读给观众，稍停一下。设想你是理发店店主，日常已经在忙排班、接待和客户关系。现在你希望 AI 帮你推进店庆。品质不变，客流是第一目标，转化是第二目标，活动贡献毛利率不能低于 10%。","完整委派还包括：下周开始，活动为期一个月；老客主推护理套餐，新客用王牌剪发引流。请协调平台营销、平台数据和平台供应链，协商出一套可执行、可验收的方案和 SOP。时间与客群策略在后续 Demo 中展开。","生成一份营销建议很容易让人感到有用。但店主真正希望改变的是客流和转化。要达到这个目标，还需要了解平台的补贴条件、门店的服务产能和耗材供应情况。","这里先不介绍系统和模块，让听众代入用户的处境。目标已经说清，组织工作才刚刚开始。"], sources: ["shop"]
  },
  {
    title: "方案之后，谁来推进？", section: "目标与协作", duration: 60, theme: "dark", layout: "questions",
    html: "<p class=\"eyebrow\">交付中的组织工作</p><h2>方案之后，<br>谁来推进？</h2><div class=\"question-rows\"><div><span>01</span><strong>数据由谁提供</strong><small>客流、转化、服务产能</small></div><div><span>02</span><strong>资源由谁承诺</strong><small>补贴、库存、交期</small></div><div><span>03</span><strong>冲突由谁处理</strong><small>销量目标与履约约束</small></div><div><span>04</span><strong>结果由谁验收</strong><small>完成条件与执行证据</small></div></div>",
    notes: ["沿着四个问题解释组织工作的成本。营销方案可能希望多卖，门店可能接待不过来，供应链可能不能按时到货。","今天很多工作仍由用户在不同产品之间搬运信息、追问进度、确认口径。用户成为了各项能力之间的协调者。","这四个问题也是稍后判断 Demo 是否真正体现协作的观察点。"], sources: []
  },
  {
    title: "产品接住目标背后的工作", section: "目标与协作", duration: 45, theme: "blue", layout: "statement",
    html: "<p class=\"eyebrow\">委派目标</p><h2>用户表达目标<br>产品组织角色<br><span class=\"soft\">团队推进交付</span></h2><p class=\"large-sub\">用户设定授权边界，并在关键节点作出决定。</p>",
    notes: ["当用户委派目标，产品需要承担更多组织责任：找到角色、提出任务、推进协商、记录承诺、呈现进度。","委派始终有边界。用户定义目标和授权，Agent 在边界内行动，遇到必须由人决定的事项时交回来。","接下来给出一套选择协作方式的判断框架。"], sources: []
  },
  {
    title: "参与者：能力分工与主体边界", section: "目标与协作", duration: 75, theme: "light", layout: "compare",
    html: "<p class=\"eyebrow\">判断框架 / 第一层</p><h2>参与者：能力分工与主体边界</h2><div class=\"compare-columns\"><div><span class=\"index\">A</span><h3>同一主体内部分工</h3><p>共享目标与最终责任</p><div class=\"role-list\"><span>研究</span><span>写作</span><span>校对</span></div><p class=\"muted\">关注任务拆解、专业分工和结果汇总。</p></div><div><span class=\"index\">B</span><h3>不同主体开放协作</h3><p>各自拥有信息、权限与资源</p><div class=\"role-list\"><span>商家</span><span>平台</span><span>供应商</span></div><p class=\"muted\">需要处理独立承诺、利益差异和授权。</p></div></div><p class=\"bottom-line\">先判断单 Agent 能否承担任务，再判断多个角色之间有哪些独立边界。</p>",
    notes: ["第一层先问需要一个参与者还是多个参与者。角色拆分应由任务需要决定。一个 Agent 已能在完整信息和权限下交付时，可以保持简单。","多 Agent 又有不同组织形态。内部研究、写作、校对可以服务同一个主体。商家、营销平台、供应商则各有资源、权限和利益。","判断开放协作需要看角色独立性、权限差异、上下文边界和利益冲突。角色名字不同，不能自动证明主体独立。"], sources: []
  },
  {
    title: "过程：预设编排与动态协商", section: "目标与协作", duration: 90, theme: "dark", layout: "matrix",
    html: "<p class=\"eyebrow\">判断框架 / 第二层</p><h2>过程：预设编排与动态协商</h2><div class=\"matrix\"><div class=\"matrix-empty\"></div><div class=\"matrix-label\">预设编排</div><div class=\"matrix-label\">动态决策 / 协商</div><div class=\"matrix-label side-label\">单 Agent</div><div class=\"matrix-cell\"><strong>固定步骤处理</strong><p>按既定规范提取、整理、检查</p></div><div class=\"matrix-cell\"><strong>自主选择下一步</strong><p>根据反馈调整搜索或工具使用</p></div><div class=\"matrix-label side-label\">多 Agent</div><div class=\"matrix-cell\"><strong>预先定义角色与依赖</strong><p>稳定的内容生产或审核流程</p></div><div class=\"matrix-cell accent-cell\"><strong>协商后形成执行流程</strong><p>根据资源、条款和反馈组织协作</p></div></div><p class=\"bottom-line\">角色数量与过程确定性，是两个独立的判断维度。</p>",
    notes: ["第二层关注过程。步骤和验收条件稳定时，预设编排便于重复执行。资源、参与者、承诺条款需要在任务中确定时，就需要动态决策或协商。","这张表是一套设计判断框架，不是对 Avernet 全部功能的可用性声明。单 Agent 的动态决策和多个主体间的协商，也要区分。","预设与动态可以组合。小店先协商，再把达成的候选方案放入明确的复核流程。游戏规则预先确定，每轮参与者和执行安排则根据状态变化。"], sources: []
  },
  {
    title: "Avernet：Agent 协作的运行基础设施", section: "目标与协作", duration: 90, theme: "light", layout: "product",
    html: "<p class=\"eyebrow\">产品实践</p><h2 class=\"product-name\"><img src=\"__AVERNET_WORDMARK__\" alt=\"Avernet\"></h2><p class=\"product-definition\">让持久化、异构的 Agent<br>进入同一个协作网络</p><div class=\"product-strip\"><div><b>接入与发现</b><span>找到可以一起工作的角色</span></div><div><b>组队与协作</b><span>组织任务，让进展可见</span></div><div><b>人机共同参与</b><span>提供输入，参与关键验收</span></div></div><p class=\"source-caption\">开源基础设施 / 具体能力开放状态以仓库说明为准</p>",
    notes: ["现在引出 Avernet。它为持久化、异构 Agent 系统提供运行与协作基础设施。Agent 可以来自不同运行时，协作网络承担接入、发现、关系、群组、路由和协作执行。","从用户视角看，产品中可以找到 Bot，组织协作，并参与执行过程。接下来用两个本地场景观察这些能力。","当前公开仓库与完整生产能力的覆盖范围不同。今天聚焦能够由教程复现的行为，经验沉淀等后续讨论会明确标记为产品演进方向。"], sources: ["overview"]
  },
  {
    title: "不同引擎中的 Agent，也能成为队友", section: "目标与协作", duration: 90, theme: "dark", layout: "heterogeneous",
    html: "<p class=\"eyebrow\">异构引擎协作</p><h2>不同引擎中的 Agent，<br>也能成为队友</h2><div class=\"engine-grid\"><div><span>目标理解 / 持续推进</span><h3>OpenClaw</h3><p>店长 Agent</p></div><div><span>分析 / 工具执行</span><h3>Claude Code</h3><p>数据与自动化 Agent</p></div><div><span>办公 / 业务产出</span><h3>千问办公</h3><p>运营与文档 Agent · 示意</p></div></div><div class=\"engine-branches\"><i></i><i></i><i></i></div><div class=\"engine-network\"><b>Avernet 协作网络</b><span>找到专业角色 · 分派合适任务 · 共同推进目标</span></div><p class=\"source-caption\">公开示例：OpenClaw + Claude Code；千问办公用于协作示意，接入能力以仓库说明为准。</p>",
    notes: ["支持异构引擎，意味着参与者可以保留自己的运行方式和专业能力，通过共同的接入与消息契约协作。图中用 OpenClaw 店长、Claude Code 数据与自动化角色、千问办公运营与文档角色示意同一任务中的协作。","引擎与角色是两个维度。同一个引擎可以承载不同角色，同一个角色也可以使用不同引擎。Avernet 负责连接参与者、路由任务和管理协作状态。","公开仓库的 hybrid 示例提供 OpenClaw 与 Claude Code 的组合，可用来复现异构接入。千问办公按本次分享要求出现在协作示意图中；这张图不表示该混合组合就是两个 Demo 的实际配置，也不表示公开 singlebox 已内置千问办公安装项。"], sources: ["overview","hybrid","context"]
  },
  {
    title: "一家小店的背后，站着一个天团", section: "小店经营", duration: 90, theme: "blue", layout: "demo",
    html: "<p class=\"eyebrow\">DEMO 01 / 小店经营</p><h2>一家小店的背后，<br>站着一个天团</h2><p class=\"demo-sub\">18 周年店庆：拉新、转化、品质与履约</p><div class=\"demo-observation\"><span>从店主的一句话开始</span><b>提出目标 → 组织角色 → 协商与验收</b></div><p class=\"source-caption\">本地 Demo 使用预置 Bot 模拟商家与平台角色。</p>",
    notes: ["切到已经准备好的小店经营环境。在店主与店长的私聊中提交目标：今年做 18 周年店庆，下周开始，为期一个月。品质不变，先提高客流，再提高转化，老客主推护理套餐，新客用剪发引流。","先给目标和毛利底线，让店长追问会改变执行方式的授权问题。再补齐商家促销预算、备货现金占用上限。具体输入见教程。","这一页作为小店 Demo 的过渡，后续补入演示视频。当前讲稿可以结合后面的教程截图讲清协作过程。"], sources: ["shop"]
  },
  {
    title: "店长围绕目标组织专业角色", section: "小店经营", duration: 75, theme: "light", layout: "roster-evidence",
    html: "<p class=\"eyebrow\">组队 / 真实协作界面</p><h2>店长围绕目标组织专业角色</h2><div class=\"roster-evidence-grid\"><div class=\"shop-image-frame\"><img src=\"__SHOP_ROSTER__\" alt=\"周年庆协作群截图：店长、营销、数据和供应链四个 Bot，以及供人类加入的分享群组入口\"></div><div class=\"roster-explainer\"><div><span>一个共同目标</span><h3>18 周年店庆<br>拉新与转化</h3></div><div><span>四个专业角色</span><p>店长统筹<br>营销、数据、供应链协作</p></div><div><span>人也能加入团队</span><p>店主通过链接加入，<br>参与关键决定。</p></div></div></div><p class=\"source-caption\">周年庆 Demo 历史截图；场景 Bot 模拟商家与平台角色。</p>",
    notes: ["这张图展示实际协作群的成员列表：店长日常运营是群主 Bot，营销、数据分析和供应链三个专业角色共同参与。对应的是一个已经组织起来的任务团队。","Demo 的共同目标是 18 周年店庆、拉新与转化，并保持品质不变。为方便投屏阅读，本页截取成员与分享入口，右侧概括目标。营销负责活动和补贴条款，数据校验需求与产能，供应链确认库存与采购条件。","底部的分享群组入口允许人类通过链接加入。截图当前列出四个 Bot，并未显示店主已经在群内；讲述时说明店主可以加入，后续参与关键节点。","角色独立性由本地 Demo profile 模拟，真实开放协作需要各方身份、权限、数据和系统接入。"], sources: ["shop"]
  },
  {
    title: "授权范围与信息范围分别设计", section: "小店经营", duration: 90, theme: "light", layout: "boundaries",
    html: "<p class=\"eyebrow\">上下文与授权</p><h2>授权范围与信息范围分别设计</h2><div class=\"boundary-grid\"><div class=\"private-zone\"><span class=\"zone-label\">店主与店长 / 私有约束</span><div class=\"budget\"><b>10<span>%</span></b><p>活动贡献毛利底线</p></div><div class=\"budget-small\"><div><b>3,000 元</b><p>商家促销总预算</p></div><div><b>5,000 元</b><p>备货新增现金上限</p></div></div></div><div class=\"public-zone\"><span class=\"zone-label\">专业协作者 / 任务所需信息</span><h3>经营事实<br>候选条款<br>校验结论</h3><p>各方获取完成本职任务所需的信息。</p></div></div><p class=\"source-caption\">数字来自教程中的演示输入，不代表真实经营结果。</p>",
    notes: ["这三个数字来自演示场景：活动贡献毛利不低于 10%，商家促销总预算不超过 3000 元，备货新增现金占用不超过 5000 元。讲清三者的口径不同，不能混算。","授权决定店长可以作出什么决策。信息范围决定哪些事实可以发送给谁。能参与一个任务，不意味着应该看到商家全部私有约束。","在现场观察 Worker 收到的任务和回复是否仅包含必要信息。这里说明的是该 profile 的设计和可观察行为，不能扩展成所有通道都具有完整安全隔离的承诺。"], sources: ["shop"]
  },
  {
    title: "各方对同一个方案作出承诺", section: "小店经营", duration: 90, theme: "dark", layout: "negotiation",
    html: "<p class=\"eyebrow\">协商与契约</p><h2>各方对同一个方案<br>作出承诺</h2><div class=\"negotiation-rows\"><div><span>营销</span><strong>卖什么，优惠谁承担</strong><small>券结构、补贴、核销规则</small></div><div><span>数据</span><strong>需求与产能是否匹配</strong><small>客流、转化、服务分钟</small></div><div><span>供应</span><strong>能否按约定履约</strong><small>数量、交期、品质条件</small></div></div><div class=\"contract-line\"><b>同一方案版本</b><span>责任人</span><span>适用范围</span><span>条件与证据</span></div>",
    notes: ["选择现场的一处分歧来解释，例如活动核销量需要同时满足营销口径、服务产能和护理耗材供应。不要预设每轮模型一定提出同样的金额和数量。","各方都说可以，还不足以确定交付。它们必须针对同一个方案版本，用同一套数量、单位、时间窗口和假设作出承诺。","这里的契约指可验收的协作约定，包含责任人、值、单位、范围、时间、来源和授权状态。它不自动等同于法律合同。"], sources: ["shop"]
  },
  {
    title: "协商结果进入复核与修订流程", section: "小店经营", duration: 180, theme: "light", layout: "review-evidence",
    html: "<p class=\"eyebrow\">执行与修订</p><h2>协商结果进入<br>复核与修订流程</h2><div class=\"evidence-layout\"><div class=\"evidence-image\"><img src=\"__SHOP_SCREENSHOT__\" alt=\"周年庆 Demo 截图：营销、数据与供应链复核，店长汇总后进入等待店主验收\"></div><div class=\"evidence-caption\"><h3>同一版本，共同复核</h3><div class=\"review-stages\"><div><span>01</span><div><h4>三方复核</h4><p>核对候选条款与履约条件</p></div></div><div><span>02</span><div><h4>汇总修订</h4><p>发现缺口，整理修改意见</p></div></div><div><span>03</span><div><h4>店主验收</h4><p>确认当前版本，或提出修改</p></div></div></div></div></div><p class=\"source-caption\">历史 Demo 的复核片段；绿色表示节点已完成，店主验收仍待处理。</p>",
    notes: ["店长取得首轮回复后，动态生成一次性自定义协作，对公开候选方案进行三方复核。截图展示营销、数据与供应链复核、店长汇总，再进入店主验收的片段。","各方需要围绕同一版本检查需求、资源与约束；发现缺口时提出修改意见，由店长汇总、修订候选方案，再组织确认。重点是复核、修订与完成条件。","绿色节点表示该节点已执行完成，不等于所有经营约束均已通过。画面中的店主验收仍待处理，不能把这张图解释成已完成的成功交付。复核轮次属于历史运行，不作为产品设计限制。"], sources: ["shop"]
  },
  {
    title: "团队带回方案，店主作出决定", section: "小店经营", duration: 75, theme: "light", layout: "owner-evidence",
    html: "<p class=\"eyebrow\">人机共同参与 / 店主验收</p><h2>团队带回方案，店主作出决定</h2><div class=\"owner-evidence-grid\"><div class=\"shop-image-frame owner-image\"><img src=\"__SHOP_ACCEPTANCE__\" alt=\"周年庆 Demo 店主验收截图：等待人工输入，展示候选方案、接受或修改的判定，并提供审核意见输入框和提交入口\"></div><div class=\"owner-explainer\"><span class=\"awaiting-owner\">等待店主验收</span><h3>接受当前版本<br>或提出修改</h3><div class=\"owner-decision-points\"><p><b>先看依据</b><span>阅读候选条款与审核说明</span></p><p><b>再作决定</b><span>用自然语言提交审核意见</span></p><p><b>继续推进</b><span>流程根据判定结果进入后续分支</span></p></div><div class=\"owner-delivery-boundary\"><b>交付边界</b><p>方案验收与真实投券、采购等<br>外部执行分别确认。</p></div></div></div><p class=\"source-caption\">周年庆 Demo 历史截图：当前状态为等待店主验收。</p>",
    notes: ["团队已经把当前候选方案带到店主验收节点。画面明确显示等待人工输入，而非验收通过。不能根据这张截图认定所有约束已经满足，也不能推断投券或采购已经完成。","沿着画面介绍三个要素：用户知道当前要决定什么，能读到候选条款与审核说明，并有明确的意见输入和提交入口。店主可以接受当前版本，或提出需要修改的条款；系统随后依据提交内容判断并继续执行。截图中的金额、数量和时间只属于当次历史方案。","店主在验收前应检查尚未解决的问题。进入人工验收节点不代表所有约束已经满足，需要根据候选条款和审核依据作出判断。","小店 Demo 以形成并验收经营 SOP 为交付范围。真实投券、采购、付款、排班或库存锁定，还需要外部系统执行与回执。方案验收不能代替这些业务执行证据。","这也是后面产品设计 03 的实际例子：Agent 持续推进任务，在需要人作决定的节点带回方案与依据。"], sources: ["shop"]
  },
  {
    title: "和 Agent 玩一轮谁是卧底", section: "谁是卧底", duration: 120, theme: "blue", layout: "demo",
    html: "<p class=\"eyebrow\">DEMO 02 / 谁是卧底</p><h2>和 Agent 玩一轮<br>谁是卧底</h2><p class=\"demo-sub\">不同的信息，不同的立场，共同的规则</p><div class=\"demo-observation\"><span>你也是其中一名玩家</span><b>观察发言 → 描述词语 → 推理与投票</b></div>",
    notes: ["小店里的各方需要形成可接受的经营方案。游戏则让我们更直观地看到：参与者知道的信息不同，所追求的结果也可能不同。","这一页作为游戏 Demo 的过渡，后续补入演示视频。下一页先展示游戏副屏，解释公开发言和玩家操作的关系。","现场只安排一个完整回合。可以请观众为你的角色提议一句描述，或一起判断投票对象，由你统一输入。整局结局留作预先准备的记录。"], sources: ["game","profile"]
  },
  {
    title: "协作过程，变成可以参与的游戏", section: "谁是卧底", duration: 60, theme: "light", layout: "game-evidence",
    html: "<p class=\"eyebrow\">DEMO 02 / 游戏副屏</p><div class=\"game-evidence-copy\"><h2>协作过程，<br>变成可以<br>参与的游戏</h2><div class=\"game-observe\"><div><span>角色</span><p>真人与 Agent 同桌参与</p></div><div><span>信息</span><p>公开发言形成共同线索</p></div><div><span>状态</span><p>主持人推进回合与裁决</p></div></div></div><div class=\"game-screenshot\"><img src=\"__GAME_SCREENSHOT__\" alt=\"谁是卧底游戏副屏：六名玩家围桌发言，主持人推进回合\"></div><p class=\"source-caption\">真实游戏副屏组件截图，使用仓库中的演示数据。</p>",
    notes: ["这张图来自仓库真实的 UndercoverGamePanel 组件，由 visual-preview.tsx 中的演示数据驱动。它用于展示产品界面，不是某次在线模型对局的证据。","沿着画面介绍主持人、六名玩家、当前轮次、发言状态和公开线索。真人与 Agent 使用同一套游戏状态；公开发言帮助推理，自己的词语则留在个人信息范围中。","截图只展示房间区域，玩家发言和投票会在自己的操作区按阶段出现。后续补上完整 Demo 视频后，可把这一页作为视频之后的观察与复盘。"], sources: ["game","gamePreview"]
  },
  {
    title: "六名玩家，一位主持人", section: "谁是卧底", duration: 60, theme: "dark", layout: "players",
    html: "<p class=\"eyebrow\">参与者与角色</p><h2>六名玩家，一位主持人</h2><div class=\"players-layout\"><div class=\"player-grid\"><div><span>01</span><b>稳健老陈</b><small>谨慎推理</small></div><div><span>02</span><b>话痨小满</b><small>场景联想</small></div><div><span>03</span><b>和事佬阿和</b><small>温和表达</small></div><div><span>04</span><b>逻辑控林工</b><small>属性分析</small></div><div><span>05</span><b>戏精阿浪</b><small>生动指认</small></div><div class=\"human-player\"><span>06</span><b>你</b><small>真人玩家</small></div></div><div class=\"referee\"><span>REFEREE</span><h3>主持人</h3><p>发牌<br>推进回合<br>计票与裁决</p><small>主持人不参赛</small></div></div><p class=\"source-caption\">角色示意。实际座位与词语由开局分配。</p>",
    notes: ["启动的是六个 Bot，其中一个是主持人、五个是玩家。加上你这位真人，共有六名玩家参与推理和胜负判定。主持人不占玩家座位，也不参与投票。","这些性格差异提供不同的表达和推理方式。角色差异与规则约束共同塑造了体验。图中的编号只是排版示意，实际座位在开局时分配。","人类以自己的身份加入当前会话，成为流程中真实的一名参与者。"], sources: ["game","rules","profile"]
  },
  {
    title: "每个人只知道自己应该知道的部分", section: "谁是卧底", duration: 60, theme: "light", layout: "information",
    html: "<p class=\"eyebrow\">信息结构</p><h2>公开信息与私有信息</h2><div class=\"info-bands\"><div><span>个人上下文</span><h3>自己的词语与输入提示</h3><p>玩家根据自己的信息进行描述和判断。</p></div><div><span>公共空间</span><h3>座位、公开发言、轮次与结果</h3><p>所有参与者遵守同一套可观察的规则。</p></div><div><span>投票阶段</span><h3>提交各自选择，等待统一公布</h3><p>收票期间不展示其他人的投票目标。</p></div></div>",
    notes: ["请观众观察自己的词语、公开发言与投票阶段的可见信息。玩家开局只拿到词，不直接得知自己是平民还是卧底。","游戏把上下文边界变成可以直接体验的规则：公开发言参与共同推理，个人输入保留私有内容，投票阶段避免提前看到他人选择。","这些是该场景的设计机制。角色提示与通道控制有各自的边界，不能把一轮成功游戏解释成完整信息安全保证。"], sources: ["game","profile"]
  },
  {
    title: "规则确定，每轮执行随状态变化", section: "谁是卧底", duration: 120, theme: "dark", layout: "round",
    html: "<p class=\"eyebrow\">规则与运行状态</p><h2>规则确定，<br>每轮执行随状态变化</h2><div class=\"round-steps\"><div><span>01</span><h3>依次发言</h3><p>存活玩家按顺序描述</p></div><div><span>02</span><h3>分别投票</h3><p>在合法候选人中选择</p></div><div><span>03</span><h3>主持人裁决</h3><p>计票，按规则推进</p></div><div><span>04</span><h3>下一轮或结束</h3><p>更新参与者与游戏状态</p></div></div><p class=\"bottom-line\">固定规则与动态组织，可以存在于同一个产品流程中。</p>",
    notes: ["完成现场这一轮的发言与投票，观察主持人如何推进。根据当前有效规则处理计票、平票 PK、淘汰和胜负，细节以 profile 的现行规范为准。","在实现层面，发言和投票由临时自定义协作承载。发言按顺序进行，投票可以分别执行并在之后汇总。每轮根据存活玩家和状态组织节点。","这就体现了前面的两层框架：参与者是多个角色和真人，规则预先确定，而执行安排动态变化。时间到八分钟时切回演示稿，不等待整局结束。"], sources: ["rules","profile"]
  },
  {
    title: "两种场景，共享一组协作问题", section: "能力与设计", duration: 45, theme: "light", layout: "comparison-table",
    html: "<p class=\"eyebrow\">场景复盘</p><h2>两种场景，共享一组协作问题</h2><table class=\"scene-table\"><thead><tr><th></th><th>小店经营</th><th>谁是卧底</th></tr></thead><tbody><tr><th>角色</th><td>店长与专业协作者</td><td>主持人与独立玩家</td></tr><tr><th>信息边界</th><td>私有经营约束与公开条款</td><td>私有词语与公开发言</td></tr><tr><th>推进方式</th><td>协商、复核与修订</td><td>按规则组织当前回合</td></tr><tr><th>人的位置</th><td>委派目标、授权、验收</td><td>直接参与发言与投票</td></tr></tbody></table>",
    notes: ["用一分钟把两个 Demo 放在一起。它们面向的结果不同，但都需要身份、信息边界、有效行动和明确状态。","游戏中的对抗不能直接等同于商业主体间的利益协商。它帮助我们观察不同信息和不同立场下的交互机制。"], sources: ["shop","game"]
  },
  {
    title: "共享让团队对齐，隔离让角色成立", section: "上下文设计", duration: 60, theme: "blue", layout: "context-why",
    html: "<p class=\"eyebrow\">上下文 / 协作的前提</p><h2>共享让团队对齐<br>隔离让角色成立</h2><div class=\"context-contrast\"><div><span>应该共享什么</span><h3>目标、事实、版本与进度</h3><p>各方依据同一份公开事实协作，<br>减少误解、重复工作与过期决策。</p></div><div><span>应该隔离什么</span><h3>私有信息、历史与授权边界</h3><p>保留角色各自的信息和立场，<br>避免无关上下文干扰或越界传播。</p></div></div><p class=\"context-principle\">每一步都要回答：谁，为了什么任务，需要知道什么？</p>",
    notes: ["回看两个 Demo：店长需要与各方共享候选条款，但商家私有预算不必发给所有协作者；游戏玩家需要看到公开发言，但如果所有人都拿到彼此的词语，游戏就失去意义。","共享不足，各方会围绕不同事实或不同版本工作。共享过度，角色的信息边界被抹平，还会把不相关历史带进当前决策。","对用户来说，这些边界应该是可以理解的：哪些内容留在私聊，哪些会交给团队，哪些行动需要再次确认。接下来用小店场景说明 Avernet 如何组织这些信息范围。"], sources: ["shop","game","context"]
  },
  {
    title: "同一次协作，信息按角色展开", section: "上下文设计", duration: 90, theme: "dark", layout: "context-routing",
    html: "<p class=\"eyebrow\">Avernet 的上下文设计</p><h2>同一次协作，<br>信息按角色展开</h2><div class=\"context-pipeline\"><div><span>店主 ↔ 店长</span><h3>私聊中的约束</h3><p>预算、底线与授权<br>先在委派关系中说清</p></div><div><span>店长 ↔ 协作团队</span><h3>共同工作的事实</h3><p>目标、候选方案与进度<br>让大家围绕同一版本讨论</p></div><div><span>店长 ↔ 专业角色</span><h3>完成任务所需的信息</h3><p>给出必要条件与任务<br>带回专业判断和承诺</p></div></div><div class=\"routing-modes\"><div><b>需要你行动</b><p>明确这次要完成什么、如何确认完成</p></div><div><b>只需保持知情</b><p>同步相关进展，减少重复响应</p></div></div><p class=\"source-caption\">设计信息范围时，同时说明哪些内容会共享给谁。</p>",
    notes: ["沿着小店的委派关系来讲：预算与底线先留在店主和店长的私聊里；经过选择的公开目标、候选条款与进度进入协作会话；专业角色收到完成本职任务所需的条件，再把判断带回来。","同样一条更新，对某个角色可能意味着要行动，对其他角色只是需要知情。产品应让用户理解这些区别，并在共享或授权发生变化时清楚说明影响。","技术备答，不在主讲展开：第一层是会话范围。群组组织一组参与者，Session 表达具体任务的上下文范围。历史读取会检查调用者与目标群组、会话的访问关系。","技术备答，不在主讲展开：第二层是角色路由。普通聊天会为符合接收条件的参与者区分 Send 和 Inject：前者触发响应，后者只是补充知情上下文。被提及的角色或协调者通常承担响应，其余接收者无需同时回答。","技术备答，不在主讲展开：任务协作群采用更明确的边界：ManagerWorker 模式下，非 Lead 的 Worker 被排除在普通群广播外，任务通过 bcs_assign_task 定向投递。店长仍须选择任务中应携带哪些事实，路由本身不会自动判断哪些经营数据可以公开。","技术备答，不在主讲展开：第三层是引擎侧映射。BCN 插件携带会话 ID、发起者、提及对象与接收角色等元数据；OpenClaw 入口优先使用规范的 bcs_session_id 作为独立会话标识，旧消息帧保留群组回退兼容。这里讲可核对的实现机制，不扩展成所有引擎与所有通道的绝对隔离保证。"], sources: ["routing","history","session","context"]
  },
  {
    title: "把分歧呈现出来，用户才能做决定", section: "上下文设计", duration: 105, theme: "light", layout: "context-fusion",
    html: "<p class=\"eyebrow\">上下文 / 多方视角融合</p><h2>把分歧呈现出来，<br>用户才能做决定</h2><div class=\"fusion-inputs\"><span>营销 / 希望扩大活动规模</span><span>数据 / 需要校验门店产能</span><span>供应 / 需要确认到货时间</span></div><div class=\"fusion-body\"><div class=\"fusion-service\"><span>围绕同一个目标</span><h3>汇总多方观点</h3><p>保留谁提出、依据是什么<br>区分共识与待决问题</p><small>为下一步协商提供输入</small></div><div class=\"fusion-outputs\"><div><b>已经对齐什么</b><span>共同认可的条件与事实</span></div><div><b>还有什么分歧</b><span>各方约束及其影响</span></div><div><b>接下来决定什么</b><span>可选方案与需要确认的人</span></div></div></div><p class=\"source-caption\">小店场景的协作信息示意。</p>",
    notes: ["假设营销希望把活动做大，数据提醒服务产能有限，供应侧还要确认交期。一个看起来完整的总结，可能会掩盖这些尚未解决的分歧。","产品应当保留观点来源和依据，把已经形成的共识、尚未解决的冲突与下一步决策分别呈现。用户才知道哪些事已经可靠、哪些需要调整、哪些必须自己决定。","Avernet 的上下文融合能力为这种表达提供结构化材料。主讲停留在观点、共识、分歧与建议的关系，具体实现仅在被问到时展开。","技术备答，不在主讲展开：Avernet 的融合请求可以指定问题、参与者、关注点与会话。输出契约保留 perspectives、conflicts、alignment、recommendation 和 key insights，视角还可以附带角色、状态与证据。这样的结构便于协作流程继续处理分歧。","技术备答，不在主讲展开：实现上，组合入口可按配置选择 LocalFusionService，或连接 BCSFuse 的 FuseBackedFusionService。本地实现支持从配置的 Bot 上下文目录取得材料；基础回退主要整理上下文，较丰富的分析依赖对应实现和模型能力。","技术备答，不在主讲展开：融合后的建议仍然是决策输入。接下来由参与者协商、由授权角色作出决定，并在执行中验收。融合不改变原本的信息访问边界，也不会自动替各方作出承诺。"], sources: ["fusion","fusionContract"]
  },
  {
    title: "协作产品的能力地图", section: "能力与设计", duration: 90, theme: "dark", layout: "capability",
    html: "<p class=\"eyebrow\">产品设计</p><h2>协作产品的能力地图</h2><div class=\"capability-map\"><div><span>01</span><h3>身份 / 发现 / 关系</h3><p>谁可以成为协作者</p></div><div><span>02</span><h3>上下文 / 授权</h3><p>谁能看见、决定什么</p></div><div><span>03</span><h3>协商 / 契约</h3><p>如何形成可验收的承诺</p></div><div><span>04</span><h3>执行 / 验收</h3><p>如何推进并确认结果</p></div><div class=\"future-capability\"><span>05</span><h3>经验沉淀</h3><p>哪些方法值得复用</p><small>产品演进方向</small></div></div><p class=\"source-caption\">能力地图用于产品设计；具体开放状态以 Avernet 仓库说明为准。</p>",
    notes: ["把概要中的十项能力按用户交付过程归为五组。身份、发现和关系回答谁能合作。上下文和授权回答合作时能看到和决定什么。","协商和契约形成可以执行的约定。执行和验收使产品能够报告真实状态。经验沉淀则考虑哪些方法可以复用、复用前如何验证。","这是一张产品设计地图，不能把它当成当前版本的功能清单。尤其经验沉淀、组织记忆和持续进化，在这次分享中作为演进方向与待验证机制讨论。"], sources: ["overview","arch"]
  },
  {
    title: "人的位置由任务决定", section: "能力与设计", duration: 60, theme: "blue", layout: "human",
    html: "<p class=\"eyebrow\">人机共同参与</p><h2>人的位置<br>由任务决定</h2><div class=\"human-roles\"><div><b>目标提出者</b><p>说明希望改变的结果</p></div><div><b>授权与决策者</b><p>设定范围，处理关键取舍</p></div><div><b>参与者与验收者</b><p>提供必要输入，确认交付</p></div></div>",
    notes: ["在小店中，人主要是目标提出者、授权者和验收者。游戏中，人直接是行动主体，发言和投票都由人自己完成。","同一个产品可以让人承担多种位置。设计时需要明确用户何时必须参与、如何参与、退出或超时后如何处理。","减少人工协调，并不意味着移除人的决策。重要的是让参与发生在有意义的节点，而不是让用户到处复制消息和追问进度。","接下来三页沿着人的参与过程展开：委派前说清目标与授权，协作中理解进度与待决事项，关键节点由人作出决定。"], sources: ["shop","game"]
  },
  {
    title: "用户委派目标，需要说清什么？", section: "协作体验设计", duration: 150, theme: "light", layout: "delegation-design",
    html: "<p class=\"eyebrow\">产品设计 01 / 目标与授权</p><h2>用户委派目标，<br>需要说清什么？</h2><div class=\"delegation-grid\"><div class=\"delegation-voice\"><span>用户先说目标</span><blockquote>下个月店庆，<br>帮我多来些客人。</blockquote><p>从一句自然表达开始，<br>逐步形成可执行的委派。</p></div><div class=\"delegation-questions\"><div><span>先对齐</span><h3>目标、时间与完成条件</h3><p>这次交付什么，怎样算完成？</p></div><div><span>按需追问</span><h3>预算底线与调整空间</h3><p>哪些条件会改变接下来的方案？</p></div><div><span>执行前确认</span><h3>行动范围与关键授权</h3><p>什么可以自主推进，什么需要问我？</p></div></div></div><p class=\"source-caption\">交互设计示意 · 每次追问都应帮助推进下一步决策。</p>",
    notes: ["接下来用三个产品设计问题，把前面的 Demo 转成听众可以带走的方法。第一个问题是委派入口。用户通常先说愿望，产品需要逐步把愿望整理成可以推进和验收的目标。","沿用店庆场景：先确认活动时间、品质要求、目标优先级，以及这一次要交付什么。用户说的是增长愿望，但当前 Demo 的交付是经过复核的经营 SOP，二者需要在开始时说清。","追问应发生在答案会影响下一步时。例如确定预算与毛利底线会改变优惠方案，就值得此时追问；后续才需要的素材细节可以晚些补齐。每个问题最好说明用途，让用户知道为什么要回答。","执行前再确认行动范围：哪些方案调整可以自主完成，哪些资源承诺、对外动作和范围变化需要用户决定。用户应能回看并修改已确认的约束。","本页是基于 Demo 提炼的交互设计建议，不是对当前界面功能的逐项描述。可停顿片刻，请听众想一个自己的任务：哪一个问题必须先问，哪一个可以后问？"], sources: ["shop"]
  },
  {
    title: "团队在工作，用户应该看见什么？", section: "协作体验设计", duration: 180, theme: "dark", layout: "visibility-design",
    html: "<p class=\"eyebrow\">产品设计 02 / 进度与决策</p><h2>团队在工作，<br>用户应该看见什么？</h2><div class=\"visibility-grid\"><div class=\"work-status\"><div class=\"status-heading\"><b>18 周年店庆</b><span>候选方案 v2</span></div><div class=\"status-row\"><i class=\"status-done\"></i><b>经营目标与约束</b><span>已确认</span></div><div class=\"status-row\"><i class=\"status-working\"></i><b>活动资源与条款</b><span>协商中</span></div><div class=\"status-row\"><i></i><b>最终经营 SOP</b><span>待验收</span></div><div class=\"decision-preview\"><span>需要你决定 / 门店承接能力</span><h3>增加服务时段，<br>还是调整活动规模？</h3><p>展示两种选择的影响与建议理由。</p></div></div><div class=\"visibility-prompts\"><div><span>现在到哪了</span><h3>阶段与下个里程碑</h3></div><div><span>为什么停在这里</span><h3>约束、责任人与影响</h3></div><div><span>需要我做什么</span><h3>选项、代价与确认入口</h3></div></div></div><p class=\"source-caption\">交互设计示意 · 让用户看懂进展，并在需要时介入。</p>",
    notes: ["第二个问题是协作过程的可见性。用户把目标交给团队之后，需要知道任务有没有推进、当前卡在哪里，以及自己什么时候需要参与。","画面左边是设计示意，不是真实运行截图。把协作整理成已确认的目标、正在协商的资源与条款、等待验收的结果。阶段旁边还应能找到负责角色与相关证据；需要时再展开具体讨论。","以门店产能不足为例，用户需要知道这会影响什么，以及可选择增加服务时段还是缩小活动规模。每个选项应说明成本、影响、仍然不确定的地方和建议理由，然后给出明确的确认方式。","请听众设想只看这个界面十秒钟，能否回答右边三个问题。若必须逐条读完 Agent 对话才能理解任务状态，说明还有可以改进的信息组织工作。","进度表达应忠于事实。方案被认可、外部动作完成、业务目标实现，是不同状态。小店的 SOP 验收之后，真实投券、采购等动作仍然需要外部执行证据。"], sources: ["shop","game"]
  },
  {
    title: "什么时候，应该把决定交回给人？", section: "协作体验设计", duration: 180, theme: "light", layout: "human-decision",
    html: "<p class=\"eyebrow\">产品设计 03 / 人的介入与决定</p><h2>什么时候，<br>应该把决定交回给人？</h2><table class=\"decision-table\"><thead><tr><th>需要人介入的时刻</th><th>Agent 应带回什么</th><th>人作出什么决定</th></tr></thead><tbody><tr><th>信息只有人知道</th><td>具体问题，以及对任务的影响</td><td>补充信息 / 确认假设</td></tr><tr><th>目标与约束冲突</th><td>可选方案，以及各自的代价</td><td>排定优先级 / 调整目标</td></tr><tr><th>下一步超出授权</th><td>拟采取的行动，以及预期影响</td><td>批准 / 修改 / 停止</td></tr></tbody></table><p class=\"decision-principle\">Agent 在授权内持续推进，人在有意义的节点作出决定。</p><p class=\"source-caption\">交互设计建议 · 请求人的决定时，带回足以判断的信息。</p>",
    notes: ["这一页承接“人机共同参与”：委派前，人表达目标并设定授权；协作中，人了解进展并保持掌控；到了关键节点，Agent 把需要人判断的决定交回来。重点是介入的时机与质量。","第一类是只有人知道的信息。例如店主还没有明确促销预算或哪些服务时段能够调整。Agent 应说明具体缺口及其对方案的影响，请人补充，或明确确认一个必要假设。已能在现有上下文中取得的信息，应由 Agent 先自行整理。","第二类是目标与约束无法同时满足。例如既希望扩大活动规模，又不能增加服务时段，现有产能就可能成为限制。Agent 应把可选方案、受影响的目标和各自代价带回来，由人排定优先级或调整目标与边界。","第三类是下一步超出已有授权。例如原本只被授权形成经营方案，下一步准备对外承诺资源或实际下单。Agent 应先说明拟采取的行动和影响，等待有权决定的人批准、修改或停止，再在新的授权范围内推进。","可以请听众回到自己的产品，找出一个必须由人决定的节点：此时界面是否已经提供足以判断的选项、依据和影响？用户作出选择后，产品还应记录决定、更新边界，并清楚说明团队将如何继续。此页是交互设计建议，不是当前版本的完整功能清单。","备答：普通技术故障可在已有授权和明确重试条件内处理。只有涉及新的业务取舍、额外授权或人工接管时，才需要把相应决定交给人；这类细节不在本页主讲展开。"], sources: ["shop","game"]
  },
  {
    title: "一张协作任务设计检查表", section: "能力与设计", duration: 90, theme: "light", layout: "checklist",
    html: "<p class=\"eyebrow\">带回自己的产品</p><h2>一张协作任务设计检查表</h2><ol class=\"design-checklist\"><li><span>目标</span><strong>哪一个结果值得用户委派？</strong></li><li><span>角色</span><strong>哪些能力和主体必须参与？</strong></li><li><span>边界</span><strong>各方能看到、决定和承诺什么？</strong></li><li><span>过程</span><strong>哪些步骤确定，哪些需要协商？</strong></li><li><span>完成</span><strong>用什么证据，由谁来验收？</strong></li></ol>",
    notes: ["结合刚才的委派、进度和介入时机设计，给听众一张可用于产品讨论的检查表。用一个自己的运营任务快速回答这五个问题。","目标要有可观察的完成条件。角色要由任务和主体边界决定。边界同时包含信息和行动权限。过程区分可以预设的步骤和必须协商的事项。完成需要证据和验收者。","答案明确后，就可以继续设计角色、输入、状态展示、决策节点和验收方式。接下来简要介绍亲手体验 Avernet 的入口。"], sources: []
  },
  {
    title: "5 分钟部署 Avernet", section: "亲手体验", duration: 120, theme: "blue", layout: "quickstart-product",
    html: "<p class=\"eyebrow\">从产品体验，到亲手运行</p><h2>5 分钟部署 Avernet</h2><p class=\"quickstart-sub\">把刚才的协作带回自己的电脑。</p><div class=\"quickstart-grid\"><div class=\"quickstart-path\"><div><span>01</span><h3>准备模型配置</h3></div><div><span>02</span><h3>启动完整环境</h3></div><div><span>03</span><h3>选择场景角色</h3></div></div><div class=\"quickstart-code\"><span>在仓库根目录运行</span><pre><code>./scripts/singlebox.sh install-tools\n./scripts/singlebox.sh start</code></pre><p>打开工作台，体验小店经营或谁是卧底。</p></div></div><p class=\"source-caption\">“5 分钟”为上手引导，非安装耗时保证；首次下载与编译可能更久。配置与场景命令见附录。</p>",
    notes: ["主讲只用两分钟介绍上手入口，传达听众可以把前面的体验带回去尝试。保留“5 分钟部署 Avernet”作为体验章节标题；首次工具下载和编译的实际耗时取决于环境与网络，不在台上等待完整安装。","在已获取的仓库根目录执行 install-tools，再执行不带服务名的 start，即可按配置启动完整本地环境。准备可用的模型配置，再按教程载入小店或游戏的场景角色。","这里展示两条命令即可，不展开模块和拓扑。详细安装、profile 命令、Singlebox 与微服务说明都在问答后的附录。建议听众从一个已经熟悉的业务目标开始体验，然后用前面的检查表判断怎样设计自己的协作。","下一部分回到产品价值：怎样判断这种协作值得持续使用，以及怎样形成增长。"], sources: ["singlebox","services","shop","game"]
  },
  {
    title: "协作网络的增长路径", section: "产品增长", duration: 90, theme: "dark", layout: "growth",
    html: "<p class=\"eyebrow\">待验证的产品假设</p><h2>协作网络的增长路径</h2><div class=\"growth-stair\"><div><span>01</span><b>专业能力更丰富</b></div><div><span>02</span><b>可承接的目标扩大</b></div><div><span>03</span><b>成功交付建立信任</b></div><div><span>04</span><b>用户愿意继续委派</b></div><div><span>05</span><b>关系与有效方法积累</b></div></div><p class=\"source-caption\">需要用实际交付与持续使用数据验证，Agent 数量本身不保证增长。</p>",
    notes: ["回到课程标题里的 AI 运营和增长。提出一条可以验证的路径：可用专业能力增加，可承接目标扩大；成功交付建立信任，用户愿意继续委派，关系和有效方法逐步积累。","这是一条产品假设。Agent 更多不自动意味着交付更好，协调成本、质量与信任都可能成为约束。经验复用也需要验证适用范围。"], sources: []
  },
  {
    title: "增长的证据来自交付和再次委派", section: "产品增长", duration: 90, theme: "light", layout: "metrics",
    html: "<p class=\"eyebrow\">可观察的产品指标</p><h2>增长的证据来自<br>交付和再次委派</h2><div class=\"metric-columns\"><div><span>交付</span><h3>目标验收率</h3><p>有多少委派达到<br>事先约定的完成条件</p></div><div><span>过程</span><h3>人工协调成本</h3><p>用户需要介入多少次<br>花多少时间追问与协调</p></div><div><span>持续使用</span><h3>再次委派与复用</h3><p>用户是否再次交付目标<br>团队与方法能否复用</p></div></div>",
    notes: ["用三个观察维度衡量价值。交付看目标是否达到事先约定的验收条件。过程看用户为协调任务付出的时间与介入次数。持续使用看用户是否再次委派，以及团队和方法能否有效复用。","这里没有给出未经验证的增长数字。指标的口径需要和业务目标共同定义。"], sources: []
  },
  {
    title: "产品开始承担组织工作的责任", section: "产品增长", duration: 90, theme: "blue", layout: "closing",
    html: "<p class=\"eyebrow\">当产品开始替用户组队</p><h2>用户委派一个目标<br>产品组织专业角色<br>共同交付可验收的结果</h2><p class=\"closing-sub\">Avernet 的实践仍在继续。</p><a class=\"repo-link\" href=\"https://github.com/inclusionAI/Avernet\" target=\"_blank\" rel=\"noopener\">github.com/inclusionAI/Avernet ↗</a>",
    notes: ["最后回扣开场的店主。产品开始替用户组队之后，设计对象扩展到了角色之间的关系、权限、承诺和交付过程。","今天的两个场景展示了经营协商与人机共同参与。两层判断框架帮助选择参与者和过程形态；上下文设计明确各方的信息范围；委派、进度和介入时机设计帮助把协作变成用户能理解和控制的体验。","正式分享到此结束，总计 50 分钟。邀请听众用自己的任务尝试复现和设计，下一页进入额外问答时间。"], sources: ["overview"]
  },
  {
    title: "谢谢，欢迎交流", section: "问答", duration: 0, theme: "dark", layout: "resources",
    html: "<p class=\"eyebrow\">致谢与交流 / Q & A</p><h2>谢谢，欢迎交流</h2><p class=\"discussion-prompt\">Agent 协作的产品设计与实践</p><div class=\"resource-links\"><a href=\"https://github.com/inclusionAI/Avernet\" target=\"_blank\" rel=\"noopener\"><span>01</span><b>Avernet 开源仓库</b><small>产品定位与能力状态 ↗</small></a><a href=\"https://github.com/inclusionAI/Avernet/blob/dev/docs/small-shop-dream-team-live-demo-tutorial.zh-CN.md\" target=\"_blank\" rel=\"noopener\"><span>02</span><b>小店经营示例</b><small>目标、协商与店主验收 ↗</small></a><a href=\"https://github.com/inclusionAI/Avernet/blob/dev/docs/undercover-game-tutorial.zh-CN.md\" target=\"_blank\" rel=\"noopener\"><span>03</span><b>谁是卧底示例</b><small>角色、规则与人机参与 ↗</small></a></div><p class=\"source-caption\">安装与架构资料位于附录，可在总览中按需选择。</p>",
    notes: ["本页是正式分享的致谢与问答页，位于第 33 页总结之后、第 35–39 页附录之前。感谢听众后，停留在本页进入交流；问答不计入 50 分钟。可讨论自己的业务是否需要多 Agent、如何设计委派入口、协作进度和关键决策，以及如何判断真实交付。如果听众关注安装或架构，可按 O 在总览中进入后面的五页附录。回答完相关问题后回到本页，结束交流时也停留在这里。","小店截图来自历史 Demo，复核和店主验收图仍处于等待人工输入状态；游戏截图来自真实副屏组件的演示数据。异构引擎图用于解释协作关系。","演示操作：左右键或空格翻页，F 全屏，O 总览，N 打开独立讲者窗口，B 黑屏，? 查看快捷键。讲者窗口可开始或暂停计时，翻页与主窗口同步。"], sources: ["overview","shop","game"]
  },
  {
    title: "两条命令，启动完整 Singlebox", section: "附录", duration: 0, theme: "dark", layout: "terminal",
    html: "<p class=\"eyebrow\">附录 / 安装运行 / 仓库根目录</p><h2>两条命令，启动完整 Singlebox</h2><div class=\"terminal-layout\"><pre><code><span class=\"code-comment\"># 1. 安装运行所需工具</span>\n./scripts/singlebox.sh install-tools\n\n<span class=\"code-comment\"># 2. 启动完整本地环境</span>\n./scripts/singlebox.sh start</code></pre><div><h3>准备模型配置</h3><p>服务地址<br>API Key<br>模型 ID</p><h3>打开工作台</h3><p>127.0.0.1:8000</p></div></div><p class=\"source-caption\">默认启动完整服务栈；真实 Agent 回复需要可用的模型配置。</p>",
    notes: ["仅在问答中按需展开，不计入 50 分钟主讲。","进入仓库根目录，先执行 install-tools，再执行不带服务名的 start。singlebox 的默认服务集合是 all，会准备并启动完整本地环境。","完整环境包括 BaaS、Backend、BCSFuse、BCS、Bot 与前端等服务，由脚本处理启动次序和就绪检查。具体启用项仍遵循本机配置。","提前准备服务地址、API Key 与模型 ID，或者复用本机已可用的模型配置。完成后打开工作台，确认服务就绪和模型可用。下一页加载小店或游戏的场景角色。"], sources: ["singlebox","services","start"]
  },
  {
    title: "一套底座，两组场景角色", section: "附录", duration: 0, theme: "dark", layout: "profiles",
    html: "<p class=\"eyebrow\">附录 / 场景配置</p><h2>一套底座，两组场景角色</h2><div class=\"profile-commands\"><div><span>小店经营 / 4 BOT</span><pre><code>./scripts/singlebox.sh start bots \\\n  --profile-dir scripts/4bots_merchant_operations_profile</code></pre></div><div><span>谁是卧底 / 6 BOT</span><pre><code>./scripts/singlebox.sh start bots \\\n  --profile-dir scripts/6bots_undercover_game_profile</code></pre></div></div><div class=\"verification-strip\"><span>服务健康</span><span>Bot 注册成功</span><span>真实模型回复</span><span>进入协作场景</span></div><p class=\"source-caption\">完整准备步骤、模型选择及状态检查见各 Demo 教程。</p>",
    notes: ["仅在问答中按需展开，不计入 50 分钟主讲。","profile 描述一组场景角色及其配置。小店经营有四个 Bot，游戏有六个 Bot。两者使用同一套 BCS 与前端能力。","按照各自教程准备后，执行对应命令启动。不要把 Running 当成完整可用：还要检查 Bot onboard 成功、真实模型可以回复，最后进入协作验证。","现场不清理或重置数据库。切换演示使用已准备的群与会话。一般排障先看 status、健康检查和对应日志。"], sources: ["shop","game"]
  },
  {
    title: "Singlebox：一台机器上的服务协作", section: "附录", duration: 0, theme: "light", layout: "singlebox",
    html: "<p class=\"eyebrow\">附录 / 运行形态</p><h2>Singlebox</h2><p class=\"section-sub\">一台机器统一编排多个服务，保留职责边界。</p><div class=\"deployment-box\"><span class=\"box-label\">本地机器</span><div class=\"service-grid\"><div><b>Frontend</b><p>用户交互与过程展示</p></div><div><b>Backend</b><p>用户资产、绑定与元数据</p></div><div class=\"bcs-service\"><b>BCS</b><p>发现、关系、路由与协作执行</p></div><div><b>BCSFuse</b><p>多方上下文融合</p></div><div><b>BaaS / Runtime</b><p>Bot 生命周期与运行能力</p></div></div><div class=\"local-bots\">场景 Bot 与本地运行资源</div></div><p class=\"source-caption\">完整 Singlebox 的职责示意；默认 start 统一准备并启动本地服务栈。</p>",
    notes: ["仅在问答中按需展开，不计入 50 分钟主讲。","Singlebox 是部署和开发组织方式：在一台机器上统一准备和管理完整服务栈，代码和契约边界依然存在。","Frontend 承载交互。Backend 负责用户资产、绑定和元数据。BCS 负责发现、关系、消息路由和协作执行。BCSFuse 提供多方上下文融合能力，BaaS 与 Engine Runtime 承担 Bot 生命周期和运行能力。","刚才的两条命令对应这个完整本地环境。场景 profile 则在环境上载入具体角色。Singlebox 适合演示、本地开发、联调与完整用户链路验证。"], sources: ["singlebox","services","arch","fusion"]
  },
  {
    title: "微服务：按职责与负载分别部署", section: "附录", duration: 0, theme: "dark", layout: "distributed",
    html: "<p class=\"eyebrow\">附录 / 部署演进</p><h2>按职责与负载<br>分别部署</h2><div class=\"distributed-map\"><div class=\"deployment-zone\"><span>交互与业务服务</span><b>Frontend / Backend</b><p>用户、资产、业务入口</p></div><div class=\"deployment-zone highlight\"><span>协作服务</span><b>BCS</b><p>连接、路由、协作状态</p></div><div class=\"deployment-zone\"><span>运行环境</span><b>Agent Runtime</b><p>不同设备与运行资源</p></div></div><div class=\"infra-list\"><span>身份与鉴权</span><span>连接路由</span><span>持久化</span><span>故障处理</span><span>可观测性</span></div><p class=\"source-caption\">部署职责示意；具体拓扑与扩容能力取决于实现和运行环境。</p>",
    notes: ["仅在问答中按需展开，不计入 50 分钟主讲。","当职责和负载需要分别管理时，可以讨论服务独立部署。不同 Agent 也可以运行在不同设备或运行环境中。","跨进程之后要处理服务地址与身份鉴权、长连接和消息路由、状态持久化、超时与故障、追踪与可观测性。不要将部署拆分直接等同于所有服务已具备任意水平扩展能力。","跨组织协作还需要明确身份、授权、协议和责任，物理分布只解决其中一部分。这里讲设计边界，具体公开实现和企业环境依赖以仓库文档为准。"], sources: ["arch","overview"]
  },
  {
    title: "部署形态与代码结构分别设计", section: "附录", duration: 0, theme: "light", layout: "architecture",
    html: "<p class=\"eyebrow\">附录 / 架构边界</p><h2>部署形态与代码结构<br>分别设计</h2><div class=\"architecture-rows\"><div><span>服务放在哪里</span><h3>Singlebox / 微服务</h3><p>机器、进程、资源与运行边界</p></div><div><span>服务内部如何组织</span><h3>核心 / 契约 / 插件</h3><p>稳定的职责与可替换的实现</p></div></div><p class=\"bottom-line\">Service API 承接调用，Plugin API 定义核心所需能力，组合入口选择实现。</p>",
    notes: ["仅在问答中按需展开，不计入 50 分钟主讲。","澄清两个维度。Singlebox 与微服务讨论服务如何部署；核心、契约和插件讨论服务内部如何组织。","Avernet 的架构规则区分 Service API 与 Plugin API。前者承接消费者对核心的调用，后者定义核心需要外部实现提供的能力。具体实现由组合入口按配置装配。","契约测试守住接口行为，完整链路验收检查用户故事。回到产品层，这些边界支撑可扩展的协作能力。"], sources: ["arch"]
  }
];
