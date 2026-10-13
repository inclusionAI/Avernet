import { parseSystemEvent } from '@/pages/Workspace/components/GroupChatPane/messageHelpers';
import { describe, expect, it } from '@jest/globals';

describe('parseSystemEvent（collaboration-message-area 系统事件行）', () => {
  it('标准三方结构：身份 + 名字 + 动作', () => {
    expect(parseSystemEvent('用户 风太 已加入会话')).toEqual({
      identity: '用户',
      name: '风太',
      action: '已加入会话',
    });
  });

  it('缺省身份词：仅 名字 + 动作', () => {
    expect(parseSystemEvent('风太 已加入会话')).toEqual({ identity: undefined, name: '风太', action: '已加入会话' });
  });

  it('退出/移出/解散类动作同样可解析', () => {
    expect(parseSystemEvent('Bot 本地代码专家 退出了会话')?.action).toBe('退出了会话');
    expect(parseSystemEvent('用户 陈燕平 移出会话')?.action).toBe('移出会话');
    expect(parseSystemEvent('用户 群主 解散了会话')?.action).toBe('解散了会话');
  });

  it('名字含空格的多词名字不截断', () => {
    expect(parseSystemEvent('用户 王小 明 已加入会话')?.name).toBe('王小 明');
  });

  it('非事件文本降级返回 null（调用方渲染 时间+原文，不隐藏消息）', () => {
    expect(parseSystemEvent('系统提示：会话已归档')).toBeNull();
    expect(parseSystemEvent('')).toBeNull();
    expect(parseSystemEvent('随便一句话')).toBeNull();
  });
});