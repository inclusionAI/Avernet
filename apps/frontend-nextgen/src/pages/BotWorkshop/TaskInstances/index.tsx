import { history, useLocation } from '@umijs/max';
import { useEffect } from 'react';
/** Preserve existing deep links and their Bot/owner query parameters. */
export default function LegacyTaskInstancesPage() {
  const location = useLocation();
  useEffect(() => { history.replace(`/bot-workshop/scheduled-tasks${location.search}`); }, [location.search]);
  return null;
}
