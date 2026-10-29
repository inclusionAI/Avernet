// @asset-migrated: teamclaw 自研资产
import { formatArtifactUpdatedAt } from '@/assets/TaskPanel/artifactTime';
import { describe, expect, it } from '@jest/globals';

describe('formatArtifactUpdatedAt', () => {
  it('UTC epoch 时间串转换为北京时间', () => {
    expect(formatArtifactUpdatedAt('2026-10-23T10:00:00.000Z')).toBe('2026-10-23 18:00');
  });

  it('无时区后缀的 legacy 数据库时间串按北京时间解析', () => {
    expect(formatArtifactUpdatedAt('2026-10-01 10:00:00')).toBe('2026-10-01 10:00');
  });

  it('显式 +08:00 时间保持北京时间显示', () => {
    expect(formatArtifactUpdatedAt('2026-10-01T10:00:00+08:00')).toBe('2026-10-01 10:00');
  });

  it('非法时间不渲染', () => {
    expect(formatArtifactUpdatedAt('')).toBe('');
    expect(formatArtifactUpdatedAt('not-a-date')).toBe('');
  });
});
