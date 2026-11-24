import argparse
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from station import InvalidGustLine, PollCommitFailed, SourcePoint, Station


EXPECTED_ROWS = [
    [1, 0, 2, 7], [1, 2, 4, 7], [1, 4, 7, 12],
    [2, 0, 2, 7], [2, 2, 5, 29], [2, 5, 8, 29],
]
EXPECTED_EVIDENCE = [[2, 2, 5, "29\n"], [2, 5, 8, "29\n"]]


def run_day(directory):
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        raise ValueError("demo requires a new or empty station directory")
    sample_path = Path(__file__).resolve().parent / "samples" / "day.json"
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    station = Station.initialize(directory)
    try:
        station.append(sample["reject"].encode("ascii"))
    except InvalidGustLine:
        pass
    else:
        raise AssertionError("the negative sample was accepted")

    for raw in sample["day"]:
        station.append(raw.encode("ascii"))
    day = station.poll(chunk_size=1)
    if day is None or day.end != SourcePoint(1, 4):
        raise AssertionError(f"unexpected white-shift receipt: {day}")

    def receive_late(_draft):
        for raw in sample["late"]:
            station.append(raw.encode("ascii"))

    cut = station.rotate(after_prepare=receive_late)
    for raw in sample["new"]:
        station.append(raw.encode("ascii"))

    with station.capture.transaction() as db:
        db.execute("""CREATE TRIGGER demo_refuse_old_tail BEFORE INSERT ON receipt_lines
                      WHEN NEW.volume = 1 AND NEW.start_byte = 4
                      BEGIN SELECT RAISE(ABORT, 'demo receipt write refused'); END""")
    try:
        try:
            station.poll(chunk_size=1)
        except PollCommitFailed:
            if station.receipt(day.batch_id) != day or station.checkpoint() != day.end:
                raise AssertionError("failed receipt changed the committed white-shift state")
        else:
            raise AssertionError("expected one rejected SQLite receipt write")
    finally:
        with station.capture.transaction() as db:
            db.execute("DROP TRIGGER demo_refuse_old_tail")

    station = Station.initialize(directory)
    tail = station.poll(chunk_size=1)
    current = station.poll(chunk_size=1)
    if tail is None or current is None or station.poll() is not None:
        raise AssertionError("reopened collector did not consume exactly two batches")
    batches = (day, tail, current)
    rows = [[line.span.volume, line.span.start, line.span.end, line.value]
            for batch in batches for line in station.receipt(batch.batch_id).lines]
    if rows != EXPECTED_ROWS or cut.sealed.end != 7:
        raise AssertionError(f"unexpected source trace: {rows}, old extent {cut.sealed.end}")
    report = station.issue(batch.batch_id for batch in batches)
    station = Station.initialize(directory)
    reopened = station.get(report.report_id)
    evidence = [[item.span.volume, item.span.start, item.span.end, item.data.decode("ascii")]
                for item in station.peak_evidence(report.report_id)]
    if (reopened != report or reopened.summary.sample_count != 6
            or reopened.summary.peak_value != 29 or evidence != EXPECTED_EVIDENCE):
        raise AssertionError(f"unexpected handover: {reopened}, evidence={evidence}")
    return {"received": rows, "report_id": reopened.report_id, "parent_batches": reopened.parent_ids,
            "sample_count": reopened.summary.sample_count, "peak_ms": reopened.summary.peak_value,
            "peak_evidence": evidence}


def run_submissions(directory):
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        raise ValueError("demo requires a new or empty station directory")
    station = Station.initialize(Path(directory))
    station.append(b"5\n")
    first = station.poll()
    station.issue([first.batch_id])
    script = Path(__file__).resolve().parent / "submission_fixture.py"
    reply_lost = [sys.executable, "-B", str(script), "child", str(station.root),
                  "submit", "3"]
    result = subprocess.run(reply_lost, capture_output=True, text=True, timeout=30)
    if result.returncode != 73:
        raise AssertionError(f"terminal stand-in missed the byte cut: {result.stderr}")
    station = Station.initialize(station.root)
    ledgers = {}
    for key in ("X", "Y"):
        entry = station.submission(key)
        ledgers[key] = {"status": entry["status"], "batch_id": entry["batch_id"],
                        "ordinal": entry["ordinal"]}
    station.rotate()
    station.submit("X", b"07\n")
    station.append(b"6\n")
    station.submit("Y", b"07\n")
    station.append(b"11\n")
    old = station.poll(chunk_size=1)
    current = station.poll(chunk_size=1)
    if old is None or current is None or station.poll() is not None:
        raise AssertionError("unexpected receipt trail after the lost reply")
    station = Station.initialize(station.root)
    received = {}
    for key, batch, ordinal in (("X", old, 0), ("Y", current, 1)):
        entry = station.submission(key)
        span = entry["span"]
        raw = station.read(span.volume, span.start, span.end)
        if (entry["status"], entry["batch_id"], entry["ordinal"]) != (
                "received", batch.batch_id, ordinal) or raw != b"07\n":
            raise AssertionError(f"unexpected receipt for {key}: {entry}, raw={raw!r}")
        received[key] = {"span": [span.volume, span.start, span.end],
                         "batch_id": entry["batch_id"], "ordinal": entry["ordinal"],
                         "raw": raw.decode("ascii")}
    return {"lost_reply_ledger": ledgers,
            "rows": [[line.span.volume, line.span.start, line.span.end, line.value,
                      line.report_key] for batch in (old, current) for line in batch.lines],
            "received": received}


def main():
    parser = argparse.ArgumentParser(description="Run a full weather-station shift")
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    if args.directory is None:
        with TemporaryDirectory(prefix="gust-demo-") as directory:
            result = run_day(Path(directory) / "shift")
            result["submissions"] = run_submissions(Path(directory) / "submissions")
    else:
        result = run_day(args.directory)
        result["submissions"] = run_submissions(args.directory.parent
                                                / (args.directory.name + "-submissions"))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
