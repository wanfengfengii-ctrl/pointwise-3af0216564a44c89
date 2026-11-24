import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

from station import ByteSpan, InvalidByteRange, PollCommitFailed, Station
from tests.fixtures import capture_counts, refuse_receipt_line, trace

RAW = b"07\n"
CUT_EXIT = 73
SCRIPT = Path(__file__).resolve()


class InterruptedFile:
    def __init__(self, stream, remaining):
        self.stream = stream
        self.remaining = remaining

    def __enter__(self):
        return self

    def __exit__(self, *error):
        return self.stream.__exit__(*error)

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def write(self, data):
        stop_at = self.remaining[0]
        expected = min(len(data), stop_at)
        written = self.stream.write(data[:expected])
        if written != expected:
            raise OSError("fixture encountered an unexpected short write")
        self.remaining[0] -= written
        if self.remaining[0] == 0:
            self.stream.flush()
            os._exit(CUT_EXIT)
        return written


def child(directory, operation, count):
    station = Station.initialize(directory)
    watched = station.workfile.path
    original_open = Path.open
    remaining = [count]

    def intercept(path, mode="r", *args, **kwargs):
        stream = original_open(path, mode, *args, **kwargs)
        if path == watched and any(flag in mode for flag in "wa+x"):
            return InterruptedFile(stream, remaining)
        return stream

    with patch.object(Path, "open", intercept):
        if operation == "append":
            station.append(RAW)
        else:
            station.submit("X", RAW)
    raise AssertionError("the operation returned without reaching the byte cut")


def interrupt(station, operation, count, expected):
    argv = [sys.executable, "-B", str(SCRIPT), "child", str(station.root),
            operation, str(count)]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    if result.returncode != CUT_EXIT:
        raise AssertionError(f"child missed the byte cut ({result.returncode}):\n"
                             f"{result.stdout}{result.stderr}")
    actual = station.workfile.path.read_bytes()
    assert actual == expected, (actual, expected)


def drain(station):
    batches = []
    while (batch := station.poll(chunk_size=1)) is not None:
        batches.append(batch)
    return batches


def baseline():
    with TemporaryDirectory(prefix="gust-baseline-") as directory:
        station = Station.initialize(Path(directory) / "full")
        interrupt(station, "append", len(RAW), RAW)
        station = Station.initialize(station.root)
        station.rotate()
        station.append(RAW)
        station.append(RAW)
        rows = [row for batch in drain(station) for row in trace(batch)]
        assert rows == [(1, 0, 3, 7), (2, 0, 3, 7), (2, 3, 6, 7)], rows
        station = Station.initialize(Path(directory) / "partial")
        interrupt(station, "append", 1, b"0")
        station = Station.initialize(station.root)
        assert station.read(1, 0, 1) == b"0"
        assert station.append(b"11\n") == ByteSpan(1, 1, 4)
        joined = station.poll(chunk_size=1)
        assert trace(joined) == ((1, 0, 4, 11),)
        raw = station.read(1, 0, 4)
        assert raw == b"011\n", raw
        return {"ordinary_rows": rows, "joined_original": raw.decode("ascii")}


def state(status, span=None, batch_id=None, ordinal=None):
    return dict(status=status, span=span, batch_id=batch_id, ordinal=ordinal)


def check_conflict(station):
    try:
        station.submit("X", b"7\n")
    except ValueError:
        return
    raise AssertionError("X accepted a different original")


