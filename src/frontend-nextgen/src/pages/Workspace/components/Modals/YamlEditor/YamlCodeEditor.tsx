import { cn } from '@/utils/cn';
import { Suspense, lazy } from 'react';
import { yamlEditorExtensions } from './yamlLanguage';

const LazyCodeMirror = lazy(() => import('@uiw/react-codemirror'));

export const YAML_EDITOR_PLACEHOLDER =
  'name: 自定义协作\nparticipants:\n  assistant:\n    display_name: 助手\n    required: true\nruntime:\n  kind: state_machine\n  state_machine:\n    nodes:\n      answer:\n        kind: bot_task\n        display_name: 输出结果\n        assignee:\n          type: bot_binding\n          binding: assistant\n        instruction: 请根据用户输入输出最终结果。\n        final_output: true';

export interface YamlCodeEditorProps {
  value: string;
  onChange: (value: string) => void;
  editable?: boolean;
  placeholder?: string;
  loading?: boolean;
  className?: string;
  /** 固定高度（px），内部可上下滚动；缺省 320。 */
  height?: number;
}

/** 带语法高亮的 YAML 编辑器（CodeMirror lazy load），固定高度、内部可滚动。 */
export function YamlCodeEditor({
  value,
  onChange,
  editable = true,
  placeholder = YAML_EDITOR_PLACEHOLDER,
  loading = false,
  className,
  height = 320,
}: YamlCodeEditorProps) {
  return (
    <div
      className={cn('overflow-hidden rounded-lg border border-[var(--color-border)] bg-white', className)}
      style={{ height }}
    >
      {loading ? (
        <div className="flex h-full items-center justify-center gap-2 text-sm text-[var(--color-muted)]">
          <span>加载模板内容...</span>
        </div>
      ) : (
        <Suspense
          fallback={
            <div className="flex h-full items-center justify-center gap-2 text-sm text-[var(--color-muted)]">
              <span>加载编辑器...</span>
            </div>
          }
        >
          <LazyCodeMirror
            value={value}
            editable={editable}
            height="100%"
            placeholder={placeholder}
            extensions={yamlEditorExtensions}
            onChange={onChange}
            basicSetup={{
              lineNumbers: true,
              foldGutter: true,
              highlightActiveLine: true,
              highlightActiveLineGutter: true,
              bracketMatching: true,
            }}
            theme="light"
            style={{ height: '100%', fontSize: 13 }}
            className="h-full text-sm"
          />
        </Suspense>
      )}
    </div>
  );
}

export default YamlCodeEditor;
