#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打印中国小学生九九乘法表,并在最后一行输出脚本时间戳。

时间戳与文件名 test-<yyMMddhhss>.py 中的 yyMMddhhss 一致。
"""

# 文件名中使用的时间戳(yyMMddhhss),最后一行原样输出。
TIMESTAMP = "261127104212"


def print_multiplication_table() -> None:
    """按中文小学生九九乘法表格式打印 1×1=1 至 9×9=81(下三角)。"""
    for i in range(1, 10):
        row = " ".join("{0}×{1}={2}".format(j, i, i * j) for j in range(1, i + 1))
        print(row)


def main() -> None:
    print_multiplication_table()
    # 最后一行输出前面的时间(与文件名时间戳一致)。
    print(TIMESTAMP)


if __name__ == "__main__":
    main()