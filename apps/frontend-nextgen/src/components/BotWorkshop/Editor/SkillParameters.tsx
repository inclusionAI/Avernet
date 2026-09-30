import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Spin } from '@/components/ui/Spin';
import { Switch } from '@/components/ui/Switch';
import { useSkillParameters } from '@/hooks/useSkillParameters';
export function SkillParameters({
  botId,
  skillId,
  ownerId,
  content,
  editable,
}: {
  botId: string;
  skillId: string;
  ownerId?: string;
  content: string;
  editable: boolean;
}) {
  const form = useSkillParameters(botId, skillId, content, ownerId);
  if (form.loading) return <Spin tip="加载 Skill 参数…" />;
  if (form.error)
    return (
      <div role="alert">
        <p>{form.error}</p>
        <Button variant="outline" onClick={form.retry}>
          重试参数加载
        </Button>
      </div>
    );
  if (!form.schema.length) return null;
  return (
    <section className="space-y-3">
      <h3 className="text-sm font-semibold">Skill 参数</h3>
      {form.schema.map((field) => {
        const value = form.values[field.name];
        const disabled = !editable || form.saving;
        return (
          <div key={field.name} className="space-y-1">
            <label className="text-sm" htmlFor={`skill-param-${field.name}`}>
              {field.label || field.name}
              {field.required ? ' *' : ''}
            </label>
            {field.type === 'boolean' ? (
              <Switch
                id={`skill-param-${field.name}`}
                checked={value === true || value === 'true' || value === 1 || value === '1'}
                disabled={disabled}
                onCheckedChange={(v) => form.change(field.name, v)}
              />
            ) : field.type === 'select' ? (
              <Select value={String(value ?? '')} disabled={disabled} onValueChange={(v) => form.change(field.name, v)}>
                <SelectTrigger id={`skill-param-${field.name}`}>
                  <SelectValue placeholder="请选择" />
                </SelectTrigger>
                <SelectContent>
                  {field.options?.map((option) => (
                    <SelectItem key={String(option.value)} value={String(option.value)}>
                      {option.label || String(option.value)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <Input
                id={`skill-param-${field.name}`}
                disabled={disabled}
                type={field.type === 'secret' ? 'password' : field.type === 'number' ? 'number' : 'text'}
                value={String(value ?? '')}
                onChange={(e) =>
                  form.change(
                    field.name,
                    field.type === 'number' && e.target.value !== '' ? Number(e.target.value) : e.target.value,
                  )
                }
              />
            )}
            {field.description ? <p className="text-xs text-muted-foreground">{field.description}</p> : null}
          </div>
        );
      })}
      {editable ? (
        <Button disabled={form.saving} onClick={() => void form.save()}>
          保存参数
        </Button>
      ) : null}
    </section>
  );
}
