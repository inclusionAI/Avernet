const baseConf = require.resolve('@umijs/max/eslint');

module.exports = {
  extends: baseConf,
  overrides: [{
    files: ['src/assets/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': ['error', {
        patterns: [{
          group: ['@/components/*', '@/hooks/*', '@/stores/*', '@/pages/*', '@/domain/*'],
          message: 'src/assets/** must remain independent from TeamClaw business layers.',
        }],
      }],
    },
  }],
};
