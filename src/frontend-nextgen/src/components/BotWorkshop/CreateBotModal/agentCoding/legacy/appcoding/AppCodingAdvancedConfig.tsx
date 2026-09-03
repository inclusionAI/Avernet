import { ChevronDown, Settings2 } from 'lucide-react';
import type { ModelOption } from './CodingModelConfigField';
import { CodingModelConfigField } from './CodingModelConfigField';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from './compat/Collapsible';
import { CustomImageConfigField } from './CustomImageConfigField';
import { ResourceSpecFields } from './ResourceSpecFields';
interface AppCodingAdvancedConfigProps {
  disabled: boolean;
  initialConfig?: Record<string, any>;
  modelOptions?: ModelOption[];
  modelsLoading: boolean;
  modelsLoadError: string | null;
  botId?: string;
  onReloadModels?: () => void | Promise<void>;
  onModelChange: (config: Record<string, any>) => void;
  onModelValidationChange: (error: string | null) => void;
  image: string;
  setImage: (value: string) => void;
  resourceSpecEnabled: boolean;
  resourceCpu: string;
  resourceMemory: string;
  resourceDisk: string;
  setResourceCpu: (value: string) => void;
  setResourceMemory: (value: string) => void;
  setResourceDisk: (value: string) => void;
}
export function AppCodingAdvancedConfig(props: AppCodingAdvancedConfigProps) {
  return (
    <Collapsible defaultOpen={false}>
      <CollapsibleTrigger className="group flex h-auto min-h-0 w-full cursor-pointer items-center justify-start gap-1.5 rounded-none border-0 bg-transparent px-0 py-1 text-left text-xs font-semibold text-slate-600 shadow-none transition-colors hover:bg-transparent hover:text-slate-700">
        <Settings2 size={13} className="text-slate-400 transition-colors group-hover:text-slate-500" />
        <span>高级配置</span>
        <ChevronDown
          size={12}
          className="text-slate-400 transition-transform duration-200 group-data-[state=open]:rotate-180"
        />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="space-y-3 px-1 pb-1 pt-2">
          <CodingModelConfigField
            disabled={props.disabled}
            initialConfig={props.initialConfig}
            onChange={props.onModelChange}
            onValidationChange={props.onModelValidationChange}
            modelOptions={props.modelOptions}
            modelsLoading={props.modelsLoading}
            modelsLoadError={props.modelsLoadError}
            botId={props.botId}
            onReloadModels={props.onReloadModels}
          />
          <CustomImageConfigField value={props.image} onChange={props.setImage} disabled={props.disabled} />
          <ResourceSpecFields
            enabled={props.resourceSpecEnabled}
            disabled={props.disabled}
            cpu={props.resourceCpu}
            memory={props.resourceMemory}
            disk={props.resourceDisk}
            setCpu={props.setResourceCpu}
            setMemory={props.setResourceMemory}
            setDisk={props.setResourceDisk}
          />
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}
