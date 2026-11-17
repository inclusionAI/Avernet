// 带 JSON 语法高亮的 CodeMirror 编辑器（懒加载），样式对齐旧版 EngineConfigDrawer。
import { jsonEditorExtensions } from '@/components/BotWorkshop/Editor/jsonLanguage';
import { cn } from '@/utils/cn';
import { Suspense, lazy } from 'react';

const LazyCodeMirror = lazy(() => import('@uiw/react-codemirror'));

export interface JsonCodeEditorProps {
  value: string;
  onChange: (value: string) => void;
  editable?: boolean;
  className?: string;
}

/** JSON 编辑器：撑满父容器剩余高度，内部可滚动；测试可通过 mock 该模块替换为 textarea。 */
export function JsonCodeEditor({ value, onChange, editable = true, className }: JsonCodeEditorProps) {
  return (
    <div
      // 编辑器固定浅色外观（对齐旧版 EngineConfigDrawer），不随 .dark 翻转
      className={cn(
        'flex min-h-[440px] flex-1 flex-col overflow-hidden rounded-lg border border-slate-200 bg-white',
        className,
      )}
    >
      <Suspense
        fallback={
          <div className="flex h-full items-center justify-center gap-2 text-xs text-muted-foreground">
            加载编辑器...
          </div>
        }
      >
        <LazyCodeMirror
          value={value}
          height="100%"
          editable={editable}
          extensions={jsonEditorExtensions}
          onChange={onChange}
          basicSetup={{
            lineNumbers: true,
            foldGutter: true,
            bracketMatching: true,
            closeBrackets: true,
            indentOnInput: true,
          }}
          theme="light"
          style={{ height: '100%', fontSize: 13 }}
        />
      </Suspense>
    </div>
  );
}

export default JsonCodeEditor;
