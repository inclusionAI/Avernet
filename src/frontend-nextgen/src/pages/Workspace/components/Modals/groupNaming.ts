export function formatAutoGroupName(
  participantIds: string[],
  leaderId: string,
  leaderFirst: boolean,
  resolveName: (id: string) => string,
): string {
  const otherIds = participantIds.filter((id) => id !== leaderId);
  const orderedIds = leaderFirst ? [leaderId, ...otherIds] : [...otherIds, leaderId];
  const validNames = orderedIds.map((id) => resolveName(id).trim()).filter(Boolean);
  if (!validNames.length) return '协作群';
  return validNames.length > 5 ? `${validNames.slice(0, 5).join('、')}等` : validNames.join('、');
}
