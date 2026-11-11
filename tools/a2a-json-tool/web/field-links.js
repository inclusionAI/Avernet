import { getMessageSourcePath } from './message-mapper.js';

const isObject = value => value !== null && typeof value === 'object';
const validPath = path => Array.isArray(path) && path.length > 0 && path.every(segment =>
  typeof segment === 'string' || (Number.isSafeInteger(segment) && segment >= 0));

function hasPath(value, path) {
  for (const segment of path) {
    if (!isObject(value)) return false;
    if (Array.isArray(value) ? typeof segment !== 'number' : typeof segment !== 'string') return false;
    if (!Object.hasOwn(value, segment)) return false;
    value = value[segment];
  }
  return true;
}

function structuredPath(sourcePath, input, output) {
  const messageBase = ['params', 'message'];
  const wrapped = getMessageSourcePath(input)?.[0] === 'message';
  if ((sourcePath.length === 1 && sourcePath[0] === 'sessionId')
    || (wrapped && sourcePath.length === 2 && sourcePath[0] === 'message' && sourcePath[1] === 'sessionId')) {
    return [...messageBase, 'contextId'];
  }
  if (wrapped && sourcePath[0] !== 'message') {
    return ['params', 'metadata', 'sourceEnvelope', ...sourcePath];
  }
  const path = wrapped ? sourcePath.slice(1) : sourcePath;
  if (!path.length) return messageBase;
  const [field, index, partField, ...tail] = path;
  if (field === 'parts' && path.length >= 3) {
    const base = [...messageBase, 'parts', index];
    if (partField === 'content') return [...base, 'text', ...tail];
    if (['text', 'raw', 'url', 'data', 'mediaType', 'filename', 'metadata'].includes(partField)) {
      return [...base, partField, ...tail];
    }
    return [...base, 'metadata', 'sourceFields', partField, ...tail];
  }
  if (field === 'extensions') {
    return hasPath(output, [...messageBase, 'extensions'])
      ? [...messageBase, ...path]
      : [...messageBase, 'metadata', 'sourceFields', ...path];
  }
  if (['role', 'messageId', 'taskId', 'contextId', 'parts', 'metadata', 'referenceTaskIds'].includes(field)) {
    return [...messageBase, ...path];
  }
  return [...messageBase, 'metadata', 'sourceFields', ...path];
}

// 依据转换模式定位完整路径，禁止通过字段名在输出中模糊查找。
export function resolveFieldPath(sourcePath, conversionResult) {
  if (!validPath(sourcePath) || !isObject(conversionResult)) return null;
  const { normalizedInput, output, mapping } = conversionResult;
  if (!hasPath(normalizedInput, sourcePath)) return null;
  let target;
  if (mapping === 'card') {
    target = !Object.hasOwn(normalizedInput, 'supportedInterfaces') && ['url', 'protocolBinding'].includes(sourcePath[0])
      ? ['supportedInterfaces', 0, ...sourcePath]
      : [...sourcePath];
  } else if (mapping === 'data') {
    target = ['params', 'message', 'parts', 0, 'data', ...sourcePath];
  } else if (mapping === 'structured-message') {
    target = structuredPath(sourcePath, normalizedInput, output);
  } else return null;
  return hasPath(output, target) ? target : null;
}

// UTF-16 半开区间：优先字段名，再按最短值范围关联所属字段，不按选中文字查找。
export function findFieldAtSelection(fields, start, end) {
  if (!Array.isArray(fields) || !Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 0 || end < start) return null;
  const validFields = fields.filter(field => {
    if (!isObject(field) || !validPath(field.path) || !Number.isSafeInteger(field.start) || !Number.isSafeInteger(field.end)) return false;
    return field.start >= 0 && field.end > field.start;
  });
  const contains = (rangeStart, rangeEnd) => start >= rangeStart && start < rangeEnd && end <= rangeEnd;
  const keys = validFields.filter(field => contains(field.start, field.end));
  if (keys.length) return keys.length === 1 ? keys[0] : null;
  // 跨过字段名的宽选区不能回退到父对象的值范围。
  if (validFields.some(field => start < field.end && end > field.start)) return null;
  const values = validFields.filter(field => Number.isSafeInteger(field.valueStart) && Number.isSafeInteger(field.valueEnd)
    && field.valueStart >= field.end && field.valueEnd > field.valueStart && contains(field.valueStart, field.valueEnd));
  values.sort((left, right) => (left.valueEnd - left.valueStart) - (right.valueEnd - right.valueStart));
  if (!values.length || (values.length > 1 && values[0].valueEnd - values[0].valueStart === values[1].valueEnd - values[1].valueStart)) return null;
  return values[0];
}
