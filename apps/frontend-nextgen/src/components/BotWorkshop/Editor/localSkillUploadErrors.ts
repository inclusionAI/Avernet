const messages: Record<string, string> = {
  missing_skill_file: '未找到 SKILL.md，请确认选择的是单个 Skill 目录。',
  multiple_skill_files: '所选目录包含多个 SKILL.md，一次只能上传一个 Skill，请选择其中一个 Skill 的目录。',
  invalid_wrapper: '文件不在同一个 Skill 目录中，请重新选择目录。',
  wrapper_name_mismatch: 'Skill 目录名需要与 SKILL.md 中的 name 一致。',
  unsafe_file_path: '文件路径不合法，请检查目录后重试。',
  duplicate_file_path: '目录中存在重复的文件路径，请检查后重试。',
  invalid_zip: 'Skill 压缩包无效，请重新打包。',
  unreadable_archive: '无法读取 Skill 压缩包，请重新打包。',
  invalid_encoding: 'SKILL.md 编码不正确，请检查文件编码。',
  INVALID_ENCODING: 'SKILL.md 编码不正确，请检查文件编码。',
  invalid_metadata: 'SKILL.md 元数据无效，请检查 name 和 description。',
  MISSING_FRONTMATTER: 'SKILL.md 缺少 YAML 元数据，请检查文件格式。',
  INVALID_FRONTMATTER: 'SKILL.md 的 YAML 元数据格式不正确，请检查后重试。',
  MISSING_NAME: 'SKILL.md 缺少 name，请补充后重试。',
  MISSING_DESCRIPTION: 'SKILL.md 缺少 description，请补充后重试。',
  INVALID_NAME_TYPE: 'SKILL.md 的 name 必须是文本。',
  INVALID_DESCRIPTION_TYPE: 'SKILL.md 的 description 必须是文本。',
  EMPTY_NAME: 'SKILL.md 的 name 不能为空。',
  EMPTY_DESCRIPTION: 'SKILL.md 的 description 不能为空。',
  NAME_TOO_LONG: 'SKILL.md 的 name 过长，请缩短后重试。',
  DESCRIPTION_TOO_LONG: 'SKILL.md 的 description 过长，请缩短后重试。',
  INVALID_NAME: 'name 只能包含英文字母、数字和连字符 -。',
  RESERVED_NAME: 'name 为系统保留名称，请更换后重试。',
  file_too_large: '单个文件超过 10 MiB，请删除或缩小文件后重试。',
  too_many_files: '文件数超过 500 个，请减少文件后重试。',
  expanded_too_large: '文件总大小超过 50 MiB，请减少文件后重试。',
  compressed_too_large: '压缩后的 Skill 包超过 10 MiB，请减少文件后重试。',
};

export function localSkillUploadErrorMessage(payload: unknown): string {
  if (payload && typeof payload === 'object' && 'data' in payload) {
    const data = payload.data;
    if (data && typeof data === 'object' && 'reason' in data && typeof data.reason === 'string') {
      return Object.prototype.hasOwnProperty.call(messages, data.reason)
        ? messages[data.reason]
        : 'Skill 上传失败，请检查目录和 SKILL.md 后重试。';
    }
  }
  return 'Skill 上传失败，请检查目录和 SKILL.md 后重试。';
}

export function isLocalSkillPackageError(payload: unknown): boolean {
  if (!payload || typeof payload !== 'object' || !('code' in payload)) return false;
  return payload.code === 400101 || payload.code === 413101;
}
