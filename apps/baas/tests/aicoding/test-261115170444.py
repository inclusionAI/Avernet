from datetime import datetime


def main() -> None:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for row in range(1, 10):
        print(" ".join(f"{col}x{row}={row * col}" for col in range(1, row + 1)))
    print(generated_at)


if __name__ == "__main__":
    main()
