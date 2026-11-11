import { EvalFileInput } from '@/components/BotWorkshop/ServiceBotEvaluation/EvalFileInput';
import { EvaluationHistoryCard } from '@/components/BotWorkshop/ServiceBotEvaluation/EvaluationHistoryCard';
import { EvaluationSubmissionCard } from '@/components/BotWorkshop/ServiceBotEvaluation/EvaluationSubmissionCard';
import { evaluationStatusLabel } from '@/components/BotWorkshop/ServiceBotEvaluation/evaluationStatus';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card';
import { Segmented } from '@/components/ui/Segmented';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Spin } from '@/components/ui/Spin';
import { Textarea } from '@/components/ui/Textarea';
import { useServiceBotEval } from '@/hooks/useServiceBotEval';
import { history, useParams, useSearchParams } from '@umijs/max';
import { RefreshCw } from 'lucide-react';
import { useMemo, useState } from 'react';

type EvalTab = 'simple_cert_submit' | 'restart_eval' | 'eval_history';

const resolveEvalTab = (value: string | null): EvalTab => {
  if (value === 'simple_cert_submit' || value === 'restart_eval' || value === 'eval_history') return value;
  return 'simple_cert_submit';
};
type GoalType = 'json' | 'semantic';

const EvaluationPage: React.FC = () => {
  const params = useParams();
  const [searchParams] = useSearchParams();
  const botId = params.botId ?? '';
  const botVersion = params.botVersion ?? '';
  const ownerId = searchParams.get('owner_id') ?? undefined;
  const evaluation = useServiceBotEval(botId, botVersion, ownerId);
  const detail = evaluation.detail as Record<string, any> | undefined;
  const tabs = detail?.tabs as
    | Record<string, { enabled?: boolean; visible?: boolean; visible_reason?: string }>
    | undefined;
  const certState = detail?.cert_state as Record<string, unknown> | undefined;
  const initialTab = resolveEvalTab(searchParams.get('tab'));
  const [activeTab, setActiveTab] = useState<EvalTab>(initialTab);
  const [sampleFile, setSampleFile] = useState<File>();
  const [reportFile, setReportFile] = useState<File>();
  const [datasetFile, setDatasetFile] = useState<File>();
  const [goalFile, setGoalFile] = useState<File>();
  const [datasetType, setDatasetType] = useState('jsonl');
  const [goalType, setGoalType] = useState<GoalType>('json');
  const [goalPrompt, setGoalPrompt] = useState('');

  const options = useMemo(() => {
    const tabOptions: Array<{ value: EvalTab; label: string }> = [
      { value: 'simple_cert_submit', label: '评测数据上报认证' },
      { value: 'restart_eval', label: '重新发起评测' },
      { value: 'eval_history', label: '历史评测记录' },
    ];
    return tabOptions.filter((option) => {
      if (option.value === 'simple_cert_submit') return tabs?.simple_cert_submit?.visible !== false;
      if (option.value === 'restart_eval') return tabs?.restart_eval?.visible !== false;
      return true;
    });
  }, [tabs]);

  const effectiveTab = options.some((option) => option.value === activeTab)
    ? activeTab
    : options[0]?.value ?? 'eval_history';

  const hasSimpleFiles = Boolean(sampleFile && reportFile);
  const hasRestartFiles = Boolean(datasetFile && (goalType === 'json' ? goalFile : true));

  const submitSimple = async () => {
    if (!sampleFile || !reportFile) return;
    await evaluation.submit([sampleFile], [reportFile], 'manual_simple_cert');
  };

  const submitRestart = async () => {
    if (!datasetFile) return;
    const reportFile: File | undefined =
      goalType === 'json'
        ? goalFile
        : new File([goalPrompt || '{}'], 'evaluation-goal.json', { type: 'application/json' });
    if (!reportFile) return;
    await evaluation.submit([datasetFile], [reportFile], 'manual_restart_eval');
  };

  const certStatus = String(certState?.cert_status ?? '');
  const certResult = certState?.cert_result as string | null | undefined;

  return (
    <main className="app-scrollbar h-full overflow-y-auto bg-page-canvas">
      <div className="w-full max-w-7xl p-5 sm:p-6">
        <Button variant="link" onClick={() => history.push('/bot-workshop')}>
          ← 返回 Bot 管理
        </Button>
        <Card className="mt-4">
          <CardHeader>
            <div>
              <CardTitle>评测详情</CardTitle>
              <p className="mt-1 text-xs text-muted-foreground">
                提供评测数据上报认证、重新发起评测和历史评测记录入口。
              </p>
            </div>
            <Badge tone={certResult?.toUpperCase() === 'PASS' ? 'success' : 'neutral'}>
              {certResult
                ? evaluationStatusLabel(certResult)
                : certStatus
                ? evaluationStatusLabel(certStatus)
                : '未认证'}
            </Badge>
          </CardHeader>
          <CardContent>
            {evaluation.loading ? (
              <Spin tip="加载评测详情…" />
            ) : (
              <>
                <div className="grid gap-3 rounded-lg bg-muted/40 p-4 text-xs md:grid-cols-4">
                  <div>
                    <p className="font-medium text-muted-foreground">Bot ID</p>
                    <p className="mt-1 truncate font-medium text-foreground">{botId || '—'}</p>
                  </div>
                  <div>
                    <p className="font-medium text-muted-foreground">Bot Version</p>
                    <p className="mt-1 font-medium text-foreground">{botVersion || '—'}</p>
                  </div>
                  <div>
                    <p className="font-medium text-muted-foreground">认证摘要</p>
                    <p className="mt-1 line-clamp-2 font-medium text-foreground">
                      {String(certState?.cert_summary ?? certState?.gateway_status ?? '暂无认证结论')}
                    </p>
                  </div>
                  <div className="flex items-end justify-start md:justify-end">
                    <Button
                      variant="secondary"
                      size="sm"
                      leftIcon={<RefreshCw className="size-3.5" />}
                      onClick={() => void evaluation.sync()}
                    >
                      同步认证状态
                    </Button>
                  </div>
                </div>
                {evaluation.actionError ? (
                  <p className="mt-3 rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">
                    {evaluation.actionError}
                  </p>
                ) : null}
                <div className="mt-4">
                  <Segmented<EvalTab>
                    value={effectiveTab}
                    options={options}
                    onChange={setActiveTab}
                    className="w-full max-w-3xl"
                    activeOptionClassName="text-primary"
                  />
                </div>
                <div className="mt-4 rounded-lg border border-border p-4">
                  {effectiveTab === 'simple_cert_submit' ? (
                    <div className="space-y-4">
                      <p className="m-0 text-xs text-muted-foreground">
                        已有评测样本集和结果报告时，可直接上传并提交认证审核。
                      </p>
                      <div className="grid gap-3 md:grid-cols-2">
                        <EvalFileInput label="评测样本集" file={sampleFile} onSelect={setSampleFile} />
                        <EvalFileInput
                          label="评测结果报告"
                          accept=".json,.pdf,.md"
                          file={reportFile}
                          onSelect={setReportFile}
                        />
                      </div>
                      <div className="flex flex-wrap justify-end gap-2">
                        <Button onClick={() => void submitSimple()} disabled={!hasSimpleFiles || evaluation.submitting}>
                          {evaluation.submitting ? '提交中…' : '提交认证审核'}
                        </Button>
                      </div>
                    </div>
                  ) : effectiveTab === 'restart_eval' ? (
                    <div className="space-y-4">
                      <p className="m-0 text-xs text-muted-foreground">适合需要重新发起评测并重新编排执行的链路。</p>
                      <div className="grid gap-3 md:grid-cols-2">
                        <div className="space-y-1.5">
                          <label className="text-xs font-medium text-muted-foreground" htmlFor="dataset-type">
                            评测集格式
                          </label>
                          <Select value={datasetType} onValueChange={setDatasetType}>
                            <SelectTrigger id="dataset-type" className="h-9 text-sm">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="jsonl">JSONL</SelectItem>
                              <SelectItem value="csv">CSV</SelectItem>
                              <SelectItem value="odps">ODPS</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                        <EvalFileInput label="评测集文件" file={datasetFile} onSelect={setDatasetFile} />
                        <div className="space-y-1.5">
                          <label className="text-xs font-medium text-muted-foreground" htmlFor="goal-type">
                            评测目标格式
                          </label>
                          <Select value={goalType} onValueChange={(value) => setGoalType(value as GoalType)}>
                            <SelectTrigger id="goal-type" className="h-9 text-sm">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="json">JSON</SelectItem>
                              <SelectItem value="semantic">自然语义</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                        {goalType === 'json' ? (
                          <EvalFileInput label="评测目标文件" accept=".json" file={goalFile} onSelect={setGoalFile} />
                        ) : null}
                      </div>
                      {goalType === 'semantic' ? (
                        <Textarea
                          className="text-sm"
                          placeholder="例如：评估该 Bot 在金融政策问答场景下的准确率和安全性。"
                          value={goalPrompt}
                          onChange={(event) => setGoalPrompt(event.target.value)}
                        />
                      ) : null}
                      <div className="flex flex-wrap justify-end gap-2">
                        <Button
                          onClick={() => void submitRestart()}
                          disabled={!hasRestartFiles || evaluation.submitting}
                        >
                          {evaluation.submitting ? '提交中…' : '上报评测数据并认证'}
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <EvaluationHistoryCard records={evaluation.history?.records} />
                  )}
                </div>
                <EvaluationSubmissionCard submission={evaluation.submission} />
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </main>
  );
};

export default EvaluationPage;
