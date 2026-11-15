/** @jest-environment jsdom */
import { prepareAvatar } from '@/components/BotWorkshop/BotAvatar/prepareAvatar';

describe('prepareAvatar', () => {
  const originalReader = global.FileReader;
  const originalImage = global.Image;
  afterEach(() => {
    global.FileReader = originalReader;
    global.Image = originalImage;
    jest.restoreAllMocks();
  });

  function mockRead(result: string) {
    global.FileReader = class {
      result = result;
      onload?: () => void;
      onerror?: () => void;
      readAsDataURL() { this.onload?.(); }
    } as unknown as typeof FileReader;
  }

  function png(width: number, height: number, padding = 0) {
    const bytes = new Uint8Array(24 + padding);
    bytes.set([0x89, 0x50, 0x4e, 0x47], 0);
    bytes.set([0x49, 0x48, 0x44, 0x52], 12);
    const view = new DataView(bytes.buffer);
    view.setUint32(16, width);
    view.setUint32(20, height);
    return 'data:image/png;base64,' + btoa(Array.from(bytes, (b) => String.fromCharCode(b)).join(''));
  }

  function mockImage() {
    global.Image = class {
      naturalWidth = 1000;
      naturalHeight = 500;
      onload?: () => void;
      onerror?: () => void;
      set src(_value: string) { this.onload?.(); }
    } as unknown as typeof Image;
  }

  it('keeps a small decodable image byte-for-byte', async () => {
    const data = png(10, 10);
    mockRead(data);
    mockImage();
    await expect(prepareAvatar(new File(['x'], 'small.png', { type: 'image/png' }))).resolves.toBe(data);
  });

  it('keeps a small GIF unchanged and rejects an undecodable image', async () => {
    const data = 'data:image/gif;base64,' + btoa('GIF89a\x10\x00\x10\x00');
    mockRead(data);
    mockImage();
    await expect(prepareAvatar(new File(['x'], 'small.gif', { type: 'image/gif' }))).resolves.toBe(data);
    global.Image = class {
      onerror?: () => void;
      set src(_value: string) { this.onerror?.(); }
    } as unknown as typeof Image;
    await expect(prepareAvatar(new File(['x'], 'bad.gif', { type: 'image/gif' }))).rejects.toThrow('无法读取');
  });

  it('bounds a large image and preserves alpha via PNG', async () => {
    mockRead(png(1000, 500, 20000));
    mockImage();
    const drawImage = jest.fn();
    jest.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ drawImage, clearRect: jest.fn() } as unknown as CanvasRenderingContext2D);
    const encode = jest.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockReturnValue('data:image/png;base64,small');
    await expect(prepareAvatar(new File(['x'], 'large.png', { type: 'image/png' })))
      .resolves.toBe('data:image/png;base64,small');
    expect(drawImage).toHaveBeenCalled();
    expect(encode).toHaveBeenCalledWith('image/png');
  });

  it('rejects oversized dimensions before decoding', async () => {
    mockRead(png(50000, 50000));
    const image = jest.fn();
    global.Image = image as unknown as typeof Image;
    await expect(prepareAvatar(new File(['x'], 'huge.png', { type: 'image/png' }))).rejects.toThrow('尺寸过大');
    expect(image).not.toHaveBeenCalled();
  });

  it('rejects corrupt images and files over 2 MB', async () => {
    mockRead('data:image/png;base64,eA==');
    await expect(prepareAvatar(new File(['x'], 'bad.png', { type: 'image/png' }))).rejects.toThrow('有效');
    const file = new File([new Uint8Array(2 * 1024 * 1024 + 1)], 'huge.png', { type: 'image/png' });
    await expect(prepareAvatar(file)).rejects.toThrow('2 MB');
  });
});
