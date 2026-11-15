import { decodeOpenCreateGroupOptions } from '@/domain/collaborationDraft';
import { backendRequest } from '@/services/backendApi/httpClient';
import { loadCollaborationDraft } from '@/services/workspace/collaborationDraftService';
import { createHash } from 'node:crypto';

jest.mock('@/services/backendApi/httpClient');
const request = jest.mocked(backendRequest);
const reference = { schemaVersion: 2 as const, draftId: '12345678-1234-1234-1234-123456789abc', revision: 2 };
const yaml = 'name: 协作\nparticipants: {}\n';
const response = () => ({
  draft_id: reference.draftId,
  revision: 2,
  definition_yaml: yaml,
  title: '协作模板',
  size_bytes: Buffer.byteLength(yaml),
  content_hash: createHash('sha256').update(yaml).digest('hex'),
});

beforeEach(() => request.mockReset());

it('reads the fixed revision through the V1 facade and verifies exact UTF-8 content', async () => {
  request.mockResolvedValue({ code: 20000, data: response() });
  const signal = new AbortController().signal;
  expect(await loadCollaborationDraft(reference, signal)).toEqual({
    ok: true,
    data: {
      reference,
      definitionYaml: yaml,
      title: '协作模板',
    },
  });
  expect(request).toHaveBeenCalledWith(`/api/v1/collaboration/drafts/${reference.draftId}/revisions/2`, {
    method: 'GET',
    injectUserId: false,
    signal,
  });
});

it.each([
  { revision: 3 },
  { draft_id: 'another-draft' },
  { size_bytes: yaml.length },
  { content_hash: '0'.repeat(64) },
  { definition_yaml: '\ud800' },
  { size_bytes: 65537 },
])('rejects mismatched or corrupt draft content: %j', async (patch) => {
  request.mockResolvedValue({ code: 20000, data: { ...response(), ...patch } });
  expect(await loadCollaborationDraft(reference)).toMatchObject({ ok: false, error: { code: 'DRAFT_READ_FAILED' } });
});

it('normalizes reference extras and rejects invalid host payloads before I/O', async () => {
  expect(decodeOpenCreateGroupOptions({ draft: { ...reference, obsolete: 'ignored' } })).toEqual({ draft: reference });
  expect(decodeOpenCreateGroupOptions()).toEqual({});
  expect(() => decodeOpenCreateGroupOptions({ draft: { ...reference, revision: 0 } })).toThrow();
  expect(() => decodeOpenCreateGroupOptions({ draft: reference, other: true })).toThrow();
  expect(await loadCollaborationDraft({ ...reference, draftId: '../invalid' })).toMatchObject({ ok: false });
  expect(request).not.toHaveBeenCalled();
});

it('reports a revoked version as unavailable', async () => {
  request.mockRejectedValue(Object.assign(new Error('Not found'), { status: 404 }));
  expect(await loadCollaborationDraft(reference)).toMatchObject({
    ok: false,
    error: { friendlyMessage: '协作版本不存在或分享已撤销' },
  });
});
