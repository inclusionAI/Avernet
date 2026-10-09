import { describe, expect, it } from 'vitest';
import { botPickerLayout } from '../useBotPickerLayout';

describe('Bot picker viewport constraints (geometry only, not browser certification)', () => {
  it('keeps the desktop picker below its anchor', () => {
    expect(botPickerLayout({ left: 300, top: 180, bottom: 225 }, 440, { left: 0, top: 0, width: 1440, height: 900 }))
      .toMatchObject({ left: 300, top: 237, width: 520 });
  });
  it('flips upwards when the lower viewport has insufficient space', () => {
    expect(botPickerLayout({ left: 700, top: 650, bottom: 695 }, 440, { left: 0, top: 0, width: 1000, height: 800 }))
      .toMatchObject({ left: 468, top: 198 });
  });
  it.each([390, 320, 195])('fits a narrow or zoomed %ipx viewport and constrains height', width => {
    const result = botPickerLayout({ left: 100, top: 180, bottom: 225 }, 440, { left: 15, top: 30, width, height: 300 });
    expect(result).toMatchObject({ left: 27, top: 42, width: width - 24, maxHeight: 276, minWidth: 0 });
  });
});
