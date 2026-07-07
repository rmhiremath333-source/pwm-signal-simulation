"""
Command-line utility to export the PWM change log (pwm_log.db) to a CSV
file, so the logged duty-cycle/frequency/power history can be opened in
Excel/Sheets for a report chart, without needing the web app running.

Usage:
    python tools/export_log.py                 -> writes pwm_log_export.csv
    python tools/export_log.py --out data.csv  -> custom output filename
    python tools/export_log.py --limit 200     -> only the most recent N rows
"""

import argparse
import csv
import os
import sqlite3
import sys

DEFAULT_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pwm_log.db")

COLUMNS = [
    "id",
    "timestamp",
    "duty_cycle",
    "frequency",
    "power_consumed_mw",
    "power_saved_mw",
    "source_device",
]


def fetch_rows(db_path, limit=None):
    if not os.path.exists(db_path):
        raise FileNotFoundError(
            f"Could not find '{db_path}'. Run the app at least once so it "
            "creates and logs to pwm_log.db before exporting."
        )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    query = "SELECT * FROM pwm_log ORDER BY id ASC"
    if limit:
        # keep only the most recent `limit` rows, but still in chronological order
        query = f"SELECT * FROM (SELECT * FROM pwm_log ORDER BY id DESC LIMIT {int(limit)}) ORDER BY id ASC"
    rows = conn.execute(query).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def write_csv(rows, out_path):
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in COLUMNS})


def summarize(rows):
    if not rows:
        print("No rows logged yet.")
        return

    duties = [r["duty_cycle"] for r in rows]
    consumed = [r["power_consumed_mw"] for r in rows]

    print(f"Total logged changes : {len(rows)}")
    print(f"Duty cycle range     : {min(duties)}% - {max(duties)}%")
    print(f"Average duty cycle   : {sum(duties) / len(duties):.1f}%")
    print(f"Average power draw   : {sum(consumed) / len(consumed):.2f} mW")


def main():
    parser = argparse.ArgumentParser(description="Export the PWM change log to CSV.")
    parser.add_argument("--db", default=DEFAULT_DB, help="Path to pwm_log.db")
    parser.add_argument("--out", default="pwm_log_export.csv", help="Output CSV filename")
    parser.add_argument("--limit", type=int, default=None, help="Only export the most recent N rows")
    args = parser.parse_args()

    try:
        rows = fetch_rows(args.db, args.limit)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    write_csv(rows, args.out)
    print(f"Wrote {len(rows)} rows to {args.out}\n")
    summarize(rows)


if __name__ == "__main__":
    main()
