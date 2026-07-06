// 用 .js 而非 .ts，避免 jest 解析 ts 配置需要 ts-node 依赖（见 CLAUDE.md 测试基建 checklist）。
/** @type {import('jest').Config} */
module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'node',
  roots: ['<rootDir>/src', '<rootDir>/test'],
  moduleNameMapper: {
    '^@/(.*)$': '<rootDir>/src/$1',
  },
  testMatch: ['**/__tests__/**/*.test.ts', '**/*.test.ts'],
  // 忽略 .umi 自动生成目录
  testPathIgnorePatterns: ['/node_modules/', '/src/.umi/'],
  transform: {
    '^.+\\.tsx?$': [
      'ts-jest',
      {
        // 隔离编译 + 跳过 lib 检查：避免 SDK 类型噪音污染测试（见 CLAUDE.md checklist）
        diagnostics: { ignoreCodes: [151001] },
        tsconfig: { isolatedModules: true, skipLibCheck: true },
      },
    ],
  },
};
