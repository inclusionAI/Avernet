import { buildDebugChatFileRequest, parseDebugChatMessageContent } from '@/services/botWorkshop/debugChatFiles';

const context = {
  mentions: [],
  commands: [],
  fileRefs: [
    { resource_id: 'resource-1', display_name: '报告.pdf', insert_id: 'file-1', order: 0 },
    { resource_id: 'resource-2', display_name: '数据.csv', insert_id: 'file-2', order: 1 },
  ],
  resolvedContent: '<file-ref insert_id="file-1"></file-ref><file-ref insert_id="file-2"></file-ref>请总结文件',
};

test('调试对话保留 Sender 生成的文件占位和引用 ID', () => {
  expect(buildDebugChatFileRequest('[报告.pdf][数据.csv]请总结文件', context)).toEqual({
    content: context.resolvedContent,
    options: {
      resourceReferences: [
        { type: 'file', resource_id: 'resource-1', insert_id: 'file-1' },
        { type: 'file', resource_id: 'resource-2', insert_id: 'file-2' },
      ],
      promptFileRefs: [
        { resource_id: 'resource-1', insert_id: 'file-1' },
        { resource_id: 'resource-2', insert_id: 'file-2' },
      ],
      fileRefDisplay: [
        { insert_id: 'file-1', name: '报告.pdf' },
        { insert_id: 'file-2', name: '数据.csv' },
      ],
    },
  });
});

test('没有文件时保持普通文本请求', () => {
  expect(buildDebugChatFileRequest('你好')).toEqual({ content: '你好' });
  expect(buildDebugChatFileRequest('你好', { mentions: [], commands: [] })).toEqual({ content: '你好' });
});

test('知识库装饰后的文件正文不被原始消息覆盖', () => {
  const prefix = '本条消息引用知识库 kb-1\n';
  expect(
    buildDebugChatFileRequest(prefix + '[报告.pdf]请总结', {
      ...context,
      resolvedContent: prefix + context.resolvedContent,
    }).content,
  ).toBe(prefix + context.resolvedContent);
});

test('只有附件时保留可发送的 file-ref 正文', () => {
  expect(
    buildDebugChatFileRequest('[报告.pdf]', {
      ...context,
      resolvedContent: '<file-ref insert_id="file-1"></file-ref>',
    }).content,
  ).toBe('<file-ref insert_id="file-1"></file-ref>');
});

test('调试消息展示时分离文件标签与正文', () => {
  expect(parseDebugChatMessageContent('<file-ref insert_id="file-1" name="报告.pdf"></file-ref>请总结文件')).toEqual({
    text: '请总结文件',
    fileNames: ['报告.pdf'],
  });
});