def full_case(directory):
    station = Station.initialize(directory)
    station.append(b"5\n")
    first = station.poll()
    historic = station.issue([first.batch_id])
    assert station.submission("X") == state("unknown")
    interrupt(station, "submit", len(RAW), b"5\n07\n")
    station = Station.initialize(directory)
    old_span = ByteSpan(1, 2, 5)
    new_span = ByteSpan(2, 2, 5)
    assert station.submission("X") == state("pending", old_span)
    assert station.rotate().sealed.end == 5
    assert station.submit("X", RAW) == state("pending", old_span)
    check_conflict(station)
    station.append(b"6\n")
    assert station.submit("Y", RAW) == state("pending", new_span)
    station.append(b"11\n")
    old = station.poll(chunk_size=1)
    assert trace(old) == ((1, 2, 5, 7),)
    old_state = state("received", old_span, old.batch_id, 0)
    assert station.submission("X") == old_state
    counts = capture_counts(station)
    with refuse_receipt_line(station, 2, 5):
        try:
            station.poll(chunk_size=1)
        except PollCommitFailed:
            pass
        else:
            raise AssertionError("expected SQLite to reject the mixed receipt")
        assert station.checkpoint() == old.end
        assert capture_counts(station) == counts
        assert station.submission("Y") == state("pending", new_span)
    station = Station.initialize(directory)
    new = station.poll(chunk_size=1)
    assert trace(new) == ((2, 0, 2, 6), (2, 2, 5, 7), (2, 5, 8, 11))
    assert station.poll() is None
    station = Station.initialize(directory)
    new_state = state("received", new_span, new.batch_id, 1)
    assert station.receipt(first.batch_id) == first
    assert station.get(historic.report_id) == historic
    assert station.receipt(first.batch_id).lines[0].report_key is None
    assert station.receipt(old.batch_id).lines[0].report_key == "X"
    assert [line.report_key for line in station.receipt(new.batch_id).lines] == [None, "Y", None]
    assert station.submit("X", RAW) == old_state
    assert station.submission("Y") == new_state
    assert station.poll() is None
    received = {}
    for key in ("X", "Y"):
        item = station.submission(key)
        span = item["span"]
        raw = station.read(span.volume, span.start, span.end)
        assert raw == RAW
        received[key] = {**item, "span": [span.volume, span.start, span.end],
                         "raw": raw.decode("ascii")}
    return {"received": received, "old_report": historic.report_id,
            "rows": [*trace(old), *trace(new)]}


def partial_case(directory):
    station = Station.initialize(directory)
    interrupt(station, "submit", 1, b"0")
    station = Station.initialize(directory)
    assert station.submission("X")["status"] == "pending"
    check_conflict(station)
    try:
        station.read(1, 0, 1)
    except InvalidByteRange:
        visible = False
    else:
        assert station.read(1, 0, 3) == RAW
        visible = True
    cut = station.rotate()
    sealed = station.read(1, 0, cut.sealed.end)
    assert sealed in (b"", RAW) and (not visible or sealed == RAW), sealed
    ordinary = station.append(b"11\n")
    assert ordinary == ByteSpan(2, 0, 3)
    reports = drain(station)
    rows = [row for batch in reports for row in trace(batch)]
    expected = [(1, 0, 3, 7)] if sealed else []
    assert rows == expected + [(2, 0, 3, 11)], rows
    if sealed:
        assert station.submit("X", RAW) == state("received", ByteSpan(1, 0, 3),
                                                reports[0].batch_id, 0)
        assert station.poll() is None
    else:
        assert station.submission("X") == state("pending")
        assert station.submit("X", RAW) == state("pending", ByteSpan(2, 3, 6))
        later = station.poll(chunk_size=1)
        assert trace(later) == ((2, 3, 6, 7),)
        assert station.submission("X") == state("received", ByteSpan(2, 3, 6), later.batch_id, 0)
        assert station.poll() is None
    return {"sealed": sealed.decode("ascii"), "rows_before_retry": rows}


def main(args):
    if len(args) == 4 and args[0] == "child":
        _, directory, operation, count = args
        child(directory, operation, int(count))
    elif args == ["baseline"]:
        print(json.dumps(baseline(), sort_keys=True))
    elif args in (["verify"], ["demo"]):
        if args == ["verify"]:
            subprocess.run([sys.executable, "-B", "check_project.py"],
                           cwd=SCRIPT.parent, check=True)
        with TemporaryDirectory(prefix="gust-submission-") as directory:
            result = full_case(Path(directory) / "full")
            if args == ["verify"]:
                result["interrupted_prefix"] = partial_case(Path(directory) / "partial")
            print(json.dumps(result, sort_keys=True))
    else:
        raise SystemExit("usage: submission_fixture.py baseline|verify|demo")


if __name__ == "__main__":
    main(sys.argv[1:])
