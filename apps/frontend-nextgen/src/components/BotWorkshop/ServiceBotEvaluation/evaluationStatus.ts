export type EvaluationStatusTone = 'success' | 'error' | 'warning' | 'neutral';

export const evaluationStatusTone = (status?: string): EvaluationStatusTone => {
  if (!status) return 'neutral';
  if (status.toUpperCase() === 'PASS' || status.toUpperCase() === 'SUCCESS') return 'success';
  if (status.toUpperCase() === 'FAIL' || status.toUpperCase() === 'FAILED') return 'error';
  if (['INIT', 'RUNNING', 'PENDING'].includes(status.toUpperCase())) return 'warning';
  return 'neutral';
};

export const evaluationStatusLabel = (status?: string) => {
  if (!status) return '待处理';
  if (status.toUpperCase() === 'PASS' || status.toUpperCase() === 'SUCCESS') return '通过';
  if (status.toUpperCase() === 'FAIL' || status.toUpperCase() === 'FAILED') return '未通过';
  if (['INIT', 'RUNNING', 'PENDING'].includes(status.toUpperCase())) return '处理中';
  return status;
};
