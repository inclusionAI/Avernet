/** @jest-environment node */
// artifactDescriptor 归一化的边角分支：非法时间戳、非 file 缺名回落、legacy link 产物 linkRef。
import { artifactViewHref, mergeArtifactLists, normalizeDescriptor } from '@/assets/TaskPanel/artifactDescriptor';
import type { TaskArtifactDescriptorDto } from '@/assets/TaskPanel/contract';
import type { TaskArtifactView } from '@/assets/TaskPanel/types';
import { describe, expect, it } from '@jest/globals';

describe('normalizeDescriptor 边角分支', () => {
  it('created_at 非法(NaN)→ msToIso 回退 String(ms)', () => {
    const view = normalizeDescriptor(
      {
        artifact_id: 'art-bad',
        artifact_name: null,
        kind: 'text',
        content: { text: 'x' },
        created_at: Number.NaN,
      } as TaskArtifactDescriptorDto,
      'n1',
    );
    // new Date(NaN).getTime() 为 NaN → 走 String(ms) 分支
    expect(Number.isNaN(new Date(NaN).getTime())).toBe(true);
    expect(view.updatedAt).toBe(String(Number.NaN));
  });

  it('非 file 类且 artifact_name 缺省 → name 回落 artifact_id(命中 inner ternary 的 null 臂)', () => {
    const view = normalizeDescriptor(
      {
        artifact_id: 'art-noname',
        artifact_name: null,
        kind: 'text',
        content: { text: 'hi' },
        created_at: 1,
      } as TaskArtifactDescriptorDto,
      'n1',
    );
    expect(view.name).toBe('art-noname');
    expect(view.kind).toBe('text');
  });
});

describe('mergeArtifactLists legacy link 产物', () => {
  it('legacy type=link 产物 → linkRef 带 url(命中 legacyToView 的 link 臂)', () => {
    const merged = mergeArtifactLists(
      null,
      [
        {
          artifact_id: 'leg-link',
          name: 'L',
          type: 'link',
          url: 'https://leg.example',
          created_at: '2026-10-01 10:00:00',
        },
      ],
      'n1',
    );
    expect(merged).toHaveLength(1);
    expect(merged[0].linkRef).toEqual({ url: 'https://leg.example', type: null });
    expect(merged[0].codeRef).toBeNull();
  });

  it('legacy type=link 且 url 缺省 → linkRef.url 为 null(命中 a.url ?? null 的 null 臂)', () => {
    const merged = mergeArtifactLists(
      null,
      [{ artifact_id: 'leg-link-null', name: 'L', type: 'link', url: null, created_at: '2026-10-01 10:00:00' }],
      'n1',
    );
    expect(merged[0].linkRef).toEqual({ url: null, type: null });
  });
});

describe('artifactViewHref url 缺省与 legacy(kind=null) 兜底', () => {
  const base = (overrides: Partial<TaskArtifactView>): TaskArtifactView =>
    ({
      id: 'a',
      name: 'n',
      type: 'document',
      kind: null,
      url: null,
      summary: null,
      linkRef: null,
      codeRef: null,
      nodeId: null,
      owner: null,
      updatedAt: '',
      fileRef: null,
      ...overrides,
    }) as TaskArtifactView;

  it('url_link/code 的 url 缺省 → null(命中 a.url ?? null 的 null 臂)', () => {
    expect(artifactViewHref(base({ kind: 'url_link', url: null }))).toBeNull();
    expect(artifactViewHref(base({ kind: 'code', url: null }))).toBeNull();
  });

  it('legacy(kind=null) 带 url → 直接返回 url(命中末尾 return a.url ?? null)', () => {
    expect(artifactViewHref(base({ kind: null, url: 'https://legacy.example' }))).toBe('https://legacy.example');
  });

  it('legacy(kind=null) url 缺省 → null(命中末尾 a.url ?? null 的 null 臂)', () => {
    expect(artifactViewHref(base({ kind: null, url: null }))).toBeNull();
  });
});
