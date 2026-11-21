#!/usr/bin/env python3
"""Blast Radius Production Test Selector - Team MVai
Full AST Impact Mapping with Safe Fallback and LPT Worker Partitioning
"""

import json
import math
import sys
from pathlib import Path

def get_data_dir() -> Path:
    p = Path("/data")
    if p.exists() and (p / "tests.json").exists():
        return p
    sample = Path(__file__).parent / "sample"
    if sample.exists() and (sample / "tests.json").exists():
        return sample
    return Path(".")

def partition_tests(test_nums: list[int], tests_by_num: dict[int, dict], max_workers: int = 8) -> list[list[int]]:
    if not test_nums:
        return []
    
    sorted_tests = [tests_by_num[n] for n in test_nums if n in tests_by_num]
    sorted_tests.sort(key=lambda t: t["seconds"], reverse=True)
    
    if not sorted_tests:
        return []
        
    best_workers = None
    best_cost = float("inf")
    
    limit_k = min(max_workers, len(sorted_tests))
    for k in range(1, limit_k + 1):
        buckets = [[] for _ in range(k)]
        loads = [0.0] * k
        for t in sorted_tests:
            idx = loads.index(min(loads))
            buckets[idx].append(t["number"])
            loads[idx] += t["seconds"]
        cost = max(loads) + 2.0 * k
        if cost < best_cost:
            best_cost = cost
            best_workers = buckets
            
    return [b for b in best_workers if b] if best_workers else []

def main() -> None:
    data_dir = get_data_dir()
    tests_file = data_dir / "tests.json"
    commits_dir = data_dir / "commits"
    
    tests_catalog = json.loads(tests_file.read_text(encoding="utf-8"))["tests"]
    tests_by_num = {t["number"]: t for t in tests_catalog}
    
    map_path = Path(__file__).parent / "test_map.json"
    map_data = json.loads(map_path.read_text(encoding="utf-8"))
    file_mapping = map_data["file_mapping"]
    fixture_mapping = map_data["fixture_mapping"]
    core_fallback = map_data.get("core_fallback", [])
    
    # Pre-index basenames for fast fuzzy matching
    basename_to_tests = {}
    for k, v in file_mapping.items():
        basename_to_tests[Path(k).name] = v

    patch_files = sorted(commits_dir.glob("*.patch"))
    for patch in patch_files:
        cid = patch.stem
        diff_text = patch.read_text(encoding="utf-8", errors="ignore")
        lines = diff_text.splitlines()
        changed_files = [l[6:] for l in lines if l.startswith("+++ b/")]
        
        selected: set[int] = set()
        for f in changed_files:
            # 1. Optimizer fixtures
            if "tests/fixtures/optimizer/" in f:
                stem = Path(f).stem
                fname = Path(f).name
                if fname in fixture_mapping:
                    selected.update(fixture_mapping[fname])
                elif stem in fixture_mapping:
                    selected.update(fixture_mapping[stem])
                continue
                
            # 2. Exact file mapping
            if f in file_mapping:
                selected.update(file_mapping[f])
                continue
                
            # 3. Basename mapping (handles differences in relative path)
            base = Path(f).name
            if base in basename_to_tests:
                selected.update(basename_to_tests[base])
                continue
                
            # 4. If test file changed directly
            for t in tests_catalog:
                if t["file"] == f:
                    selected.add(t["number"])
                    
        # If no tests matched yet files changed, add safety core fallback to ensure zero misses
        if not selected and changed_files:
            selected.update(core_fallback)
            
        workers = partition_tests(sorted(list(selected)), tests_by_num, max_workers=8)
        print(json.dumps({"commit": cid, "workers": workers}), flush=True)

if __name__ == "__main__":
    main()
