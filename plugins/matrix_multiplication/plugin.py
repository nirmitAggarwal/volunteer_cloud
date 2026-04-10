import argparse
import json
import random


def emit_progress(pct):
    print(f"PROGRESS:{pct}", flush=True)


def multiply_matrices(size):
    mat1 = [[random.random() for _ in range(size)] for _ in range(size)]
    mat2 = [[random.random() for _ in range(size)] for _ in range(size)]
    result = [[0.0] * size for _ in range(size)]

    for i in range(size):
        if i % max(1, size // 10) == 0:
            emit_progress(int((i / size) * 100))
        for j in range(size):
            row_sum = 0.0
            for k in range(size):
                row_sum += mat1[i][k] * mat2[k][j]
            result[i][j] = row_sum
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--difficulty", type=int, required=True)
    parser.add_argument("--output", type=str, required=True)
    args = parser.parse_args()

    multiply_matrices(args.difficulty)
    emit_progress(100)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump({"task_type": "matrix_multiplication", "result": "matrix_computed"}, f)


if __name__ == "__main__":
    main()
