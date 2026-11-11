import { ConversionError, convertMessage } from './converter.js';

const own = (value, key) => Object.hasOwn(value, key);
const objectLike = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const fail = (path, reason) => { throw new ConversionError([`${path}：${reason}`]); };
const roles = new Map([
  ['USER', 'ROLE_USER'], ['user', 'ROLE_USER'], ['ROLE_USER', 'ROLE_USER'],
  ['AGENT', 'ROLE_AGENT'], ['agent', 'ROLE_AGENT'], ['ROLE_AGENT', 'ROLE_AGENT']
]);
const fieldsExcept = (value, known) => Object.fromEntries(Object.entries(value).filter(([key]) => !known.includes(key)));

function uri(value) {
  if (!/^[a-z][a-z0-9+.-]*:[^\s\\]+$/i.test(value) || /%(?![0-9a-f]{2})/i.test(value)) return false;
  try {
    const parsed = new URL(value);
    if (['http:', 'https:', 'ftp:', 'ws:', 'wss:'].includes(parsed.protocol)) {
      return /^[a-z]+:\/\/[^/?#]/i.test(value) && Boolean(parsed.hostname);
    }
    return Boolean(parsed.hostname || parsed.pathname);
  } catch {
    return false;
  }
}

// 转换分派、消息提取与字段联动共用同一选择规则；明确的根消息优先。
export function getMessageSourcePath(input) {
  if (!objectLike(input)) return null;
  if ((own(input, 'parts') || own(input, 'messageId')) && (own(input, 'role') || input.kind === 'message')) return [];
  const nested = input.message;
  return objectLike(nested) && (own(nested, 'parts') || own(nested, 'role')) ? ['message'] : null;
}

function metadataWithFields(value, sourceFields, path) {
  if (value !== undefined && !objectLike(value)) fail(`${path}.metadata`, '必须是 JSON 对象。');
  if (!Object.keys(sourceFields).length) return value;
  if (value && own(value, 'sourceFields')) {
    fail(`${path}.metadata.sourceFields`, '与用于保留原始业务字段的键冲突，请先重命名该键，避免覆盖数据。');
  }
  return { ...value, sourceFields };
}

function stringArray(value, path) {
  if (!Array.isArray(value)) fail(path, '必须是字符串数组。');
  value.forEach((item, index) => {
    if (typeof item !== 'string' || !item.trim()) fail(`${path}[${index}]`, '必须是非空字符串。');
  });
}

function mapPart(part, path, targetPath, note) {
  if (!objectLike(part)) fail(path, '每个消息片段必须是对象。');
  const selectors = ['text', 'raw', 'url', 'data'].filter(key => own(part, key));
  const hasContent = own(part, 'content');
  if (selectors.length + Number(hasContent) !== 1) {
    fail(path, 'text、raw、url、data 与待映射的 content 必须恰好提供一项，不能丢弃或猜测片段内容。');
  }
  const output = {};
  if (hasContent) {
    if (own(part, 'kind') && part.kind !== 'text') fail(`${path}.kind`, '仅支持将 text 类型的 content 映射为文本。');
    if (typeof part.content !== 'string') fail(`${path}.content`, '文本内容必须是字符串。');
    output.text = part.content;
    note(`${path}.content`, `${targetPath}.text`, `${path}.content → text。`, '转换为 A2A 文本，内容保持不变。');
  } else {
    const field = selectors[0];
    if (field !== 'data' && typeof part[field] !== 'string') fail(`${path}.${field}`, '必须是字符串。');
    if (field === 'url' && !uri(part.url)) fail(`${path}.url`, '必须是带协议的非空 URI。');
    if (field === 'raw' && !/^(?:[A-Za-z0-9+/_-]{4})*(?:[A-Za-z0-9+/_-]{2}(?:==)?|[A-Za-z0-9+/_-]{3}=?)?$/.test(part.raw)) {
      fail(`${path}.raw`, '必须是 Base64 编码字符串。');
    }
    output[field] = part[field];
  }
  for (const field of ['mediaType', 'filename']) {
    if (!own(part, field)) continue;
    if (typeof part[field] !== 'string') fail(`${path}.${field}`, '必须是字符串。');
    output[field] = part[field];
  }
  const sourceFields = fieldsExcept(part, ['text', 'raw', 'url', 'data', 'content', 'mediaType', 'filename', 'metadata']);
  const metadata = metadataWithFields(part.metadata, sourceFields, path);
  if (metadata !== undefined) output.metadata = metadata;
  if (Object.keys(sourceFields).length) note(`${path} 的其他业务字段`, `${targetPath}.metadata.sourceFields`, `${path} 的业务字段保留在 metadata.sourceFields。`, '原样保留业务字段。');
  return output;
}

// Message/Part 定义依据：https://github.com/a2aproject/A2A/blob/v1.0.0/specification/a2a.proto
export function convertStructuredMessage(input, options = {}) {
  // 复用 JSON 数据检查、选项校验与 ID 生成，同时获得与调用方隔离的深拷贝。
  const { output } = convertMessage(input, options);
  const cloned = output.params.message.parts[0].data;
  if (!objectLike(cloned)) fail('$', '结构化消息输入必须是对象。');
  const wrapped = getMessageSourcePath(cloned)?.[0] === 'message';
  const source = wrapped ? cloned.message : cloned;
  const path = wrapped ? '$.message' : '$';
  if (!objectLike(source)) fail(path, '必须是消息对象。');
  if (!roles.has(source.role)) fail(`${path}.role`, '仅支持 USER、AGENT、user、agent、ROLE_USER 或 ROLE_AGENT。');
  if (!Array.isArray(source.parts) || !source.parts.length) fail(`${path}.parts`, '必须是非空消息片段数组。');

  const notes = [];
  const noteRows = [];
  function note(source, target, description, explanation = description) {
    notes.push(description);
    noteRows.push({ source, target, description: explanation });
  }
  const sessions = [];
  if (wrapped && own(cloned, 'sessionId')) sessions.push({ path: '$.sessionId', value: cloned.sessionId });
  if (own(source, 'sessionId')) sessions.push({ path: `${path}.sessionId`, value: source.sessionId });
  for (const session of sessions) {
    if (typeof session.value !== 'string' || !session.value.trim()) fail(session.path, '必须是非空字符串。');
    if (session.value !== sessions[0].value) fail(session.path, `与 ${sessions[0].path} 冲突，请统一会话标识。`);
  }
  if (sessions.length) {
    for (const [contextPath, value] of [[`${path}.contextId`, source.contextId], ['options.contextId', options.contextId]]) {
      if (value === undefined || value === null || value === '') continue;
      if (typeof value !== 'string' || !value.trim()) fail(contextPath, '必须是非空字符串，或留空自动处理。');
      if (value !== sessions[0].value) fail(sessions[0].path, `与 ${contextPath} 冲突，请统一会话标识。`);
    }
  }
  const message = output.params.message;
  message.role = roles.get(source.role);
  for (const field of ['messageId', 'taskId', 'contextId']) {
    const override = options[field] !== undefined && options[field] !== '';
    if (override) {
      if (own(source, field) && source[field] !== options[field]) note(`${path}.${field}`, `$.params.message.${field}`, `${field}：已使用选项覆盖原始标识。`, '使用选项覆盖原始标识。');
      continue;
    }
    const value = source[field];
    if (value === undefined || value === null || value === '') continue;
    if (typeof value !== 'string' || !value.trim()) fail(`${path}.${field}`, '必须是非空字符串，或留空自动处理。');
    message[field] = value;
  }
  for (const session of sessions) {
    message.contextId = session.value;
    note(session.path, '$.params.message.contextId', `${session.path} → $.params.message.contextId；作为会话标识沿用。`, '作为 A2A 会话标识沿用。');
  }
  if (!source.messageId && !options.messageId) note('未提供', '$.params.message.messageId', 'messageId 未提供，已生成新标识；未使用业务 queryId。', '生成新标识；未使用业务 queryId。');
  message.parts = source.parts.map((part, index) => mapPart(part, `${path}.parts[${index}]`, `$.params.message.parts[${index}]`, note));

  const sourceFields = fieldsExcept(source, ['role', 'parts', 'messageId', 'taskId', 'contextId', 'sessionId', 'metadata', 'extensions', 'referenceTaskIds']);
  if (own(source, 'referenceTaskIds')) {
    stringArray(source.referenceTaskIds, `${path}.referenceTaskIds`);
    message.referenceTaskIds = source.referenceTaskIds;
  }
  if (own(source, 'extensions')) {
    stringArray(source.extensions, `${path}.extensions`);
    if (source.extensions.every(uri)) message.extensions = source.extensions;
    else {
      sourceFields.extensions = source.extensions;
      note(`${path}.extensions`, '$.params.message.metadata.sourceFields.extensions', 'extensions 包含非 URI 标记，原样保留在 message.metadata.sourceFields.extensions，未声明为协议扩展。', '包含非 URI 标记，作为业务字段保留，未声明为协议扩展。');
    }
  }
  const metadata = metadataWithFields(source.metadata, sourceFields, path);
  if (metadata !== undefined) message.metadata = metadata;
  if (Object.keys(sourceFields).length) note(`${path} 的其他业务字段`, '$.params.message.metadata.sourceFields', '消息的其他业务字段原样保留在 message.metadata.sourceFields。', '原样保留其他业务字段。');
  if (wrapped) {
    const envelope = fieldsExcept(cloned, ['message', 'sessionId']);
    if (Object.keys(envelope).length) {
      output.params.metadata = { sourceEnvelope: envelope };
      note('$.message 和 $.sessionId 之外的字段', '$.params.metadata.sourceEnvelope', 'message 和已映射 sessionId 之外的外层字段原样保留在 params.metadata.sourceEnvelope。', '原样保留其余外层业务字段。');
    }
  }
  return { output, notes, noteRows };
}
