import argparse
import json
import math


def emit_progress(pct):
    print(f"PROGRESS:{pct}", flush=True)


def count_primes(limit):
    total = 0
    for i in range(2, limit):
        if i % max(1, limit // 10) == 0:
            emit_progress(int((i / limit) * 100))
        is_prime = True
        for j in range(2, int(math.sqrt(i)) + 1):
            if i % j == 0:
                is_prime = False
                break
        if is_prime:
            total += 1
    return total


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--difficulty", type=int, required=True)
    parser.add_argument("--output", type=str, required=True)
    args = parser.parse_args()

    result = count_primes(args.difficulty)
    emit_progress(100)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump({"task_type": "prime_number", "result": result}, f)


if __name__ == "__main__":
    main()
