import { uploadChatImageAttachment, uploadChatImageAttachments } from '@/services/workspace/groupChatAttachmentService';
import { sessionFileService } from '@/services/workspace/sessionFileService';
import { beforeEach, expect, it, jest } from '@jest/globals';

jest.mock('@/services/workspace/sessionFileService');

const svc = sessionFileService as unknown as Record<string, jest.Mock<any>>;

beforeEach(() => {
  jest.resetAllMocks();
});

const completedFile = {
  fileId: 'f1',
  sessionId: 's1',
  name: 'photo.jpg',
  mimeType: 'image/jpeg',
  size: 2048,
  status: 'ready',
  ownerActorId: 'human_1',
  ownerKind: 'human',
  ownerName: '我',
  sha256: null,
  createdAt: 1,
  updatedAt: 1,
};

it('uploads one image through prepare → upload → complete → share and returns BCS attachment', async () => {
  svc.prepareUpload.mockResolvedValue({ ok: true, data: { fileId: 'f1', uploadUrl: 'https://up.example/f1' } });
  svc.uploadBytes.mockResolvedValue(undefined);
  svc.completeUpload.mockResolvedValue({ ok: true, data: completedFile });
  svc.shareFileWithExpiry.mockResolvedValue({
    ok: true,
    data: { shareUrl: 'https://share.example/f1', expiresAt: 1700003600000 },
  });
  const file = new File(['abc'], 'photo.jpg', { type: 'image/jpeg' });

  const attachment = await uploadChatImageAttachment('s1', file);

  expect(svc.prepareUpload).toHaveBeenCalledWith('s1', {
    file_name: 'photo.jpg',
    size: 3,
    mime_type: 'image/jpeg',
  });
  expect(svc.uploadBytes).toHaveBeenCalledWith(
    'https://up.example/f1',
    file,
    expect.objectContaining({ mime: 'image/jpeg' }),
  );
  expect(svc.completeUpload).toHaveBeenCalledWith('s1', 'f1');
  expect(svc.shareFileWithExpiry).toHaveBeenCalledWith('s1', 'f1', 3600);
  expect(attachment).toEqual({
    attachment_id: 'f1',
    type: 'image',
    file_name: 'photo.jpg',
    mime_type: 'image/jpeg',
    size: 2048,
    url: 'https://share.example/f1',
    expires_at: 1700003600000,
  });
});

it('uploadChatImageAttachments keeps fulfilled attachments and drops failed files', async () => {
  const good = new File(['a'], 'a.png', { type: 'image/png' });
  const bad = new File(['b'], 'b.webp', { type: 'image/webp' });
  svc.prepareUpload
    .mockResolvedValueOnce({ ok: true, data: { fileId: 'good', uploadUrl: 'https://up.example/good' } })
    .mockResolvedValueOnce({ ok: false, error: { friendlyMessage: '网络异常' } });
  svc.uploadBytes.mockResolvedValue(undefined);
  svc.completeUpload.mockResolvedValue({ ok: true, data: { ...completedFile, fileId: 'good' } });
  svc.shareFileWithExpiry.mockResolvedValue({
    ok: true,
    data: { shareUrl: 'https://share.example/good', expiresAt: 1700003600000 },
  });

  const attachments = await uploadChatImageAttachments('s1', [good, bad]);

  expect(attachments).toHaveLength(1);
  expect(attachments[0].attachment_id).toBe('good');
});
