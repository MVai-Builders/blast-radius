#!/usr/bin/env python3
"""Blast Radius test selector.

Uses only repository-derived impact maps. Unknown or ambiguous changes fall back
conservatively to the complete test catalog to avoid the 300-second miss penalty.
"""

import json
import shlex
from pathlib import Path


def get_data_dir() -> Path:
    data = Path("/data")
    if (data / "tests.json").is_file():
        return data
    sample = Path(__file__).parent / "sample"
    if (sample / "tests.json").is_file():
        return sample
    return Path(".")


def partition_tests(
    test_nums: list[int],
    tests_by_num: dict[int, dict],
    max_workers: int = 8,
) -> list[list[int]]:
    """Choose worker count and balance known test durations with LPT + pair swaps."""
    tests = [tests_by_num[n] for n in set(test_nums) if n in tests_by_num]
    if not tests:
        return []
    tests.sort(key=lambda t: (-float(t["seconds"]), t["number"]))

    best: list[list[int]] | None = None
    best_cost = float("inf")
    for worker_count in range(1, min(max_workers, len(tests)) + 1):
        buckets: list[list[int]] = [[] for _ in range(worker_count)]
        loads = [0.0] * worker_count
        for test in tests:
            index = min(range(worker_count), key=lambda i: (loads[i], i))
            buckets[index].append(test["number"])
            loads[index] += float(test["seconds"])

        # Deterministic best-improvement single-test moves. Never changes selection.
        while True:
            current_peak = max(loads)
            move = None
            best_peak = current_peak
            for src in range(worker_count):
                if loads[src] != current_peak:
                    continue
                for number in buckets[src]:
                    duration = float(tests_by_num[number]["seconds"])
                    for dst in range(worker_count):
                        if dst == src:
                            continue
                        candidate_peak = max(
                            loads[dst] + duration,
                            *(loads[i] - duration if i == src else loads[i]
                              for i in range(worker_count) if i != dst),
                        )
                        if candidate_peak + 1e-12 < best_peak:
                            best_peak = candidate_peak
                            move = (src, dst, number, duration)
            if move is None:
                break
            src, dst, number, duration = move
            buckets[src].remove(number)
            buckets[dst].append(number)
            loads[src] -= duration
            loads[dst] += duration

        active = [bucket for bucket in buckets if bucket]
        peak = max(sum(float(tests_by_num[n]["seconds"]) for n in b) for b in active)
        cost = peak + 2.0 * len(active)
        if cost < best_cost - 1e-12:
            best_cost = cost
            best = active

    return best or []


def _git_path_tokens(text: str) -> list[str]:
    """Parse Git's quoted/unquoted path tokens, including paths containing spaces."""
    try:
        return shlex.split(text, posix=True)
    except ValueError:
        return []


def extract_changed_files(diff_text: str) -> list[str]:
    """Read both sides of git diff headers, including additions, deletions, and renames."""
    changed: set[str] = set()
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            tokens = _git_path_tokens(line[len("diff --git "):])
            if len(tokens) == 2:
                left, right = tokens
                if left.startswith("a/"):
                    changed.add(left[2:])
                if right.startswith("b/"):
                    changed.add(right[2:])
        elif line.startswith(("--- ", "+++ ")):
            marker, value = line[:4], line[4:]
            tokens = _git_path_tokens(value)
            if tokens:
                path = tokens[0]
                if marker == "--- " and path.startswith("a/"):
                    changed.add(path[2:])
                elif marker == "+++ " and path.startswith("b/"):
                    changed.add(path[2:])
        elif line.startswith("rename from "):
            changed.add(line[len("rename from "):])
        elif line.startswith("rename to "):
            changed.add(line[len("rename to "):])
    changed.discard("/dev/null")
    return sorted(changed)

def main() -> None:
    root = Path(__file__).parent
    data_dir = get_data_dir()
    catalog = json.loads((data_dir / "tests.json").read_text(encoding="utf-8"))["tests"]
    tests_by_num = {test["number"]: test for test in catalog}
    tests_by_file: dict[str, set[int]] = {}
    for test in catalog:
        tests_by_file.setdefault(test["file"], set()).add(test["number"])

    map_data = json.loads((root / "test_map.json").read_text(encoding="utf-8"))
    file_mapping = map_data["file_mapping"]
    fixture_mapping = map_data["fixture_mapping"]
    full_fallback = set(map_data.get("full_fallback", tests_by_num.keys()))

    basename_to_tests: dict[str, set[int]] = {}
    for path, numbers in file_mapping.items():
        basename_to_tests.setdefault(Path(path).name, set()).update(numbers)

    commits_dir = data_dir / "commits"
    for patch in sorted(commits_dir.glob("*.patch")):
        diff_text = patch.read_text(encoding="utf-8", errors="replace")
        changed_files = extract_changed_files(diff_text)
        selected: set[int] = set()
        uncertain = False

        for path in changed_files:
            # Direct test edits must always run the edited test file's catalog entries.
            if path in tests_by_file:
                selected.update(tests_by_file[path])
                continue

            # Fixture edits use exact fixture mapping. An unmapped fixture is uncertain.
            if "fixtures" in path or path.endswith((".sql", ".json")):
                name, stem = Path(path).name, Path(path).stem
                matches = fixture_mapping.get(name, fixture_mapping.get(stem))
                if matches:
                    selected.update(matches)
                else:
                    uncertain = True
                continue

            # Exact source-file map.
            if path in file_mapping:
                selected.update(file_mapping[path])
                continue

            # Never infer impact from a basename alone: unrelated packages can
            # contain identically named modules. Unknown paths must fail safe.


            # Test helpers and every unrecognized path are conservative fallbacks.
            # Do not let a known file in the same patch hide an unknown changed file.
            uncertain = True

        if uncertain:
            selected.update(full_fallback)
        elif not changed_files:
            # Empty/unparseable patch: safest interpretation is unknown impact.
            selected.update(full_fallback)

        # Do not prune "expensive" parser tests based on a filename heuristic:
        # parser behavior can be affected by dialect parsers, shared bases, and helpers.
        workers = partition_tests(sorted(selected), tests_by_num)
        print(json.dumps({"commit": patch.stem, "workers": workers}, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
