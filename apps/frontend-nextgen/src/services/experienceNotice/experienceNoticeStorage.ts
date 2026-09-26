export const EXPERIENCE_NOTICE_STORAGE_KEY = 'teamclaw:open-source-experience-notice:acknowledged-version';

type ExperienceNoticeStorage = Pick<Storage, 'getItem' | 'setItem'>;

function getBrowserStorage(): ExperienceNoticeStorage | null {
  if (typeof window === 'undefined') return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function readAcknowledgedExperienceNoticeVersion(
  storage: ExperienceNoticeStorage | null = getBrowserStorage(),
): string | null {
  if (!storage) return null;
  try {
    return storage.getItem(EXPERIENCE_NOTICE_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function writeAcknowledgedExperienceNoticeVersion(
  version: string,
  storage: ExperienceNoticeStorage | null = getBrowserStorage(),
): boolean {
  if (!storage) return false;
  try {
    storage.setItem(EXPERIENCE_NOTICE_STORAGE_KEY, version);
    return true;
  } catch {
    return false;
  }
}
