/**
 * SPM 语义点位常量表（add-spm-tracking,P0 批次）。
 *
 * == 命名与存放约定（务必遵守，测试 test/tracking/points.test.ts 会防泄漏扫描）==
 *
 * 1. 常量值 = antlog 平台注册的中文点位名（组名按「页面-操作」书写；模块区段在平台侧
 *    由 C 位承载，码表中的 c 段即对应），绝不是 SPM 码——任何 a/b/c/d 位码值
 *    禁止出现在开源侧源码；真实码表由 internal overlay（src/internal/tracking/spmMap）
 *    维护，经 capabilities.trackerAdapter 解析后上报。
 * 2. 结构 = 页面命名空间 → 点位常量。新增点位时同批次完成三件事（缺一不可，禁止码空挂）：
 *      a. antlog 需求内申请成功拿码；
 *      b. 本表登记常量 + overlay spmMap 登记名→码；
 *      c. 页面调用点同批合入。
 * 3. P0 范围：一级导航、页面主 CTA、关键转化漏斗、核心区块曝光（源清单由拓界确认）。
 *    P1（memberAction / notify.openItem / capabilityWorkshop.openItem 等低粒度点）后移。
 * 4. 扩展参数随点位在平台注册：bot_id / group_id / task_id / domain_id / skill_code /
 *    mcp_code / ticket_id / nav_target / tab——调用处按点位携带。
 */

/**
 * 全部语义点位常量。叶子值 = 中文点位名，消费方传给
 * `Tracert.click/expo`（src/utils/tracert.ts）。
 */
export const Points = {
  // —— 全局 Shell（侧栏；物理挂「工作台-我的对话」页 B 位，跨页面采集由运行时 B 位拼装）——
  shell: {
    /** 全局-侧栏-一级导航点击（params: nav_target） */
    navClick: '全局-侧栏-一级导航点击',
    spaceSwitchOpen: '全局-侧栏-打开空间切换器',
    spaceSwitchSelect: '全局-侧栏-选择空间',
    globalSearchOpen: '全局-侧栏-打开全局搜索',
    notifyOpen: '全局-侧栏-打开通知中心',
  },
  // —— 工作台-我的对话（/workspace/chat）——
  workspaceChat: {
    newSession: '工作台-我的对话-新建会话',
    /** params: bot_id */
    sendMessage: '工作台-我的对话-发送消息',
    /** params: bot_id */
    switchSession: '工作台-我的对话-切换会话',
    openWorkflowMenu: '工作台-我的对话-打开工作流菜单',
    engineUpdateEntry: '工作台-我的对话-点击引擎更新入口',
    emptyGuideExpo: '工作台-我的对话-空状态引导曝光',
  },
  // —— 工作台-我的协作（/workspace/collaboration）——
  workspaceCollab: {
    createGroup: '工作台-我的协作-创建协作群',
    /** params: group_id */
    openGroup: '工作台-我的协作-打开群聊',
  },
  // —— 协作群聚焦页（/workspace/collaboration-only）——
  groupFocus: {
    /** params: group_id */
    sendMessage: '协作群聚焦页-发送消息',
    /** params: group_id */
    inviteMember: '协作群聚焦页-邀请成员',
  },
  // —— 我的任务（/work/my-task）——
  myTask: {
    /** params: task_id */
    openDetail: '我的任务-打开任务详情',
    /** params: task_id */
    claim: '我的任务-认领任务',
  },
  // —— 邀请加入页（/workspace/invite）——
  invite: {
    accept: '邀请加入页-接受邀请',
  },
  // —— 协作广场（/collaboration-square/*）——
  collabSquare: {
    search: '协作广场-搜索',
    /** params: bot_id */
    openBotCard: '协作广场-查看Bot详情',
    /** params: group_id */
    openGroupCard: '协作广场-查看群聊',
    /** params: group_id */
    joinGroup: '协作广场-加入群聊',
    heroExpo: '协作广场-Hero区曝光',
  },
  // —— 实验室（/lab）——
  lab: {
    openTasks: '实验室-进入任务',
    openCommunity: '实验室-进入社区',
    cardsExpo: '实验室-功能卡曝光',
  },
  // —— 智能进化（/evolution-factory/*）——
  evolution: {
    entryCardsExpo: '智能进化首页-入口卡曝光',
    createEntry: '智能进化首页-点击创建任务',
    createSubmit: '创建进化任务-创建任务提交',
    /** params: task_id */
    runTask: '进化任务详情-启动运行',
    /** params: task_id */
    openBenchRun: '进化任务详情-查看评测运行',
    /** params: domain_id */
    openDomain: '进化领域详情-进入领域',
    /** params: task_id */
    viewBenchReport: '评测运行详情-查看报告',
  },
  // —— Bot 管理（/bot-workshop/*，核心转化漏斗）——
  botWorkshop: {
    createEntry: 'Bot管理-点击创建Bot入口',
    createLocal: 'Bot管理-创建本地Bot',
    createCloud: 'Bot管理-创建云端Bot',
    createSubmit: 'Bot管理-创建Bot提交',
    /** params: bot_id */
    openDetail: 'Bot管理-打开Bot详情',
    /** params: bot_id */
    saveConfig: 'Bot管理-保存通用配置',
    /** params: bot_id */
    publishPre: 'Bot管理-发布预发',
    /** params: bot_id */
    publishOnline: 'Bot管理-发布上线',
    /** params: bot_id */
    viewLogs: 'Bot管理-查看日志',
    /** params: bot_id */
    viewTaskInstance: 'Bot管理-查看任务实例',
    /** params: bot_id */
    runHealthCheck: 'Bot管理-运行健康检查',
  },
  // —— 空间管理（/space-admin）——
  spaceAdmin: {
    addMember: '空间管理-添加成员',
  },
  // —— 工单中心（/ticket-center）——
  ticketCenter: {
    /** params: ticket_id */
    handleWorkOrder: '工单中心-处理工单',
  },
  // —— 市场（/market/*，内部 overlay 页面共用语义键）——
  market: {
    /** params: skill_code */
    openSkill: 'Skill市场-查看Skill详情',
    /** params: skill_code */
    applySkill: 'Skill市场-申请Skill',
    bannerExpo: 'Skill市场-运营位曝光',
    /** params: mcp_code */
    openMcp: 'MCP市场-查看MCP详情',
    /** params: mcp_code */
    connectMcp: 'MCP市场-接入MCP',
  },
  // —— 能力工厂（/capability-workshop，内部 overlay 页面）——
  capabilityWorkshop: {
    /** params: tab(skill|card) */
    switchTab: '能力工厂-切换页签',
  },
} as const;