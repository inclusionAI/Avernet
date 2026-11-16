/** 会话文件与 Skill 能力独立；未知引擎保留原有行为，不扩大限制范围。 */
export function supportsBotSessionFiles(bot: { engine?: string; botType?: string } | null): boolean {
  return !!bot && bot.botType !== 'desktop' && bot.engine?.trim().toLowerCase() !== 'teclaw';
}
