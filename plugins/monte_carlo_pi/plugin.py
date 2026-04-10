import argparse
import json
import random


def emit_progress(pct):
    print(f"PROGRESS:{pct}", flush=True)


def estimate_pi(iterations):
    inside = 0
    for i in range(iterations):
        if i % max(1, iterations // 10) == 0:
            emit_progress(int((i / iterations) * 100))
        x = random.random()
        y = random.random()
        if x * x + y * y <= 1.0:
            inside += 1
    return (inside / iterations) * 4


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--difficulty", type=int, required=True)
    parser.add_argument("--output", type=str, required=True)
    args = parser.parse_args()

    pi_value = estimate_pi(args.difficulty)
    emit_progress(100)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump({"task_type": "monte_carlo_pi", "result": pi_value}, f)


if __name__ == "__main__":
    main()
