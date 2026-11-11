// 契约依据：https://github.com/a2aproject/A2A/blob/v1.0.0/specification/a2a.proto
// 这里只校验转换所需字段；复杂可选字段的验证范围通过 notes 明确告知。

export class ConversionError extends Error {
  constructor(issues) {
    super(issues.join('\n'));
    this.name = 'ConversionError';
    this.issues = issues;
  }
}

const own = (object, key) => Object.hasOwn(object, key);
const objectLike = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const bindings = ['JSONRPC', 'HTTP+JSON', 'GRPC'];

function jsonIssues(value, path = '$', ancestors = new Set(), issues = []) {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return issues;
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) issues.push(`${path}：数值必须是有限数。`);
    else if (Number.isInteger(value) && !Number.isSafeInteger(value)) issues.push(`${path}：整数超出 JavaScript 安全范围，请改用字符串保存。`);
    return issues;
  }
  if (typeof value !== 'object' || (!Array.isArray(value) && ![Object.prototype, null].includes(Object.getPrototypeOf(value)))) {
    issues.push(`${path}：必须是 JSON 可表示的数据。`);
    return issues;
  }
  if (ancestors.has(value)) {
    issues.push(`${path}：包含循环引用，无法转换为 JSON。`);
    return issues;
  }
  ancestors.add(value);
  if (Array.isArray(value)) {
    for (let i = 0; i < value.length; i++) jsonIssues(value[i], `${path}[${i}]`, ancestors, issues);
  } else {
    for (const [key, item] of Object.entries(value)) jsonIssues(item, `${path}.${key}`, ancestors, issues);
  }
  ancestors.delete(value);
  return issues;
}

function assertJson(value) {
  const issues = jsonIssues(value);
  if (issues.length) throw new ConversionError(issues);
}

const clone = value => JSON.parse(JSON.stringify(value));

export function parseJson(text) {
  if (typeof text !== 'string') throw new ConversionError(['输入：请提供 JSON 文本。']);
  let value;
  try {
    value = JSON.parse(text);
  } catch (error) {
    throw new ConversionError([`JSON 语法错误：${error.message}`]);
  }
  assertJson(value);
  return value;
}

