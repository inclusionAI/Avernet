/**
 * 包内 jest 配置：与 apps/frontend-nextgen 的转译链同链（transform/replace 同式；
 * @tc-chat/{adapters,core,utils} 只发 ESM dist，transformIgnorePatterns 放行转译；
 * babel 映射 @babel/runtime esm helpers 重定向——对齐宿主已知可跑组合
 * jest@30 + babel-jest@29.7，宿主全量套件长期绿于该组合）。
 */
const transform = {
  '^.+\\.(ts|tsx|js|jsx)$': [
    'babel-jest',
    { presets: ['@umijs/babel-preset-umi'], plugins: ['@babel/plugin-transform-modules-commonjs'] },
  ],
};

const moduleNameMapper = {
  '^@babel/runtime/helpers/esm/(.*)$':
    '<rootDir>/node_modules/@babel/runtime/helpers/$1',
};

const transformIgnorePatterns = [
  '/node_modules/(?!@tc-chat/|@ant-design/colors/|@dicebear/)',
  '\\.pnp\\.[^\\\\/]+$',
];

const testMatch = ['<rootDir>/test/**/*.(test|spec|e2e).(ts|tsx|js|jsx)'];

module.exports = {
  testEnvironment: 'node',
  testMatch,
  transform,
  moduleNameMapper,
  transformIgnorePatterns,
  testTimeout: 30000,
};