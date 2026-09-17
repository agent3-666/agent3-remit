"""Delete one rule at a time and require the test that covers it to fail.

A suite that stays green with a rule removed is decoration. Two results that look like success and
are not, both refused here:

  * pytest exits 5 when a filter matches nothing. That is not a pass, it is nothing having run.
  * a mutant that fails to import would make every test fail for the wrong reason.

Run: python scripts/mutation_check.py
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

SPEC = {
    "read-both-key-names": "test_a_record_using_the_other_key_name_is_not_missed",
    "free-short-circuit": "test_free_records_are_not_dragged_into_settlement",
    "amount-required": "test_a_price_with_no_amount_names_the_missing_field",
    "currency-required": "test_a_price_with_no_currency_names_the_missing_field",
    "never-guess-a-payee": "test_no_payee_anywhere_is_refused_rather_than_guessed",
    "unreachable-endpoint": "test_an_endpoint_that_does_not_answer_is_reported_as_such",
    "simulate-before-broadcast": "test_it_simulates_before_it_broadcasts",
}


def pytest(args: list[str]) -> int:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *args], cwd=ROOT, capture_output=True, text=True
    ).returncode


def markers_in_source() -> set[str]:
    found = set()
    for path in SRC.rglob("*.py"):
        found.update(re.findall(r"#\s*GUARD:([a-z0-9-]+)", path.read_text()))
    return found


def main() -> int:
    print("== baseline: the suite must pass before any rule is removed")
    if pytest([]) != 0:
        print("FAIL baseline: suite is not green")
        return 1
    print("ok   baseline")

    missing = markers_in_source() - set(SPEC)
    if missing:
        print(f"FAIL coverage: {', '.join(sorted(missing))} is not named in this script")
        return 1
    print("ok   coverage: every GUARD marker is named here")

    backup = Path(tempfile.mkdtemp()) / "src"
    shutil.copytree(SRC, backup)
    failures = 0
    try:
        for marker, test_name in SPEC.items():
            shutil.rmtree(SRC)
            shutil.copytree(backup, SRC)

            removed = 0
            for path in SRC.rglob("*.py"):
                lines = path.read_text().splitlines(keepends=True)
                kept = [ln for ln in lines if not re.search(rf"#\s*GUARD:{re.escape(marker)}\s*$", ln.rstrip())]
                if len(kept) != len(lines):
                    removed += len(lines) - len(kept)
                    path.write_text("".join(kept))

            if removed != 1:
                print(f"FAIL {marker}: matched {removed} lines, expected exactly 1")
                failures += 1
                continue

            if pytest(["--collect-only", "-k", test_name]) != 0:
                print(f"FAIL {marker}: no test named {test_name} was collected")
                failures += 1
                continue

            code = pytest(["-k", test_name])
            if code == 1:
                print(f"ok   {marker}: {test_name} turned red")
            elif code == 0:
                print(f"FAIL {marker}: {test_name} stayed green without the rule")
                failures += 1
            else:
                print(f"FAIL {marker}: pytest exited {code}, which proves nothing")
                failures += 1
    finally:
        shutil.rmtree(SRC, ignore_errors=True)
        shutil.copytree(backup, SRC)
        shutil.rmtree(backup.parent, ignore_errors=True)

    if failures:
        print(f"== {failures} rule(s) unproven")
        return 1
    print("== every rule is proven by a failing test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
