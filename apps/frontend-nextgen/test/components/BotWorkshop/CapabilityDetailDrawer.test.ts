import { splitFrontmatter } from '@/components/BotWorkshop/Editor/CapabilityDetailDrawer';

describe('splitFrontmatter', () => {
  it('剥离 frontmatter 并提取 name/description', () => {
    const content = [
      '---',
      'name: teamclaw-cli',
      'description: 同步路由技能。',
      'argument-hint: "[agent|skill]"',
      'tags:',
      '  - sync',
      '---',
      '',
      '# teamclaw-cli',
    ].join('\n');
    const { meta, body } = splitFrontmatter(content);
    expect(meta).toEqual({ name: 'teamclaw-cli', description: '同步路由技能。' });
    expect(body).toBe('\n# teamclaw-cli');
    expect(body).not.toContain('argument-hint');
  });

  it('折行续行并入 description，列表项不并入', () => {
    const content = ['---', 'name: a', 'description: 第一行', '  续行', 'tags:', '  - t', '---', 'body'].join('\n');
    expect(splitFrontmatter(content).meta).toEqual({ name: 'a', description: '第一行 续行' });
  });

  it('无 frontmatter 时原样返回', () => {
    const { meta, body } = splitFrontmatter('# title\n正文');
    expect(meta).toBeNull();
    expect(body).toBe('# title\n正文');
  });
});
