module.exports = {
  extends: require.resolve('@umijs/max/stylelint'),
  rules: {
    // 支持 Tailwind CSS 的 @tailwind 和 @layer 指令
    'at-rule-no-unknown': [
      true,
      {
        ignoreAtRules: [
          'tailwind',
          'layer',
          'apply',
          'variants',
          'responsive',
          'screen',
        ],
      },
    ],
    // 允许注释前没有空行
    'comment-empty-line-before': null,
  },
};
