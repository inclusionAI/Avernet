/** @jest-environment jsdom */
import { openLoginInNewTab } from '@/utils/redirectCurrentTab';
import { afterEach, describe, expect, it, jest } from '@jest/globals';

/**
 * 真机回归 #2(ace-gateway-popup-reauth):`window.open` features 带 `noopener` 时按 HTML
 * 规范返回 null——曾因此把正常打开误报为「浏览器拦截」。新手法:普通打开取引用 + 立即
 * `opener = null` 断开(tabnabbing 等价);真拦截(返回 null)如实透传。
 */

function fakePopup(): Window {
  return { opener: window, closed: false } as unknown as Window;
}

afterEach(() => {
  jest.restoreAllMocks();
});

describe('openLoginInNewTab', () => {
  it('打开成功:普通 window.open 取引用,并立即断开 opener(tabnabbing 等价 noopener)', () => {
    const popup = fakePopup();
    const openSpy = jest.spyOn(window, 'open').mockReturnValue(popup);

    const result = openLoginInNewTab('https://login.example/pubLogin');

    expect(result).toBe(popup);
    expect(openSpy).toHaveBeenCalledWith('https://login.example/pubLogin', '_blank');
    expect(popup.opener).toBeNull();
  });

  it('跨域写 opener 抛错时:静默放弃断开,句柄照常返回(不误报拦截)', () => {
    const popup = {
      get opener() {
        return window;
      },
      set opener(_value: unknown) {
        throw new DOMException('Blocked a frame with origin', 'SecurityError');
      },
      closed: false,
    } as unknown as Window;
    jest.spyOn(window, 'open').mockReturnValue(popup);

    const result = openLoginInNewTab('https://login.example/pubLogin');

    expect(result).toBe(popup);
  });

  it('真被弹窗拦截(window.open 返回 null)→ 如实透传 null,调用方据此提示降级', () => {
    jest.spyOn(window, 'open').mockReturnValue(null);

    expect(openLoginInNewTab('https://login.example/pubLogin')).toBeNull();
  });
});
