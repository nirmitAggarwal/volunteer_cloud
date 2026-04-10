import argparse
import json
import random


def emit_progress(pct):
    print(f"PROGRESS:{pct}", flush=True)


def sort_random_array(size):
    arr = [random.randint(1, 100000) for _ in range(size)]
    emit_progress(40)
    arr.sort()
    emit_progress(90)
    return arr[:5]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--difficulty", type=int, required=True)
    parser.add_argument("--output", type=str, required=True)
    args = parser.parse_args()

    sample = sort_random_array(args.difficulty)
    emit_progress(100)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump({"task_type": "sort_arrays", "result": sample}, f)


if __name__ == "__main__":
    main()
