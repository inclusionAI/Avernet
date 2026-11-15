/**
 * A ready-made example a user can load from the config form.
 *
 * Three Bot agents split a real reporting task by discipline and run in
 * parallel — the shape Agent Compute exists for. Nothing here is loaded by
 * default: an empty form is the starting point, and the example is opted into.
 */
export interface AgentExample {
  id: string;
  title: string;
  summary: string;
  goal: string;
  agents: Array<{ name: string; role: string; instructions: string }>;
}

export const AGENT_EXAMPLES: readonly AgentExample[] = [
  {
    // Mirrors the backend E2E suite's --multi case, which is known to complete
    // end to end: three agents in parallel, one word each.
    id: 'hello-brave-world',
    title: '三 Agent 并行拼词（验证可用）',
    summary: '三个 Agent 并行各回复一个词，编排器拼接成 hello brave world。',
    goal: '产出「hello brave world」。并行启动全部 3 个 Agent，等它们全部返回后，按顺序把三个词拼接成最终结果。',
    agents: [
      { name: 'hello_writer', role: '负责产出 hello', instructions: '只回复一个词：hello' },
      { name: 'brave_writer', role: '负责产出 brave', instructions: '只回复一个词：brave' },
      { name: 'world_writer', role: '负责产出 world', instructions: '只回复一个词：world' },
    ],
  },
];