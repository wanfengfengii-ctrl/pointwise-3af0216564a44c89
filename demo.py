import argparse
import json
from pathlib import Path
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


def main():
    parser = argparse.ArgumentParser(description="Run a full weather-station shift")
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    if args.directory is None:
        with TemporaryDirectory(prefix="gust-demo-") as directory:
            result = run_day(Path(directory))
    else:
        result = run_day(args.directory)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
