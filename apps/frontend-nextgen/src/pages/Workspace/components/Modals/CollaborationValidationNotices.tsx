import {
  canExecuteValidatedCollaboration,
  formatValidationErrors,
  type DefinitionValidation,
} from '@/services/workspace/collaborationDefinitionService';

export default function CollaborationValidationNotices({ warnings }: {
  warnings: DefinitionValidation['warnings'];
}) {
  if (!warnings.length) return null;
  return (
    <div role="status" className="rounded-xl border border-warning/30 bg-warning/10 p-3 text-xs text-warning">
      {!canExecuteValidatedCollaboration(warnings) && (
        <p className="mb-2 font-medium">当前服务仅支持校验和预览此流程，暂不能创建协作群。</p>
      )}
      <ul className="space-y-2 break-words">
        {warnings.map((warning, index) => (
          <li key={`${warning.code}:${warning.path}:${index}`}>{formatValidationErrors([warning])}</li>
        ))}
      </ul>
    </div>
  );
}
