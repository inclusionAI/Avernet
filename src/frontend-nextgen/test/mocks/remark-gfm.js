// remark-gfm ESM 包在 Jest CommonJS 环境里的桩：空插件，react-markdown mock 不会真正调用。
module.exports = function remarkGfm() {
  return function transform(tree) {
    return tree;
  };
};
