/** @jest-environment node */
import { compareArtifactsByCreationOrder } from '@/assets/TaskPanel/artifactDescriptor';
import type { TaskArtifactView } from '@/assets/TaskPanel/types';
import { describe, expect, it } from '@jest/globals';

// 排序兜底分支补充:created_at 为毫秒数字符串(Date.parse 失败但可 Number)的口径、
// 以及同 timestamps 且无 sourceOrder 时按 artifact_id 数值序稳定兜底。

const view = (id: string, updatedAt: string, sourceOrder?: number): TaskArtifactView =>
  ({
    id,
    name: id,
    type: 'file',
    updatedAt,
    ...(sourceOrder === undefined ? {} : { sourceOrder }),
  }) as TaskArtifactView;

describe('compareArtifactsByCreationOrder', () => {
  it('created_at 为毫秒数字符串 → 按 Number 折算的时间参与正序排序', () => {
    const numericMs = view('a', '1740000000000');
    const isoEarly = view('b', '2024-01-01T00:00:00Z');
    // 数字串 1740000000000 晚于 2024-01-01 → b 在前
    expect(compareArtifactsByCreationOrder(numericMs, isoEarly)).toBeGreaterThan(0);
    expect(compareArtifactsByCreationOrder(isoEarly, numericMs)).toBeLessThan(0);
  });

  it('created_at 完全不可解析 → null 时间排在可解析产物之后(双侧对称)', () => {
    const broken = view('a', '不是时间戳');
    const normal = view('b', '2026-10-01T00:00:00Z');
    expect(compareArtifactsByCreationOrder(broken, normal)).toBe(1);
    expect(compareArtifactsByCreationOrder(normal, broken)).toBe(-1);
  });

  it('同时间同无 sourceOrder → 按 artifact_id 数值序稳定兜底', () => {
    const a10 = view('a-10', '2026-10-01T00:00:00Z');
    const a2 = view('a-2', '2026-10-01T00:00:00Z');
    // numeric 比较 a-2 < a-10(不受字典序 a-10 < a-2 影响)
    expect(compareArtifactsByCreationOrder(a2, a10)).toBeLessThan(0);
    expect(compareArtifactsByCreationOrder(a10, a2)).toBeGreaterThan(0);
  });

  it('同时间携带 sourceOrder → 反转输入序还原后端 id 正序', () => {
    const first = view('a', '2026-10-01T00:00:00Z', 0);
    const second = view('b', '2026-10-01T00:00:00Z', 1);
    // 后端按 id 倒序给出 → 反转输入序:sourceOrder 大的排前面
    expect(compareArtifactsByCreationOrder(second, first)).toBeLessThan(0);
  });
});
