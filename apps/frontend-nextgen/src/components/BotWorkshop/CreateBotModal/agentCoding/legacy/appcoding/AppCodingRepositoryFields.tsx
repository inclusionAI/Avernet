import { getCapabilities } from '@/capabilities';
import type { WorkflowItem } from '@/services/botWorkshop/agentCodingLegacyService';
import { CodeRepoListField, YuqueKbReposField } from '../aicoding/configFields';
import { fromCodeRepoItems, renderYuqueKbError, toCodeRepoItems } from './AppCodingConfigUtils';
import { ArchitectBotConfigField } from './ArchitectBotConfigField';
import { CodingWorkflowConfigField } from './CodingWorkflowConfigField';
interface AppCodingRepositoryFieldsProps {
  disabled: boolean;
  repoFieldsDisabled: boolean;
  backendRepos: string[];
  frontendRepos: string[];
  libRepos: string[];
  setBackendRepos: (value: string[]) => void;
  setFrontendRepos: (value: string[]) => void;
  setLibRepos: (value: string[]) => void;
  duplicateRepoUrls: Set<string>;
  yuqueKbRepos: { url: string; token: string }[];
  setYuqueKbRepos: (value: { url: string; token: string }[]) => void;
  yuqueKbErrors: Record<number, string>;
  setYuqueKbErrors: (value: Record<number, string>) => void;
  validatingYuque: boolean;
  yuqueTokenWarning: string | null;
  yuqueTooltipOpen: boolean;
  setYuqueTooltipOpen: (value: boolean) => void;
  architectBotId: string;
  setArchitectBotId: (value: string) => void;
  devflowWorkflow: WorkflowItem | null;
  setDevflowWorkflow: (value: WorkflowItem | null) => void;
}
export function AppCodingRepositoryFields(props: AppCodingRepositoryFieldsProps) {
  const videoUrl = getCapabilities().getAppCodingYuqueTokenGuideVideoUrl().value;
  return (
    <>
      {/* 后端代码仓库 */}
      <CodeRepoListField
        label="后端代码仓库"
        value={toCodeRepoItems(props.backendRepos)}
        onChange={(next) => props.setBackendRepos(fromCodeRepoItems(next))}
        disabled={props.disabled || props.repoFieldsDisabled}
        removeDisabled={props.repoFieldsDisabled}
        duplicateUrls={props.duplicateRepoUrls}
      />

      {/* 前端代码仓库 */}
      <CodeRepoListField
        label="前端代码仓库"
        value={toCodeRepoItems(props.frontendRepos)}
        onChange={(next) => props.setFrontendRepos(fromCodeRepoItems(next))}
        disabled={props.disabled || props.repoFieldsDisabled}
        removeDisabled={props.repoFieldsDisabled}
        duplicateUrls={props.duplicateRepoUrls}
      />

      {/* lib 代码仓库 */}
      <CodeRepoListField
        label="Lib 代码仓库"
        value={toCodeRepoItems(props.libRepos)}
        onChange={(next) => props.setLibRepos(fromCodeRepoItems(next))}
        disabled={props.disabled || props.repoFieldsDisabled}
        removeDisabled={props.repoFieldsDisabled}
        duplicateUrls={props.duplicateRepoUrls}
      />

      {/* 重复仓库地址提示 */}
      {props.duplicateRepoUrls.size > 0 && (
        <p className="text-[11px] text-red-500">
          存在重复的仓库地址：{Array.from(props.duplicateRepoUrls).join('、')}，同一 Bot 内不可配置重复仓库
        </p>
      )}

      {/* 语雀知识库 */}
      <YuqueKbReposField
        label="语雀团队知识库"
        value={props.yuqueKbRepos}
        onChange={(next) => {
          props.setYuqueKbRepos(
            next.map((repo) => ({
              url: repo.url || '',
              token: repo.token || '',
            })),
          );
          if (Object.keys(props.yuqueKbErrors).length > 0) {
            props.setYuqueKbErrors({});
          }
        }}
        disabled={props.disabled}
        errors={props.yuqueKbErrors}
        renderError={renderYuqueKbError}
        validating={props.validatingYuque}
        warning={props.yuqueTokenWarning}
        tooltipOpen={props.yuqueTooltipOpen}
        onTooltipOpenChange={props.setYuqueTooltipOpen}
        tooltipContent={
          <>
            <p className="mb-2">用于 Bot 知识库检索与 Memory 增强。</p>
            <p>获取团队 Token 方式：</p>
            <p>1. 语雀管理员进入语雀团队文档</p>
            <p>2. 设置-选择更多设置</p>
            <p>3. 选择token, 创建一个拥有 读取知识库/文档 权限的token</p>
            <p>4. 查看并复制token 的 Access, 粘贴进这里即可</p>
            {videoUrl && (
              <video
                src={videoUrl}
                autoPlay
                muted
                loop
                playsInline
                controls
                className="mt-1.5"
                style={{ width: 360 }}
                onFocus={() => props.setYuqueTooltipOpen(true)}
              />
            )}
          </>
        }
      />

      {/* 域架构 Bot */}
      <ArchitectBotConfigField
        value={props.architectBotId}
        disabled={props.disabled}
        onChange={props.setArchitectBotId}
      />

      {/* 研发工作流 */}
      <CodingWorkflowConfigField
        value={props.devflowWorkflow}
        disabled={props.disabled}
        onChange={props.setDevflowWorkflow}
      />
    </>
  );
}
