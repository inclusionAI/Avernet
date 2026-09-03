/** 从 YAML 顶层 key 中提取 participants/roles 等条目做结构摘要预览。 */
export function summarizeYaml(yaml: string): string[] {
  const keys: string[] = [];
  for (const line of yaml.split('\n')) {
    const match = /^([A-Za-z_][\w-]*):\s*$/.exec(line.trim());
    if (match && !keys.includes(match[1])) keys.push(match[1]);
  }
  return keys;
}