function validEndpoint(value, binding) {
  if (typeof value !== 'string' || value !== value.trim()) return false;
  if (binding === 'GRPC' && /^(?:[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?|\[[0-9a-f:]+\]):\d+$/i.test(value)) {
    const port = Number(value.slice(value.lastIndexOf(':') + 1));
    try { new URL(`http://${value}`); } catch { return false; }
    return port > 0 && port <= 65535;
  }
  if (!/^https?:\/\//i.test(value)) return false;
  try {
    const url = new URL(value);
    if (url.username || url.password || url.hash) return false;
    return url.protocol === 'https:' || (url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname));
  } catch {
    return false;
  }
}

export function convertAgentCard(input) {
  assertJson(input);
  if (!objectLike(input)) throw new ConversionError(['$：AgentCard 输入必须是 JSON 对象。']);
  const output = clone(input);
  const issues = [];
  const notes = [];
  const noteRows = [];
  function note(source, target, description, explanation = description) {
    notes.push(description);
    noteRows.push({ source, target, description: explanation });
  }

  function text(value, path) {
    if (typeof value !== 'string' || !value.trim()) issues.push(`${path}：必须是非空字符串。`);
  }
  function object(value, path) {
    if (!objectLike(value)) { issues.push(`${path}：必须是对象。`); return false; }
    return true;
  }
  function strings(value, path, nonempty = false) {
    if (!Array.isArray(value) || (nonempty && !value.length)) { issues.push(`${path}：必须是${nonempty ? '非空' : ''}字符串数组。`); return; }
    value.forEach((item, i) => text(item, `${path}[${i}]`));
  }
  function unknown(value, known, path = '') {
    for (const key of Object.keys(value)) {
      if (!known.includes(key)) note(`$.${path}${key}`, `$.${path}${key}`, `${path}${key}：未知字段已保留，未验证是否符合 A2A 1.0。`, '原样保留未知字段；未验证是否符合 A2A 1.0。');
    }
  }
  function partial(path) { note(`$.${path}`, `$.${path}`, `${path}：已保留，仅检查外层类型，未深度验证协议结构。`, '原样保留；仅检查外层类型，未深度验证协议结构。'); }
  function optionalStrings(value, fields, path = '') {
    for (const field of fields) if (own(value, field)) text(value[field], `${path}${field}`);
  }
  function optionalSecurity(value, path = '') {
    if (own(value, 'securityRequirements')) {
      if (!Array.isArray(value.securityRequirements)) issues.push(`${path}securityRequirements：必须是数组。`);
      else partial(`${path}securityRequirements`);
    }
  }

  const legacy = ['preferredTransport', 'additionalInterfaces', 'security', 'supportsAuthenticatedExtendedCard', 'protocolVersion'];
  for (const field of legacy) {
    if (own(input, field)) issues.push(`${field}：不支持自动迁移旧版字段，请按 A2A 1.0 结构调整；修改版本号不能升级实际服务。`);
  }
  if (own(input, 'signatures')) issues.push('signatures：不转换已签名的 AgentCard；修改内容会使签名失效，请移除签名并在最终内容确定后重新签名。');

  if (own(input, 'supportedInterfaces')) {
    for (const field of ['url', 'protocolBinding']) {
      if (own(input, field)) issues.push(`${field}：不能与 supportedInterfaces 同时提供，请只使用一种接口输入格式。`);
    }
  } else if (own(input, 'url')) {
    output.supportedInterfaces = [{ url: input.url, protocolBinding: own(input, 'protocolBinding') ? input.protocolBinding : 'JSONRPC', protocolVersion: '1.0' }];
    delete output.url;
    delete output.protocolBinding;
    note('$.url', '$.supportedInterfaces[0].url', 'url → supportedInterfaces[0].url；接口 protocolVersion 设为 "1.0"，请确认实际服务支持 A2A 1.0。', '接口协议版本设为 "1.0"，请确认实际服务支持 A2A 1.0。');
    if (!own(input, 'protocolBinding')) note('未提供', '$.supportedInterfaces[0].protocolBinding', 'protocolBinding 未提供，默认使用 JSONRPC。', '默认使用 JSONRPC。');
  }
  for (const [field, fallback] of [['capabilities', {}], ['defaultInputModes', ['application/json']], ['defaultOutputModes', ['application/json']]]) {
    if (!own(output, field)) {
      output[field] = fallback;
      note('未提供', `$.${field}`, `${field} 未提供，默认使用 ${JSON.stringify(fallback)}；请确认与实际服务一致。`, `默认使用 ${JSON.stringify(fallback)}；请确认与实际服务一致。`);
    }
  }

  for (const field of ['name', 'description', 'version']) text(output[field], field);
  strings(output.defaultInputModes, 'defaultInputModes', true);
  strings(output.defaultOutputModes, 'defaultOutputModes', true);
  optionalStrings(output, ['documentationUrl', 'iconUrl']);
  optionalSecurity(output);
  if (own(output, 'securitySchemes') && object(output.securitySchemes, 'securitySchemes')) partial('securitySchemes');

  if (!Array.isArray(output.supportedInterfaces) || !output.supportedInterfaces.length) issues.push('supportedInterfaces：必须是非空接口数组，或在简化输入中提供 url。');
  else output.supportedInterfaces.forEach((iface, i) => {
    const path = `supportedInterfaces[${i}]`;
    if (!object(iface, path)) return;
    if (!bindings.includes(iface.protocolBinding)) issues.push(`${path}.protocolBinding：本工具仅支持 JSONRPC、HTTP+JSON、GRPC。`);
    if (iface.protocolVersion !== '1.0') issues.push(`${path}.protocolVersion：必须是字符串 "1.0"。`);
    if (!validEndpoint(iface.url, iface.protocolBinding)) issues.push(`${path}.url：请输入绝对 HTTPS 地址、本机 HTTP 地址，或 GRPC 主机:端口。`);
    optionalStrings(iface, ['tenant'], `${path}.`);
    unknown(iface, ['url', 'protocolBinding', 'protocolVersion', 'tenant'], `${path}.`);
  });

  if (own(output, 'provider') && object(output.provider, 'provider')) {
    text(output.provider.url, 'provider.url');
    text(output.provider.organization, 'provider.organization');
    unknown(output.provider, ['url', 'organization'], 'provider.');
  }
  if (object(output.capabilities, 'capabilities')) {
    for (const field of ['streaming', 'pushNotifications', 'extendedAgentCard']) {
      if (own(output.capabilities, field) && typeof output.capabilities[field] !== 'boolean') issues.push(`capabilities.${field}：必须是布尔值。`);
    }
    if (own(output.capabilities, 'extensions')) {
      if (!Array.isArray(output.capabilities.extensions)) issues.push('capabilities.extensions：必须是数组。');
      else partial('capabilities.extensions');
    }
    unknown(output.capabilities, ['streaming', 'pushNotifications', 'extendedAgentCard', 'extensions'], 'capabilities.');
  }
  if (!Array.isArray(output.skills) || !output.skills.length) issues.push('skills：必须提供非空技能数组，不能自动推断 Agent 的能力。');
  else output.skills.forEach((skill, i) => {
    const path = `skills[${i}]`;
    if (!object(skill, path)) return;
    for (const field of ['id', 'name', 'description']) text(skill[field], `${path}.${field}`);
    strings(skill.tags, `${path}.tags`, true);
    for (const field of ['examples', 'inputModes', 'outputModes']) if (own(skill, field)) strings(skill[field], `${path}.${field}`);
    optionalSecurity(skill, `${path}.`);
    unknown(skill, ['id', 'name', 'description', 'tags', 'examples', 'inputModes', 'outputModes', 'securityRequirements'], `${path}.`);
  });
  unknown(output, ['name', 'description', 'version', 'supportedInterfaces', 'provider', 'documentationUrl', 'capabilities', 'securitySchemes', 'securityRequirements', 'defaultInputModes', 'defaultOutputModes', 'skills', 'signatures', 'iconUrl']);
  if (issues.length) throw new ConversionError(issues);
  return { output, notes, noteRows };
}

export function convertMessage(input, options = {}) {
  assertJson(input);
  if (!objectLike(options)) throw new ConversionError(['options：消息选项必须是对象。']);
  const issues = [];
  const ids = {};
  for (const field of ['messageId', 'requestId', 'contextId', 'taskId']) {
    const value = options[field];
    if (value === undefined || value === '') continue;
    if (typeof value !== 'string' || !value.trim()) issues.push(`${field}：必须是非空字符串，或留空自动处理。`);
    else ids[field] = value;
  }
  if (issues.length) throw new ConversionError(issues);
  const message = {
    messageId: ids.messageId ?? globalThis.crypto.randomUUID(),
    role: 'ROLE_USER',
    parts: [{ data: clone(input), mediaType: 'application/json' }],
  };
  if (ids.contextId) message.contextId = ids.contextId;
  if (ids.taskId) message.taskId = ids.taskId;
  const description = '原始 JSON 完整放入 message.parts[0].data；未推断或改写业务字段。';
  return {
    output: { jsonrpc: '2.0', id: ids.requestId ?? globalThis.crypto.randomUUID(), method: 'SendMessage', params: { message } },
    notes: [description],
    noteRows: [{ source: '$', target: '$.params.message.parts[0].data', description: '完整保留原始 JSON，不推断或改写业务字段。' }],
  };
}
