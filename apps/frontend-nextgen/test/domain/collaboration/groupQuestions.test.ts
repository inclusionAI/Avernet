import {
  buildGroupQuestionAnswers,
  GroupQuestionValidationError,
  type GroupQuestion,
  type QuestionDrafts,
} from '@/domain/collaboration/groupQuestions';
import { describe, expect, it } from '@jest/globals';

/** 核心用例的单选题：不带自定义输入。 */
const singleChoice: GroupQuestion = {
  questionId: 'q1',
  question: '环境？',
  multiSelect: false,
  allowOther: false,
  options: [{ value: 'stage', label: '预发布' }],
};

/** 断言校验失败并返回携带首错 questionId 的错误；非校验异常原样抛出，避免吞错。 */
function expectValidationError(questions: GroupQuestion[], drafts: QuestionDrafts): GroupQuestionValidationError {
  try {
    buildGroupQuestionAnswers(questions, drafts);
  } catch (error) {
    if (error instanceof GroupQuestionValidationError) return error;
    throw new Error(`应当抛出 GroupQuestionValidationError，实际抛出：${String(error)}`);
  }
  throw new Error('buildGroupQuestionAnswers 应当抛出 GroupQuestionValidationError，但校验通过');
}

describe('buildGroupQuestionAnswers 基础提交', () => {
  it('提交原始 value 并保留显式跳过', () => {
    expect(
      buildGroupQuestionAnswers([singleChoice], {
        q1: { values: ['stage'], other: '', skipped: false },
      }),
    ).toEqual({ q1: { values: ['stage'] } });
    expect(
      buildGroupQuestionAnswers([singleChoice], {
        q1: { values: [], other: '', skipped: true },
      }),
    ).toEqual({ q1: { values: [] } });
    expect(() => buildGroupQuestionAnswers([singleChoice], {})).toThrow();
  });

  it('草稿存在但未作答且未跳过时拒绝', () => {
    expect(() =>
      buildGroupQuestionAnswers([singleChoice], {
        q1: { values: [], other: '', skipped: false },
      }),
    ).toThrow();
  });

  it('显式跳过时忽略残留输入，提交空数组', () => {
    expect(
      buildGroupQuestionAnswers([singleChoice], {
        q1: { values: ['stage'], other: '残留输入', skipped: true },
      }),
    ).toEqual({ q1: { values: [] } });
  });
});

describe('选项 value 与 label', () => {
  it('提交 label 而非 value 时按无效选项拒绝', () => {
    const error = expectValidationError([singleChoice], {
      q1: { values: ['预发布'], other: '', skipped: false },
    });
    expect(error.questionId).toBe('q1');
  });

  it('保留选项原始 value 的空白，不做 trim 改写', () => {
    const question: GroupQuestion = {
      questionId: 'q1',
      question: '环境？',
      multiSelect: false,
      allowOther: false,
      options: [{ value: ' prod ', label: '生产' }],
    };
    expect(
      buildGroupQuestionAnswers([question], {
        q1: { values: [' prod '], other: '', skipped: false },
      }),
    ).toEqual({ q1: { values: [' prod '] } });
  });

  it('带空白的草稿值不等于 canonical value，按无效选项拒绝', () => {
    const error = expectValidationError([singleChoice], {
      q1: { values: [' stage'], other: '', skipped: false },
    });
    expect(error.questionId).toBe('q1');
  });
});

