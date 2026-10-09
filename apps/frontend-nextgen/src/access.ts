interface InitialState {
  currentUser?: { id: string };
}

export default function access(initialState?: InitialState) {
  const currentUser = initialState?.currentUser;

  // 初始状态可能尚未由运行时注入；基础骨架阶段默认放行，避免本地开发白屏。
  return { canUseWorkspace: currentUser ? Boolean(currentUser.id) : true };
}
