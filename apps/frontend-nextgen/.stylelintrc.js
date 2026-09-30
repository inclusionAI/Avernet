module.exports = {
  extends: require.resolve('@umijs/max/stylelint'),
  rules: {
    'at-rule-no-unknown': [
      true,
      {
        ignoreAtRules: [
          'custom-variant',
          'theme',
          'layer',
        ],
      },
    ],
    'custom-property-empty-line-before': null,
    'value-keyword-case': null,
    'alpha-value-notation': null,
    'rule-empty-line-before': null,
  },
};
