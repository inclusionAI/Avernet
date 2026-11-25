import { Points } from '@/tracking/points';

/**
 * SPM 码值防泄漏扫描（add-spm-tracking，spec「开源侧零 SPM 编码」要求的自动化护栏）：
 * 语义点位常量的值必须是平台注册的中文点位名；任何 a/b/c/d 位码值出现在本表即视为边界破坏。
 */

function collectPointValues(obj: unknown): string[] {
  if (typeof obj === 'string') return [obj];
  if (obj && typeof obj === 'object') {
    return Object.values(obj).flatMap(collectPointValues);
  }
  return [];
}

// SPM 单段编码形态：a5589 / b264652 / c472691 / d703158 等（字母+3位以上数字）
const SPM_CODE_PATTERN = /(?:^|[^a-zA-Z])[a-dA-D]\d{3,}(?:\.[^ ]*)?/;

describe('语义点位常量表（src/tracking/points.ts）', () => {
  const values = collectPointValues(Points);

  it('每个点位值都是中文点位名（含 CJK 字符），不出现 SPM 码值', () => {
    for (const v of values) {
      expect(v.length).toBeGreaterThan(0);
      expect(/[一-龥]/.test(v)).toBe(true);
      expect(SPM_CODE_PATTERN.test(v)).toBe(false);
    }
  });

  it('点位值不含内部站点标识', () => {
    // 本断言在 Points 骨架为空时平凡通过；随 P0 清单落表后即时生效
    for (const v of values) {
      expect(v).not.toMatch(/a5589/i);
    }
  });
});