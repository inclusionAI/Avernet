import type { GroupQuestion } from '@/domain/collaboration/groupQuestions';
import { Button } from '@/components/ui';
import { cn } from '@/utils/cn';

interface GroupQuestionNavProps {
  questions: GroupQuestion[];
  activeIndex: number;
  disabled?: boolean;
  onSelect: (index: number) => void;
}

/** 多题 ask_user 交互的题目导航，保持作答表单只承载当前题目与提交逻辑。 */
export function GroupQuestionNav({ questions, activeIndex, disabled, onSelect }: GroupQuestionNavProps) {
  return (
    <div role="group" aria-label="问题切换" className="flex flex-wrap items-center gap-1">
      {questions.map((question, index) => (
        <Button
          key={question.questionId}
          variant="ghost"
          size="sm"
          aria-current={index === activeIndex ? 'step' : undefined}
          disabled={disabled}
          onClick={() => onSelect(index)}
          className={cn(
            'rounded-t-md border-b-2 px-3 py-1.5 text-xs transition-colors disabled:opacity-50',
            index === activeIndex
              ? 'border-primary bg-primary/10 font-medium text-primary'
              : 'border-transparent bg-muted text-muted-foreground hover:bg-muted/70',
          )}
        >
          {question.header || `问题 ${index + 1}`}
        </Button>
      ))}
    </div>
  );
}
