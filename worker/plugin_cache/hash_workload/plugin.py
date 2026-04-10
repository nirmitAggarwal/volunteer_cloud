import argparse
import hashlib
import json


def emit_progress(pct):
    print(f"PROGRESS:{pct}", flush=True)


def run_hash_workload(iterations):
    value = "start"
    for i in range(iterations):
        if i % max(1, iterations // 10) == 0:
            emit_progress(int((i / iterations) * 100))
        value = hashlib.sha256((value + str(i)).encode()).hexdigest()
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--difficulty", type=int, required=True)
    parser.add_argument("--output", type=str, required=True)
    args = parser.parse_args()

    result = run_hash_workload(args.difficulty)
    emit_progress(100)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump({"task_type": "hash_workload", "result": result}, f)


if __name__ == "__main__":
    main()