describe('多选与自定义输入', () => {
  const multiChoice: GroupQuestion = {
    questionId: 'q1',
    question: '涉及哪些环境？',
    multiSelect: true,
    allowOther: true,
    options: [
      { value: 'stage', label: '预发布' },
      { value: 'prod', label: '生产' },
    ],
  };

  it('多选合并选项 value 与 trim 后的自定义输入', () => {
    expect(
      buildGroupQuestionAnswers([multiChoice], {
        q1: { values: ['stage', 'prod'], other: '  自定义  ', skipped: false },
      }),
    ).toEqual({ q1: { values: ['stage', 'prod', '自定义'] } });
  });

  it('自定义值直接进入 values，不生成 customValues 字段', () => {
    const singleWithOther: GroupQuestion = {
      questionId: 'q1',
      question: '环境？',
      multiSelect: false,
      allowOther: true,
      options: [{ value: 'stage', label: '预发布' }],
    };
    expect(
      buildGroupQuestionAnswers([singleWithOther], {
        q1: { values: [], other: '自定义说明', skipped: false },
      }),
    ).toEqual({ q1: { values: ['自定义说明'] } });
  });
});

describe('纯文本题目', () => {
  it('无 options 时自由文本可用，不受 allowOther=false 限制', () => {
    const freeText: GroupQuestion = {
      questionId: 'q1',
      question: '补充说明',
      multiSelect: false,
      allowOther: false,
      options: [],
    };
    expect(
      buildGroupQuestionAnswers([freeText], {
        q1: { values: [], other: '自由文本', skipped: false },
      }),
    ).toEqual({ q1: { values: ['自由文本'] } });
  });
});

describe('allowOther=false 拒绝自定义', () => {
  it('有选项时填写自定义文本拒绝', () => {
    const error = expectValidationError([singleChoice], {
      q1: { values: [], other: '额外输入', skipped: false },
    });
    expect(error.questionId).toBe('q1');
  });
});

describe('单选数量限制', () => {
  const threeOptions: GroupQuestion = {
    questionId: 'q1',
    question: '环境？',
    multiSelect: false,
    allowOther: false,
    options: [
      { value: 'stage', label: '预发布' },
      { value: 'prod', label: '生产' },
      { value: 'gray', label: '灰度' },
    ],
  };

  it('单选提交两个选项值时拒绝', () => {
    const error = expectValidationError([threeOptions], {
      q1: { values: ['stage', 'prod'], other: '', skipped: false },
    });
    expect(error.questionId).toBe('q1');
  });

  it('单选同时选择选项并填写自定义时拒绝（选项与自定义互斥）', () => {
    const singleWithOther: GroupQuestion = {
      questionId: 'q1',
      question: '环境？',
      multiSelect: false,
      allowOther: true,
      options: [{ value: 'stage', label: '预发布' }],
    };
    const error = expectValidationError([singleWithOther], {
      q1: { values: ['stage'], other: '补充', skipped: false },
    });
    expect(error.questionId).toBe('q1');
  });
});

describe('空白输入无效', () => {
  it('自定义输入仅空白时按未作答拒绝', () => {
    const singleWithOther: GroupQuestion = {
      questionId: 'q1',
      question: '环境？',
      multiSelect: false,
      allowOther: true,
      options: [{ value: 'stage', label: '预发布' }],
    };
    const error = expectValidationError([singleWithOther], {
      q1: { values: [], other: '   ', skipped: false },
    });
    expect(error.questionId).toBe('q1');
  });

  it('纯文本题仅空白时按未作答拒绝', () => {
    const freeText: GroupQuestion = {
      questionId: 'q1',
      question: '补充说明',
      multiSelect: false,
      allowOther: false,
      options: [],
    };
    const error = expectValidationError([freeText], {
      q1: { values: [], other: ' \t ', skipped: false },
    });
    expect(error.questionId).toBe('q1');
  });

  it('纯文本题空白 values 条目拒绝', () => {
    const freeText: GroupQuestion = {
      questionId: 'q1',
      question: '补充说明',
      multiSelect: false,
      allowOther: false,
      options: [],
    };
    const error = expectValidationError([freeText], {
      q1: { values: ['   '], other: '', skipped: false },
    });
    expect(error.questionId).toBe('q1');
  });

  it('纯文本题非空白 values 保持原样，不做 trim 改写', () => {
    const freeText: GroupQuestion = {
      questionId: 'q1',
      question: '补充说明',
      multiSelect: false,
      allowOther: true,
      options: [],
    };
    expect(
      buildGroupQuestionAnswers([freeText], {
        q1: { values: [' 自由文本 '], other: '', skipped: false },
      }),
    ).toEqual({ q1: { values: [' 自由文本 '] } });
  });

  it('选项 value 本身全空白时拒绝提交', () => {
    const question: GroupQuestion = {
      questionId: 'q1',
      question: '环境？',
      multiSelect: false,
      allowOther: false,
      options: [{ value: '   ', label: '全空白' }],
    };
    const error = expectValidationError([question], {
      q1: { values: ['   '], other: '', skipped: false },
    });
    expect(error.questionId).toBe('q1');
  });
});

