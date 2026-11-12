export const examples = {
  card: {
    name: '数据分析助手',
    description: '接收结构化数据，提供汇总与分析。',
    version: '1.0.0',
    url: 'http://localhost:8080/a2a',
    protocolBinding: 'JSONRPC',
    capabilities: { streaming: false, pushNotifications: false },
    defaultInputModes: ['application/json'],
    defaultOutputModes: ['application/json'],
    skills: [{
      id: 'analyze-data',
      name: '数据分析',
      description: '分析输入数据并返回结构化结果。',
      tags: ['data', 'analysis'],
      examples: ['分析最近一周的访问量']
    }]
  },
  message: {
    action: 'analyze',
    question: '汇总这三天的访问量，并找出最高的一天。',
    data: [
      { date: '2026-11-08', visits: 128 },
      { date: '2026-11-09', visits: 256 },
      { date: '2026-11-10', visits: 192 }
    ],
    options: { language: 'zh-CN', includeSummary: true }
  }
};

export const fragmentExamples = {
  card: '"name": "数据分析助手",\n"description": "接收并分析 JSON 数据",\n"version": "1.0.0",\n"url": "http://localhost:8080/a2a",\n"skills": [{"id":"analyze","name":"分析","description":"分析数据","tags":["data"]}],',
  message: 'userId=123456, sessionId=session_demo, queryId=query_demo,\nmessage=Message(kind=message, role=USER,\n  parts=[TextPart(kind=text, itemId=item_demo, content=想去杭州玩两天,\n    extParams={"scene":"chat"}, metadata={"source":"example"})],\n  messageId=msg_demo, taskId=task_demo),\nagentId=travel_agent, metadata={"query":"想去杭州玩两天"}'
};

export const demoExamples = {
  message: [
    {
      id: 'message-structured',
      title: '结构化消息 JSON',
      description: 'content 转为 text，sessionId 转为 contextId，业务字段保留到 metadata。',
      input: JSON.stringify({
        userId: 'user_demo',
        sessionId: 'session_demo',
        message: {
          role: 'USER',
          messageId: 'message_demo',
          sessionId: 'session_demo',
          parts: [{
            kind: 'text',
            content: '请汇总这三天的访问量。',
            itemId: 'item_demo',
            metadata: { source: 'demo' }
          }],
          metadata: { channel: 'web' }
        },
        metadata: { scenario: 'demo' }
      }, null, 2)
    },
    {
      id: 'message-fragment',
      title: 'JSON 字段片段',
      description: '补齐外层对象，文本转为 text，会话标识转为 contextId。',
      input: '"role": "USER",\n"sessionId": "session_demo",\n"parts": [{"content":"请分析这份示例数据。","itemId":"item_demo"}],\n"metadata": {"source":"fragment-demo"}'
    },
    {
      id: 'message-object-dump',
      title: 'Message 对象打印',
      description: '识别 Message / TextPart，提取文本与会话标识，保留业务字段。',
      input: fragmentExamples.message.replace(', taskId=task_demo', '')
    },
    {
      id: 'message-data',
      title: '业务 JSON',
      description: '将整份业务 JSON 原样放入消息的 data 部分。',
      input: JSON.stringify(examples.message, null, 2)
    }
  ],
  card: [
    {
      id: 'card-simple',
      title: '简化 Agent 配置',
      description: 'url 转为 supportedInterfaces，保留技能和能力配置。',
      input: JSON.stringify(examples.card, null, 2)
    },
    {
      id: 'card-fragment',
      title: 'JSON 字段片段',
      description: '补齐外层对象，将接口地址整理为 A2A 1.0 AgentCard。',
      input: fragmentExamples.card
    },
    {
      id: 'card-complete',
      title: '完整 AgentCard',
      description: '检查已有 supportedInterfaces，直接输出 A2A 1.0 AgentCard。',
      input: JSON.stringify({
        name: examples.card.name,
        description: examples.card.description,
        version: examples.card.version,
        supportedInterfaces: [{
          url: examples.card.url,
          protocolBinding: examples.card.protocolBinding,
          protocolVersion: '1.0'
        }],
        capabilities: examples.card.capabilities,
        defaultInputModes: examples.card.defaultInputModes,
        defaultOutputModes: examples.card.defaultOutputModes,
        skills: examples.card.skills
      }, null, 2)
    }
  ]
};
