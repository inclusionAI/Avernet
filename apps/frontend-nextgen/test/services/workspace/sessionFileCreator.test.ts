/**
 * 会话文件创建者信息展示纯函数（2026-11-09 契约）：
 * describeSessionFileCreator（来源+创建者合段）与 formatSessionUploadTime（本地时区时间）。
 * 两者驱动单聊 BotSessionFilesRow 元数据行，文案/语义与老前端（oc-web）
 * add-session-file-creator-fields 逐字对齐——灰度切流下同一文件在两套 UI 展示一致。
 */
import { describeSessionFileCreator, formatSessionUploadTime } from '@/services/workspace/sessionFileUtils';
import { describe, expect, it } from '@jest/globals';

describe('describeSessionFileCreator - 创建者信息 → 元数据行来源合段', () => {
  it("bot 带名 → 'Bot 生成 · {名}'", () => {
    expect(describeSessionFileCreator('bot', '报告助手')).toBe('Bot 生成 · 报告助手');
  });

  it("user 带名 → '用户上传 · {名}'", () => {
    expect(describeSessionFileCreator('user', '运海')).toBe('用户上传 · 运海');
  });

  it('creator_name null/undefined/空串/纯空白 → 只显来源标签（无追加分隔名、无占位符）', () => {
    expect(describeSessionFileCreator('bot', null)).toBe('Bot 生成');
    expect(describeSessionFileCreator('user', undefined)).toBe('用户上传');
    expect(describeSessionFileCreator('bot', '')).toBe('Bot 生成');
    expect(describeSessionFileCreator('user', '   ')).toBe('用户上传');
  });

  it('creator_type null/缺失/未知值 → null（行内不渲染来源段，不猜测来源）', () => {
    expect(describeSessionFileCreator(null, '某名字')).toBeNull();
    expect(describeSessionFileCreator(undefined, null)).toBeNull();
    // 引擎将来新增枚举（如 'system'）→ 前端不猜测，与 null 合流。
    expect(describeSessionFileCreator('whatever-new', 'x')).toBeNull();
  });
});

describe('formatSessionUploadTime - created_at ISO 串 → 查看端本地时区 YYYY-MM-DD HH:mm:ss', () => {
  // 2026-11-12 用户反馈：展示时间需为查看端本地时间（北京时间即 UTC+8，+8 小时）。
  // 钉死 Asia/Shanghai 使断言跨 CI 时区确定；跑完恢复，避免污染同 worker 的其他套件。
  const originalTZ = process.env.TZ;
  beforeAll(() => {
    process.env.TZ = 'Asia/Shanghai';
  });
  afterAll(() => {
    process.env.TZ = originalTZ;
  });

  it('ISO Z 串取本地时区分量（东八区显示 +8 小时，不与后端原值逐字一致）', () => {
    // 2026-11-09T02:15:30Z → 北京时间 10:15:30（曾经 UTC 逐字展示被反馈少 8 小时）。
    expect(formatSessionUploadTime('2026-11-09T02:15:30Z')).toBe('2026-11-09 10:15:30');
    expect(formatSessionUploadTime('2026-11-09T02:15:30Z')).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);
  });

  it('吃微秒精度 ISO（2026-11-09 契约 created_at 秒级截断展示）', () => {
    // 后端 created_at 可能带 6 位微秒；new Date 按毫秒截断解析，格式化只取秒级分量。北京时间 +8 → 10:xx:xx。
    expect(formatSessionUploadTime('2026-11-09T02:15:30.123456Z')).toBe('2026-11-09 10:15:30');
    expect(formatSessionUploadTime('2026-11-09T02:10:00.999999Z')).toBe('2026-11-09 10:10:00');
  });

  it('带偏移的 ISO 串按其 UTC 瞬时取本地分量（同瞬时不同写法显示一致）', () => {
    // 2026-11-09T10:15:30+08:00 与 02:15:30Z 同瞬时，北京时间均显示 10:15:30。
    expect(formatSessionUploadTime('2026-11-09T10:15:30+08:00')).toBe('2026-11-09 10:15:30');
  });

  it('null / undefined / 空串 / 非法日期 → null（legacy 缺 created_at，行内不渲染时间段）', () => {
    expect(formatSessionUploadTime(null)).toBeNull();
    expect(formatSessionUploadTime(undefined)).toBeNull();
    expect(formatSessionUploadTime('')).toBeNull();
    expect(formatSessionUploadTime('not-a-date')).toBeNull();
  });
});
