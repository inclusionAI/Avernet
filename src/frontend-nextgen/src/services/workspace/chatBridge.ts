// @sdd: 全局单例 ChatBridge —— aixcore 卡片（方式① AixUIRenderer→ReactRender 沙箱）的通信承重点。
//
// 背景：SDK ReactRender/IframeSandbox 的沙箱写死从 `window.aixBridge` 取桥并以闭包变量注入
// 远程卡片组件代码（@tc-chat/ui sandbox/IframeSandbox.tsx:344、createSandbox.ts:44）；
// @alipay/tc-chat-extensions 的 AixUIRenderer 不透传 onAction/onInteraction/eventEmitter props，
// 故沙箱卡片通信**唯一出口是 `window.aixBridge.*`**。
//
// 解法：模块级全局单例 ChatBridge（installGlobal:true，写 window.aixBridge）+ chatBridgeHelper.set('main')
// 登记，对齐 open-claw。经 registerSidePanelWiring() 在 app 启动早期 import，
// 使 window.aixBridge 在任意会话页挂载前就位；useChatBridge 注册的 submit/abort 等挂在这同一实例上。
//
// 任务执行拦截：卡片 task_ready「执行」按钮调 aixBridge.submit('执行任务', {__taskAction:'execute', task})
// 时，submit 拦截层识别 __taskAction==='execute'，不透传原 submit，改调注入的 onTaskExecute(task) 回调
// （由 useWorkspace/useGroupChat 注入，带 taskComposerContext → executeTaskService → 成功后经
// submitPanelMessage 发 <AixUI type="panel"> 给 bot 落库 → loadHistory 拉回持久）。
import { ChatBridge, chatBridgeHelper } from '@tc-chat/core';

export const chatBridge = new ChatBridge({ installGlobal: true });
chatBridgeHelper.set('main', chatBridge);

/** 任务执行拦截回调：卡片点「执行」时被调用，task 为卡片传出的 task_ready.task JSON。 */
type TaskExecuteHandler = (task: Record<string, unknown>) => void;

let taskExecuteHandler: TaskExecuteHandler | null = null;

/** 注入任务执行拦截回调（useWorkspace/useGroupChat 挂载时调，卸载传 null 清除）。 */
export function setTaskExecuteHandler(fn: TaskExecuteHandler | null): void {
  taskExecuteHandler = fn;
}

/** 原始 submit 引用（模块加载时捕获，未被拦截层覆盖）。execute 成功后用此发 <AixUI> 给 bot。 */
let origSubmit: ((content: string, extra?: unknown) => unknown) | null = null;

/** 命令式发送副屏消息：走正常对话链路（插 user 消息 + 发后端落库），标记 isInject 让 bot 不回复。
 * execute 成功后调此函数发 <AixUI type="panel"> 给 bot，消息落库 → loadHistory 拉回 → 副屏持久。 */
export function submitPanelMessage(content: string): void {
  origSubmit?.(content, { isInject: true });
}

// 模块级包 submit（Public API）：卡片调 window.aixBridge.submit() 时先进本拦截层。
// 命中 __taskAction==='execute' → 调注入的 onTaskExecute(task)，不透传给 useChatBridge 的 submit handler。
// 其它情况（普通消息/暂存/丢弃）→ 透传原 submit，不影响正常对话链路。
if (typeof window !== 'undefined') {
  const raw = chatBridge as unknown as {
    submit: (content: string, extra?: unknown) => unknown;
  };
  origSubmit = raw.submit.bind(chatBridge);
  raw.submit = (content: string, extra?: unknown) => {
    if (extra && typeof extra === 'object' && (extra as { __taskAction?: string }).__taskAction === 'execute') {
      const task = (extra as { task?: Record<string, unknown> }).task;
      if (task && taskExecuteHandler) {
        // 拦截执行动作：调宿主注入的执行回调（execute + 发副屏消息）。
        taskExecuteHandler(task);
        return;
      }
    }
    // 非执行动作：透传原 submit（普通对话消息 / 暂存 / 丢弃等）。
    return origSubmit?.(content, extra);
  };
  // window.aixBridge 已由 installGlobal:true 写入，指向 chatBridge 实例；
  // submit 已在实例上替换，卡片 window.aixBridge.submit 命中拦截层。
}
