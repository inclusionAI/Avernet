import { isLocalSkillPackageError, localSkillUploadErrorMessage } from '@/hooks/localSkillUploadErrors';

describe('local Skill upload errors', () => {
  it('describes the actionable validation reason', () => {
    expect(localSkillUploadErrorMessage({ data: { reason: 'multiple_skill_files' } })).toContain('多个 SKILL.md');
    expect(localSkillUploadErrorMessage({ data: { reason: 'MISSING_DESCRIPTION' } })).toContain('缺少 description');
    expect(localSkillUploadErrorMessage({ data: { reason: 'too_many_files' } })).toContain('500');
  });

  it('only claims package errors', () => {
    expect(isLocalSkillPackageError({ code: 400101 })).toBe(true);
    expect(isLocalSkillPackageError({ code: 413101 })).toBe(true);
    expect(isLocalSkillPackageError({ code: 400000 })).toBe(false);
  });

  it('uses a safe fallback for older or unknown responses', () => {
    const fallback = 'Skill 上传失败，请检查目录和 SKILL.md 后重试。';
    expect(localSkillUploadErrorMessage({ data: null, message: 'internal details' })).toBe(fallback);
    expect(localSkillUploadErrorMessage({ data: { reason: 'unknown' } })).toBe(fallback);
    expect(localSkillUploadErrorMessage({ data: { reason: 'constructor' } })).toBe(fallback);
    expect(localSkillUploadErrorMessage({ data: { reason: '__proto__' } })).toBe(fallback);
  });
});
