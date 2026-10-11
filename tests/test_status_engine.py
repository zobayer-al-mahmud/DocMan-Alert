"""
Status-engine boundary tests (no pytest dependency; run directly).

    venv\\Scripts\\python.exe tests\\test_status_engine.py

Covers every boundary required by the spec: >90, 90, 61, 60, 31, 30, 8, 7,
1, 0, negative, and no-expiry.
"""
import os
import sys
from datetime import timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SECRET_KEY", "test-key")

from app.utils import (compute_status, bst_today, STATUS_VALID, STATUS_90, STATUS_60,
                       STATUS_30, STATUS_7, STATUS_EXPIRED)

CASES = [
    (95, STATUS_VALID),
    (91, STATUS_VALID),
    (90, STATUS_90),
    (61, STATUS_90),
    (60, STATUS_60),
    (31, STATUS_60),
    (30, STATUS_30),
    (8, STATUS_30),
    (7, STATUS_7),
    (1, STATUS_7),
    (0, STATUS_EXPIRED),
    (-5, STATUS_EXPIRED),
]


def main():
    today = bst_today()
    ok = True
    print("=== STATUS ENGINE BOUNDARY TESTS ===")
    for days, expected in CASES:
        got = compute_status(today + timedelta(days=days))
        passed = got == expected
        ok = ok and passed
        print(f"  [{'OK' if passed else 'FAIL'}] days={days:>4} -> {got!r} (expected {expected!r})")

    # no-expiry cases
    for label, result in [("expiry=None", compute_status(None)),
                          ("no_expiry=True", compute_status(today + timedelta(days=-100), no_expiry=True))]:
        passed = result == STATUS_VALID
        ok = ok and passed
        print(f"  [{'OK' if passed else 'FAIL'}] {label} -> {result!r} (expected {STATUS_VALID!r})")

    print("\nSTATUS ENGINE TEST:", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
