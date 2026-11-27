import { Label } from '@/components/ui/Label';
import { YamlCodeEditor } from '@/pages/Workspace/components/Modals/YamlEditor';

/**
 * Edits a bot-config manifest as YAML.
 *
 * YAML is the only editor on purpose: the document is the contract, and a form
 * that models four of its seven categories invited edits that read as complete
 * when they were not. A single text area cannot imply that. The full grammar is
 * linked from 我的 Agent 库 next to the editor.
 */
export function ManifestEditor({
  value,
  onChange,
  height = 220,
}: {
  value: string;
  onChange: (next: string) => void;
  height?: number;
}) {
  return (
    <div className="space-y-2">
      <Label>能力配置 YAML</Label>
      <YamlCodeEditor value={value} onChange={onChange} height={height} />
    </div>
  );
}