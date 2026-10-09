import { buildDebugChatFileRequest, parseDebugChatMessageContent } from '@/services/botWorkshop/debugChatFiles';

test('调试对话把已上传文件转换为消息引用参数', () => {
  expect(
    buildDebugChatFileRequest('请总结文件', [
      { resourceId: 'resource-1', displayName: '报告.pdf' },
      { resourceId: 'resource-2', displayName: '数据.csv' },
    ]),
  ).toEqual({
    content: '<file-ref insert_id="debug_file_1"></file-ref><file-ref insert_id="debug_file_2"></file-ref>请总结文件',
    options: {
      resourceReferences: [
        { type: 'file', resource_id: 'resource-1', insert_id: 'debug_file_1' },
        { type: 'file', resource_id: 'resource-2', insert_id: 'debug_file_2' },
      ],
      promptFileRefs: [
        { resource_id: 'resource-1', insert_id: 'debug_file_1' },
        { resource_id: 'resource-2', insert_id: 'debug_file_2' },
      ],
      fileRefDisplay: [
        { insert_id: 'debug_file_1', name: '报告.pdf' },
        { insert_id: 'debug_file_2', name: '数据.csv' },
      ],
    },
  });
});

test('没有文件时保持普通文本请求', () => {
  expect(buildDebugChatFileRequest('你好', [])).toEqual({ content: '你好' });
});

test('调试消息展示时分离文件标签与正文', () => {
  expect(
    parseDebugChatMessageContent('<file-ref insert_id="debug_file_1" name="报告.pdf"></file-ref>请总结文件'),
  ).toEqual({ text: '请总结文件', fileNames: ['报告.pdf'] });
});
