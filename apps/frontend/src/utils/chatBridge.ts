/**
 * ChatBridge 全局实例
 *
 * 挂载到 window.aixBridge，供外部代码（ReactRender sandbox、操作按钮等）
 * 命令式地控制聊天行为。
 *
 * 用法：
 *   window.aixBridge.submit('Hello')     // 发送消息
 *   window.aixBridge.abort()             // 中止请求
 *   window.aixBridge.getMessages()       // 获取消息列表
 *   window.aixBridge.getIsRequesting()   // 是否请求中
 *   window.aixBridge.getInputRef()?.focus()  // 聚焦输入框
 */
import { ChatBridge, chatBridgeHelper } from '@aix-chat/core';

export const chatBridge = new ChatBridge({ installGlobal: true });

// 注册到全局注册表，方便按 key 查找
chatBridgeHelper.set('main', chatBridge);
