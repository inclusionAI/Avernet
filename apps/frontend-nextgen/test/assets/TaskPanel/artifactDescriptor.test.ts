/** @jest-environment node */
import {
  artifactDescriptorWorkspaceFile,
  artifactDescriptorSessionFile,
  artifactDescriptorUrlLink,
  artifactDescriptorCode,
  artifactDescriptorText,
  descriptorList,
  legacyArtifacts,
} from '@/assets/TaskPanel/testdata/artifactFixtures';
import {
  artifactViewHref,
  mergeArtifactLists,
  normalizeDescriptor,
} from '@/assets/TaskPanel/artifactDescriptor';

describe('normalizeDescriptor', () => {
  it('url_link 分支:kind/url/creator/nodeId/时间戳 ISO 化', () => {
    const view = normalizeDescriptor(artifactDescriptorUrlLink, 'n2v');
    expect(view.id).toBe('art-url');
    expect(view.kind).toBe('url_link');
    expect(view.type).toBe('link');
    expect(view.url).toBe('https://yuque.example/doc1');
    expect(view.owner).toBe('group-902');
    expect(view.nodeId).toBe('n2v');
    expect(view.updatedAt).toBe(new Date(1729400000000).toISOString());
  });

  it('file 分支:bot_workspace 产 fileRef,缺 artifact_name 回落 file_name', () => {
    const view = normalizeDescriptor(artifactDescriptorWorkspaceFile, 'n1');
    expect(view.name).toBe('竞品分析报告.md');
    expect(view.kind).toBe('file');
    expect(view.fileRef).toEqual({
      storageType: 'bot_workspace',
      storageId: 'workspace/report.md',
      fileId: null,
      fileType: 'text/markdown',
    });
    expect(view.url).toBeNull();
  });

  it('file 分支:session_file 带 file_id', () => {
    const view = normalizeDescriptor(artifactDescriptorSessionFile, 'n2');
    expect(view.fileRef).toMatchObject({ storageType: 'session_file', storageId: 'sess-9', fileId: 'f-1' });
  });

  it('兼容旧 Mock 的大写 storage_type,归一化为后端小写 canonical 值', () => {
    const view = normalizeDescriptor(
      {
        ...artifactDescriptorSessionFile,
        content: { ...artifactDescriptorSessionFile.content, storage_type: 'SESSION_FILE' },
      },
      'n2',
    );
    expect(view.fileRef?.storageType).toBe('session_file');
  });

  it('text 分支:summary 承载文本,无链接', () => {
    const view = normalizeDescriptor(artifactDescriptorText, 'n1');
    expect(view.kind).toBe('text');
    expect(view.type).toBe('document');
    expect(view.summary).toBe('一段纯文本结论');
    expect(artifactViewHref(view)).toBeNull();
  });

  it('code 分支:type=other,url 指向 pr_link', () => {
    const view = normalizeDescriptor(artifactDescriptorCode, 'n2');
    expect(view.type).toBe('other');
    expect(view.url).toBe('https://git.example/pr/1024');
  });

  it('content 非 object(脏数据)不崩溃,回落 id 为名', () => {
    const view = normalizeDescriptor(
      { artifact_id: 'x', artifact_name: null, kind: 'file', content: null as never, created_at: 1 },
      'n1',
    );
    expect(view.id).toBe('x');
    expect(view.fileRef).toBeNull();
  });
});

describe('mergeArtifactLists', () => {
  it('Descriptor 优先,同 id 与 legacy 去重不重复渲染(Review Focus #3)', () => {
    const merged = mergeArtifactLists(descriptorList, legacyArtifacts, 'n1');

    const ids = merged.map((a) => a.id);
    expect(new Set(ids).size).toBe(ids.length); // 无重复
    expect(ids).toContain('art-url');
    expect(ids).toContain('legacy-only');
    expect(merged.find((a) => a.id === 'both-common')?.kind).toBe('url_link'); // Descriptor 口径胜出
  });

  it('Descriptor 为 null/undefined(后端未富化)时仅回退 legacy', () => {
    const mergedNull = mergeArtifactLists(null, legacyArtifacts, 'n1');
    const mergedUndef = mergeArtifactLists(undefined, legacyArtifacts, 'n1');
    expect(mergedNull).toHaveLength(legacyArtifacts.length);
    expect(mergedUndef).toHaveLength(legacyArtifacts.length);
  });

  it('两者皆空 → 空数组', () => {
    expect(mergeArtifactLists(null, [], 'n1')).toEqual([]);
    expect(mergeArtifactLists([], undefined, 'n1')).toEqual([]);
  });

  it('legacy 项 nodeId 缺省时回落入参 nodeId', () => {
    const merged = mergeArtifactLists(null, legacyArtifacts, 'n1');
    expect(merged.find((a) => a.id === 'legacy-only')?.nodeId).toBe('n1');
  });
});

describe('artifactViewHref', () => {
  const ws = normalizeDescriptor(artifactDescriptorWorkspaceFile, 'n1');
  const sf = normalizeDescriptor(artifactDescriptorSessionFile, 'n2');
  const link = normalizeDescriptor(artifactDescriptorUrlLink, 'n2v');
  const code = normalizeDescriptor(artifactDescriptorCode, 'n2');

  it('Descriptor file 不猜测 HTTP 地址,统一交给产物预览 Hook', () => {
    expect(artifactViewHref(ws)).toBeNull();
    expect(artifactViewHref(sf)).toBeNull();
  });

  it('url_link → 业务链接;code → pr_link;text → null', () => {
    expect(artifactViewHref(link)).toBe('https://yuque.example/doc1');
    expect(artifactViewHref(code)).toBe('https://git.example/pr/1024');
  });

  it('无 fileRef 的 file 视图 → null(不猜测链接)', () => {
    expect(artifactViewHref({ ...ws, fileRef: null })).toBeNull();
  });
});
