import { isImageResourcePath } from '@/services/botWorkshop/resourcePreview';

test.each(['photo.png', 'assets/COVER.JPG', 'icon.svg', 'picture.webp'])('%s 使用图片预览', (path) => {
  expect(isImageResourcePath(path)).toBe(true);
});

test.each(['README.md', 'config.json', 'archive.zip'])('%s 使用文本预览', (path) => {
  expect(isImageResourcePath(path)).toBe(false);
});