describe('原型键安全', () => {
  it('questionId 为 __proto__ 时提交结果经 JSON 序列化仍保留该键', () => {
    const question: GroupQuestion = {
      questionId: '__proto__',
      question: '环境？',
      multiSelect: false,
      allowOther: false,
      options: [{ value: 'stage', label: '预发布' }],
    };
    // 计算属性键会创建自有 '__proto__' 数据属性（字面量简写会改写原型，不能用于构造 fixture）。
    const drafts: QuestionDrafts = {
      ['__proto__']: { values: ['stage'], other: '', skipped: false },
    };
    const answers = buildGroupQuestionAnswers([question], drafts);
    expect(Object.keys(answers)).toEqual(['__proto__']);
    expect(JSON.stringify(answers)).toContain('__proto__');
    expect(answers['__proto__']).toEqual({ values: ['stage'] });
  });

  it('questionId 为 constructor 且无自有草稿时抛结构化校验错误', () => {
    const question: GroupQuestion = {
      questionId: 'constructor',
      question: '环境？',
      multiSelect: false,
      allowOther: false,
      options: [{ value: 'stage', label: '预发布' }],
    };
    // 空对象沿原型链可取到 constructor，必须按未作答处理，不得触发 TypeError。
    const error = expectValidationError([question], {});
    expect(error.questionId).toBe('constructor');
    expect(error.message).toContain('constructor');
  });

  it('questionId 为 constructor 且存在自有草稿时可正常提交', () => {
    const question: GroupQuestion = {
      questionId: 'constructor',
      question: '环境？',
      multiSelect: false,
      allowOther: false,
      options: [{ value: 'stage', label: '预发布' }],
    };
    expect(
      buildGroupQuestionAnswers([question], {
        constructor: { values: ['stage'], other: '', skipped: false },
      }),
    ).toEqual({ constructor: { values: ['stage'] } });
  });
});

describe('错误定位与题目覆盖', () => {
  const choiceQuestion: GroupQuestion = {
    questionId: 'q1',
    question: '环境？',
    multiSelect: false,
    allowOther: false,
    options: [{ value: 'stage', label: '预发布' }],
  };
  const freeTextQuestion: GroupQuestion = {
    questionId: 'q2',
    question: '补充说明',
    multiSelect: false,
    allowOther: false,
    options: [],
  };

  it('错误携带首个无效 questionId，消息包含该 ID', () => {
    const error = expectValidationError([choiceQuestion, freeTextQuestion], {
      q1: { values: ['stage'], other: '', skipped: false },
    });
    expect(error.questionId).toBe('q2');
    expect(error.message).toContain('q2');

    const reversed = expectValidationError([choiceQuestion, freeTextQuestion], {
      q2: { values: [], other: '说明', skipped: false },
    });
    expect(reversed.questionId).toBe('q1');
  });

  it('全部 questionId 均包含在答案中', () => {
    const drafts: QuestionDrafts = {
      q1: { values: ['stage'], other: '', skipped: false },
      q2: { values: [], other: '', skipped: true },
    };
    expect(buildGroupQuestionAnswers([choiceQuestion, freeTextQuestion], drafts)).toEqual({
      q1: { values: ['stage'] },
      q2: { values: [] },
    });
  });
});
