import type { CollaborationDefinitionValidationDiagnostic } from '@/services/backend-api/BcnController';
import React from 'react';
import { canExecuteValidatedCollaboration, formatCollaborationValidationErrors } from '../utils/collaborationValidation';

export default function CollaborationValidationNotices({ warnings }: {
  warnings: CollaborationDefinitionValidationDiagnostic[];
}) {
  if (!warnings.length) return null;
  return <div role="status" className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
    {!canExecuteValidatedCollaboration(warnings) && <p className="mb-2 font-medium">
      当前服务仅支持校验和预览此流程，暂不能创建协作群。
    </p>}
    <ul className="space-y-2 break-words">
      {warnings.map((warning, index) => <li key={`${warning.code}:${warning.path}:${index}`}>
        {formatCollaborationValidationErrors([warning])}
      </li>)}
    </ul>
  </div>;
}
