/** Keep small images intact; bound large data URLs before they reach Bot metadata. */
const MAX_DATA_URL_LENGTH = 24 * 1024;
const MAX_PIXELS = 16_000_000;

// Inspect dimensions before asking the browser to decode potentially huge images.
function dimensions(data: string): [number, number] {
  const payload = data.split(',', 2)[1];
  if (!payload) throw new Error('无法读取此图片，请选择其他图片');
  const raw = atob(payload);
  const bytes = Uint8Array.from(raw, (char) => char.charCodeAt(0));
  const view = new DataView(bytes.buffer);
  const text = (start: number, end: number) => String.fromCharCode(...bytes.slice(start, end));
  if (bytes.length >= 24 && text(1, 4) === 'PNG' && bytes[0] === 0x89 && text(12, 16) === 'IHDR') {
    return [view.getUint32(16), view.getUint32(20)];
  }
  if (bytes.length >= 10 && (text(0, 6) === 'GIF87a' || text(0, 6) === 'GIF89a')) {
    return [view.getUint16(6, true), view.getUint16(8, true)];
  }
  if (bytes.length >= 30 && text(0, 4) === 'RIFF' && text(8, 12) === 'WEBP') {
    const kind = text(12, 16);
    if (kind === 'VP8X') {
      const u24 = (i: number) => bytes[i] | (bytes[i + 1] << 8) | (bytes[i + 2] << 16);
      return [1 + u24(24), 1 + u24(27)];
    }
    if (kind === 'VP8L' && bytes[20] === 0x2f) {
      const bits = view.getUint32(21, true);
      return [(bits & 0x3fff) + 1, ((bits >>> 14) & 0x3fff) + 1];
    }
    if (kind === 'VP8 ' && bytes[23] === 0x9d && bytes[24] === 0x01 && bytes[25] === 0x2a) {
      return [view.getUint16(26, true) & 0x3fff, view.getUint16(28, true) & 0x3fff];
    }
  }
  if (bytes.length >= 4 && bytes[0] === 0xff && bytes[1] === 0xd8) {
    let i = 2;
    while (i + 3 < bytes.length) {
      if (bytes[i++] !== 0xff) break;
      while (bytes[i] === 0xff) i++;
      const marker = bytes[i++];
      if (marker === 0xd9 || marker === 0xda) break;
      if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) continue;
      if (i + 2 > bytes.length) break;
      const length = view.getUint16(i);
      if (length < 2 || i + length > bytes.length) break;
      if ([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf].includes(marker) && length >= 7) {
        return [view.getUint16(i + 5), view.getUint16(i + 3)];
      }
      i += length;
    }
  }
  throw new Error('请选择有效的 PNG、JPEG、GIF 或 WebP 图片');
}

function readDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('头像读取失败，请重新选择'));
    reader.onload = () => typeof reader.result === 'string'
      ? resolve(reader.result)
      : reject(new Error('头像读取失败，请重新选择'));
    reader.readAsDataURL(file);
  });
}

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error('无法读取此图片，请选择其他图片'));
    image.src = url;
  });
}

export async function prepareAvatar(file: File): Promise<string> {
  if (!file.type.startsWith('image/')) throw new Error('请选择图片文件');
  if (file.size > 2 * 1024 * 1024) throw new Error('头像图片不能超过 2 MB');
  const data = await readDataUrl(file);
  const [width, height] = dimensions(data);
  if (!width || !height || width * height > MAX_PIXELS) {
    throw new Error('图片尺寸过大，请选择其他图片');
  }
  const image = await loadImage(data);
  if (!image.naturalWidth || !image.naturalHeight || image.naturalWidth * image.naturalHeight > MAX_PIXELS) {
    throw new Error('图片尺寸过大，请选择其他图片');
  }
  if (data.length <= MAX_DATA_URL_LENGTH) return data;
  const canvas = document.createElement('canvas');
  const context = canvas.getContext('2d');
  if (!context) throw new Error('无法处理此图片，请选择其他图片');
  for (const limit of [256, 192, 128, 96, 64]) {
    const scale = Math.min(1, limit / Math.max(image.naturalWidth, image.naturalHeight));
    canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    // PNG preserves alpha; progressively smaller dimensions give a bounded result.
    const result = canvas.toDataURL('image/png');
    if (result.length <= MAX_DATA_URL_LENGTH) return result;
  }
  throw new Error('图片无法缩小至可保存大小，请选择其他图片');
}
