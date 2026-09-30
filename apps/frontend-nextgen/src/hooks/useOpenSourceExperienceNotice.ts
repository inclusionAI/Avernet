import { getCapabilities, type OpenSourceExperienceNoticeSpec } from '@/capabilities';
import {
  readAcknowledgedExperienceNoticeVersion,
  writeAcknowledgedExperienceNoticeVersion,
} from '@/services/experienceNotice/experienceNoticeStorage';
import { useCallback, useState } from 'react';

interface OpenSourceExperienceNoticeState {
  notice: OpenSourceExperienceNoticeSpec | null;
  visible: boolean;
  acknowledge: () => void;
}

export function useOpenSourceExperienceNotice(): OpenSourceExperienceNoticeState {
  const notice = getCapabilities().getOpenSourceExperienceNotice().value;
  const [acknowledgedVersion, setAcknowledgedVersion] = useState<string | null>(() =>
    notice ? readAcknowledgedExperienceNoticeVersion() : null,
  );

  const acknowledge = useCallback(() => {
    if (!notice) return;
    setAcknowledgedVersion(notice.version);
    writeAcknowledgedExperienceNoticeVersion(notice.version);
  }, [notice]);

  return {
    notice,
    visible: Boolean(notice && acknowledgedVersion !== notice.version),
    acknowledge,
  };
}
