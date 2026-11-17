import { previewInvitation } from '@/services/backendApi/collaboration/collaborationInvitationController';
import { backendRequest } from '@/services/backendApi/httpClient';
import { expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/httpClient');

it('previews the encoded token with a read-only, authenticated GET', async () => {
  await previewInvitation('tk/with?delimiters');
  expect(backendRequest).toHaveBeenCalledWith(
    '/openapi/v1/collaboration/invitations/tk%2Fwith%3Fdelimiters',
    { method: 'GET', injectUserId: false },
  );
});
