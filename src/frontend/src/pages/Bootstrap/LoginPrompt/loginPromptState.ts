export interface LoginPromptState {
  visible: boolean;
  loginUrl: string;
  help: string;
}

let state: LoginPromptState = { visible: false, loginUrl: '', help: '' };
const listeners = new Set<() => void>();

export function getLoginPromptState(): LoginPromptState {
  return state;
}

export function showLoginPrompt(loginUrl: string, help?: string) {
  state = { visible: true, loginUrl, help: help || '当前无法识别用户信息，请先完成身份验证' };
  listeners.forEach(l => l());
}

export function hideLoginPrompt() {
  state = { ...state, visible: false };
  listeners.forEach(l => l());
}

export function subscribeLoginPrompt(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
