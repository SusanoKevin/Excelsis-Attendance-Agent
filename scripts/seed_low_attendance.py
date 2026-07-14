#!/usr/bin/env python3
"""
Fix attendance rates for target class sections by flipping enough
existing 'present' rows to 'absent' to hit the desired rate.

Sections targeted (all below 75%):
  9-A  ~50%   10-C  ~58%   11-E  ~63%   12-B  ~68%   10-D  ~72%
"""
from __future__ import annotations

from sqlalchemy import text

from _db_utils import build_engine

SERVER   = "localhost,1433"
USERNAME = "sa"
PASSWORD = "ExcelsisTest_2024!"
DRIVER   = "{ODBC Driver 18 for SQL Server}"

SECTION_TARGETS = [
    {"section": "9-A",  "target_rate": 0.50},
    {"section": "10-C", "target_rate": 0.58},
    {"section": "11-E", "target_rate": 0.63},
    {"section": "12-B", "target_rate": 0.68},
    {"section": "10-D", "target_rate": 0.72},
]


def _engine():
    return build_engine(
        "education_db", SERVER, driver=DRIVER, username=USERNAME, password=PASSWORD,
    )


def main() -> None:
    engine = _engine()

    with engine.begin() as conn:
        for profile in SECTION_TARGETS:
            sec    = profile["section"]
            target = profile["target_rate"]

            row = conn.execute(
                text("SELECT COUNT(*) AS total, SUM(CASE WHEN status='present' THEN 1 ELSE 0 END) AS present_count FROM attendance WHERE class_section = :sec"),
                {"sec": sec},
            ).fetchone()

            total, present = row.total, row.present_count
            target_present = int(total * target)
            to_flip = present - target_present

            if to_flip <= 0:
                print(f"  {sec}: already at or below {target*100:.0f}%, skipping")
                continue

            conn.execute(
                text(f"UPDATE TOP ({to_flip}) attendance SET status = 'absent' WHERE class_section = :sec AND status = 'present'"),
                {"sec": sec},
            )

            actual_rate = (target_present / total) * 100
            print(f"  {sec}: flipped {to_flip:,} rows → ~{actual_rate:.0f}% present  (was {present/total*100:.0f}%)")

    print("\nDone ✓")
    engine.dispose()


if __name__ == "__main__":
    main()
