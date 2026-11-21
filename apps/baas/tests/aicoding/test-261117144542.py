#!/usr/bin/env python3
"""打印中国小学生九九乘法表，末尾输出生成时间戳。"""

import datetime


def print_multiplication_table() -> None:
    """打印标准九九乘法表（1×1=1 至 9×9=81）。"""
    for i in range(1, 10):
        row = []
        for j in range(1, i + 1):
            row.append(f"{j}×{i}={i * j}")
        print("\t".join(row))


def main() -> None:
    timestamp = "261117144542"
    print_multiplication_table()
    print(timestamp)


if __name__ == "__main__":
    main()
