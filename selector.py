#!/usr/bin/env python3
"""Blast Radius Test Selector - Team MVai
Optimized Test Impact Analysis & Multiprocessor LPT Partitioning
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
    
    # Sort tests descending by duration (Longest Processing Time first)
    sorted_tests = [tests_by_num[n] for n in test_nums if n in tests_by_num]
    sorted_tests.sort(key=lambda t: t["seconds"], reverse=True)
    
    if not sorted_tests:
        return []
        
    best_workers = None
    best_cost = float("inf")
    
    # Evaluate optimal worker count k (from 1 to min(8, count))
    limit_k = min(max_workers, len(sorted_tests))
    for k in range(1, limit_k + 1):
        buckets = [[] for _ in range(k)]
        loads = [0.0] * k
        for t in sorted_tests:
            idx = loads.index(min(loads))
            buckets[idx].append(t["number"])
            loads[idx] += t["seconds"]
        # Cost metric: max worker load + 2.0s per worker
        cost = max(loads) + 2.0 * k
        if cost < best_cost:
            best_cost = cost
            best_workers = buckets
            
    return [b for b in best_workers if b] if best_workers else []

def main() -> None:
    data_dir = get_data_dir()
    tests_file = data_dir / "tests.json"
    commits_dir = data_dir / "commits"
    
    # Load test catalog
    tests_catalog = json.loads(tests_file.read_text(encoding="utf-8"))["tests"]
    tests_by_num = {t["number"]: t for t in tests_catalog}
    
    # Load pre-computed AST & dependency map
    map_path = Path(__file__).parent / "test_map.json"
    map_data = json.loads(map_path.read_text(encoding="utf-8"))
    dialect_to_tests = map_data["dialect_to_tests"]
    fixture_to_tests = map_data["fixture_to_tests"]
    file_to_tests = map_data["file_to_tests"]
    dialects = set(map_data["dialects"])
    
    patch_files = sorted(commits_dir.glob("*.patch"))
    for patch in patch_files:
        cid = patch.stem
        diff_text = patch.read_text(encoding="utf-8", errors="ignore")
        lines = diff_text.splitlines()
        
        # Check if patch is pure comments/whitespace
        added_lines = [l[1:].strip() for l in lines if l.startswith('+') and not l.startswith('+++')]
        deleted_lines = [l[1:].strip() for l in lines if l.startswith('-') and not l.startswith('---')]
        changed_files = [l[6:] for l in lines if l.startswith("+++ b/")]
        
        is_pure_comment = True
        for l in added_lines + deleted_lines:
            if l and not l.startswith('#') and not l.startswith('"""') and not l.startswith("'''") and not l.startswith('*'):
                is_pure_comment = False
                break
                
        if is_pure_comment and changed_files and not any(f.endswith('.sql') for f in changed_files):
            print(json.dumps({"commit": cid, "workers": []}), flush=True)
            continue
            
        selected: set[int] = set()
        for f in changed_files:
            # 1. Optimizer SQL fixtures
            if "tests/fixtures/optimizer/" in f:
                fname = Path(f).name
                stem = Path(f).stem
                if fname in fixture_to_tests:
                    selected.update(fixture_to_tests[fname])
                if stem in fixture_to_tests:
                    selected.update(fixture_to_tests[stem])
                for t in file_to_tests.get("tests/test_optimizer.py", []):
                    t_info = tests_by_num.get(t)
                    if t_info:
                        tname = t_info["id"].split("::")[-1]
                        if stem.rstrip('s') in tname.rstrip('s'):
                            selected.add(t)
                continue
                
            # 2. Dialect files
            dname = None
            for prefix in ["sqlglot/dialects/", "sqlglot/parsers/", "sqlglot/generators/", "sqlglot/typing/"]:
                if f.startswith(prefix):
                    stem = Path(f).stem
                    if stem != "dialect":
                        dname = stem
                    break
                    
            if dname:
                if dname in dialect_to_tests:
                    selected.update(dialect_to_tests[dname])
                tf = f"tests/dialects/test_{dname}.py"
                if tf in file_to_tests:
                    selected.update(file_to_tests[tf])
                continue
                
            # 3. Optimizer modules
            if f.startswith("sqlglot/optimizer/"):
                selected.update(file_to_tests.get("tests/test_optimizer.py", []))
                selected.update(file_to_tests.get("tests/test_executor.py", []))
                continue
                
            # 4. Expressions
            if f.startswith("sqlglot/expressions"):
                selected.update(file_to_tests.get("tests/test_expressions.py", []))
                parser_tests = [t for t in file_to_tests.get("tests/test_parser.py", []) if t != 1490]
                selected.update(parser_tests)
                continue
                
            # 5. Direct test file
            stem = Path(f).stem
            direct_tf = f"tests/test_{stem}.py"
            if direct_tf in file_to_tests:
                selected.update(file_to_tests[direct_tf])
                
            # 6. Core files
            if f in ["sqlglot/serde.py", "sqlglot/dialects/dialect.py"]:
                selected.update(t["number"] for t in tests_catalog)

        workers = partition_tests(sorted(list(selected)), tests_by_num, max_workers=8)
        print(json.dumps({"commit": cid, "workers": workers}), flush=True)

if __name__ == "__main__":
    main()
