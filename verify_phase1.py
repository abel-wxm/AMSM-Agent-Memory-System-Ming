#!/usr/bin/env python3
"""Phase 1 验证脚本 — 检查所有 .py 文件语法正确性。"""
import py_compile
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

FILES = [
    "memoir/__init__.py",
    "memoir/config.py",
    "memoir/provider.py",
    "memoir/archive.py",
    "memoir/graph.py",
    "memoir/retrieval.py",
    "memoir/evolution.py",
    "memoir/gc.py",
    "memoir/db/__init__.py",
    "memoir/db/schema.py",
    "memoir/db/conn.py",
    "tests/test_smoke.py",
]

all_ok = True
for f in FILES:
    path = os.path.join(PROJECT_ROOT, f)
    if not os.path.exists(path):
        print(f"  MISSING: {f}")
        all_ok = False
        continue
    try:
        py_compile.compile(path, doraise=True)
        print(f"  OK: {f}")
    except py_compile.PyCompileError as e:
        print(f"  FAIL: {f} — {e}")
        all_ok = False

print()
if all_ok:
    print(f"ALL {len(FILES)} files: syntax OK ✓")
else:
    print("ERRORS FOUND")
    sys.exit(1)
