import type {
  CollaborationDefinitionComponentProps,
  CollaborationDefinitionSnapshot,
} from './types';

export const COLLABORATION_DEFINITION_COMPONENT =
  'bcsPanel.CollaborationDefinitionCard';
export const COLLABORATION_DEFINITION_PREVIEW_ENTRY =
  'CollaborationDefinitionPreview';
export const DEFINITION_YAML_MAX_BYTES = 65_536;
export const SNAPSHOT_MESSAGE_MAX_BYTES = 262_144;
export const SNAPSHOT_TITLE_MAX_CODE_POINTS = 120;

const SNAPSHOT_FIELDS = new Set([
  'schemaVersion',
  'documentId',
  'snapshotId',
  'parentSnapshotId',
  'title',
  'definitionYaml',
]);
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export type CollaborationDefinitionSnapshotErrorCode =
  | 'invalid_payload'
  | 'unsupported_schema_version'
  | 'unknown_field'
  | 'invalid_field'
  | 'invalid_uuid'
  | 'invalid_parent_snapshot'
  | 'invalid_title'
  | 'yaml_too_large'
  | 'message_too_large';

export class CollaborationDefinitionSnapshotError extends Error {
  constructor(
    public readonly code: CollaborationDefinitionSnapshotErrorCode,
    message: string,
  ) {
    super(message);
    this.name = 'CollaborationDefinitionSnapshotError';
  }
}

function utf8ByteLength(value: string): number {
  return new TextEncoder().encode(value).byteLength;
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new CollaborationDefinitionSnapshotError(
      'invalid_payload',
      '协作快照必须是对象',
    );
  }
  return value as Record<string, unknown>;
}

function uuid(value: unknown, field: string): string {
  if (typeof value !== 'string' || !UUID_PATTERN.test(value)) {
    throw new CollaborationDefinitionSnapshotError(
      'invalid_uuid',
      `${field} 必须是标准 UUID`,
    );
  }
  return value.toLowerCase();
}

function serializeParams(snapshot: CollaborationDefinitionSnapshot): string {
  return JSON.stringify(snapshot).replace(/[\s&<>']/gu, (char) =>
    `\\u${char.charCodeAt(0).toString(16).padStart(4, '0')}`,
  );
}

function canonicalMessage(snapshot: CollaborationDefinitionSnapshot): string {
  return `<AixUI type="card" component="${COLLABORATION_DEFINITION_COMPONENT}" params='${serializeParams(
    snapshot,
  )}' />`;
}

export function snapshotInputFromProps(
  props: CollaborationDefinitionComponentProps,
): unknown {
  const paramsData = props.params?.data;
  if (paramsData && typeof paramsData === 'object' && 'params' in paramsData) {
    return (paramsData as Record<string, unknown>).params;
  }
  const payloadData = props.payload?.data;
  if (payloadData && typeof payloadData === 'object' && 'params' in payloadData) {
    return (payloadData as Record<string, unknown>).params;
  }
  if (props.snapshot !== undefined) return props.snapshot;
  // Host-injected transport settings are not fields of the message payload.
  const { apiBaseUrl, baseUrl, onOpenCreateGroup, data, ...snapshot } = props;
  return snapshot;
}

export function decodeCollaborationDefinitionSnapshot(
  input: unknown,
): CollaborationDefinitionSnapshot {
  const value = record(input);
  const unknownField = Object.keys(value).find(
    (field) => !SNAPSHOT_FIELDS.has(field),
  );
  if (unknownField) {
    throw new CollaborationDefinitionSnapshotError(
      'unknown_field',
      `协作快照包含未知字段 ${unknownField}`,
    );
  }
  if (value.schemaVersion !== 1) {
    throw new CollaborationDefinitionSnapshotError(
      'unsupported_schema_version',
      `不支持协作快照版本 ${String(value.schemaVersion)}`,
    );
  }
  const documentId = uuid(value.documentId, 'documentId');
  const snapshotId = uuid(value.snapshotId, 'snapshotId');
  const parentSnapshotId =
    value.parentSnapshotId === undefined
      ? undefined
      : uuid(value.parentSnapshotId, 'parentSnapshotId');
  if (parentSnapshotId === snapshotId) {
    throw new CollaborationDefinitionSnapshotError(
      'invalid_parent_snapshot',
      '父快照不能指向自身',
    );
  }
  if (typeof value.definitionYaml !== 'string') {
    throw new CollaborationDefinitionSnapshotError(
      'invalid_field',
      'definitionYaml 必须是字符串',
    );
  }
  if (utf8ByteLength(value.definitionYaml) > DEFINITION_YAML_MAX_BYTES) {
    throw new CollaborationDefinitionSnapshotError(
      'yaml_too_large',
      '协作 YAML 超过 64 KiB 上限',
    );
  }

  let title: string | undefined;
  if (value.title !== undefined) {
    if (
      typeof value.title !== 'string' ||
      !value.title.trim() ||
      Array.from(value.title).length > SNAPSHOT_TITLE_MAX_CODE_POINTS
    ) {
      throw new CollaborationDefinitionSnapshotError(
        'invalid_title',
        '标题必须非空且不超过 120 个 Unicode 码点',
      );
    }
    title = value.title;
  }

  const snapshot: CollaborationDefinitionSnapshot = {
    schemaVersion: 1,
    documentId,
    snapshotId,
    ...(parentSnapshotId ? { parentSnapshotId } : {}),
    ...(title === undefined ? {} : { title }),
    definitionYaml: value.definitionYaml,
  };
  if (utf8ByteLength(canonicalMessage(snapshot)) > SNAPSHOT_MESSAGE_MAX_BYTES) {
    throw new CollaborationDefinitionSnapshotError(
      'message_too_large',
      '完整消息超过 256 KiB 上限',
    );
  }
  return snapshot;
}

function lengthPrefixed(value: string): string {
  return `${value.length}:${value}`;
}

export function buildCollaborationDefinitionTabId(
  sourceMessageId: string,
  snapshotId: string,
): string {
  return ['collaboration-definition', sourceMessageId, snapshotId.toLowerCase()]
    .map(lengthPrefixed)
    .join('|');
}

export function collaborationDefinitionSnapshotFingerprint(
  snapshot: CollaborationDefinitionSnapshot,
): string {
  return JSON.stringify(snapshot);
}
