"""任务 prompt 跨模块共享常量(零依赖,shared kernel)。

teamclaw 运行环境出于安全原因不支持联网搜索:规划/派发/执行/验收的 prompt 一律注入此约束,
强制 bot 仅依据给定上下文与自身知识产出,不调用联网搜索/web_search 工具。
"""

#: 联网搜索禁令(注入到 planning/search/execute/verify prompt 末尾)
NO_WEB_SEARCH_CONSTRAINT = (
    "## 执行约束(硬性,违反将判定任务失败)\n"
    "- 严禁调用任何联网搜索/web_search/联网检索工具,严禁发起任何外部 HTTP 或网络请求,"
    "严禁访问外部网络;违反将导致任务直接判定失败。\n"
    "- 仅依据上方给定上下文与自身知识完成执行、校验、验收并产出结论;"
    "不得因无法联网核实而跳过验收或判 FAILED。\n"
)
