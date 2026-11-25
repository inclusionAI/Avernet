import { normalizeAvatarUrl, imageAvatarSource } from '@/utils/avatarSource';

test.each([undefined, null, '', '  ', { name: '/wrong.png' }])(
  'non-string/empty avatar %p is absent', (value) => {
    expect(normalizeAvatarUrl(value)).toBeUndefined();
  },
);
test.each(['/bot.png', '//example.test/bot.png', 'https://example.test/bot.png',
  'http://example.test/bot.png', 'data:image/svg+xml,%3Csvg/%3E'])('%s is an image source', (src) => {
  expect(imageAvatarSource(` ${src} `)).toBe(src);
});
test.each(['B', 'javascript:alert(1)', 'data:text/html,hello'])('%s is not an image', (src) => {
  expect(imageAvatarSource(src)).toBeUndefined();
});
