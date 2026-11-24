from pathlib import Path
import subprocess
import sys

from station import ByteSpan, InvalidByteRange, Station
from tests.fixtures import StationCase

FIXTURE = Path(__file__).resolve().parent.parent / "submission_fixture.py"
RAW = b"07\n"
CUT_EXIT = 73


def interrupt(root, operation, count):
    result = subprocess.run(
        [sys.executable, "-B", str(FIXTURE), "child", str(root), operation, str(count)],
        capture_output=True, text=True, timeout=30)
    if result.returncode != CUT_EXIT:
        raise AssertionError(f"child missed the byte cut ({result.returncode}): "
                             f"{result.stdout}{result.stderr}")


class SubmissionInterruptTests(StationCase):
    def test_complete_original_keeps_its_first_volume_and_span(self):
        self.station.append(b"5\n")
        first = self.station.poll()
        interrupt(self.root, "submit", len(RAW))
        self.assertEqual(self.station.workfile.path.read_bytes(), b"5\n07\n")
        reopened = Station.initialize(self.root)
        span = ByteSpan(1, 2, 5)
        self.assertEqual(reopened.submission("X"),
                         {"status": "pending", "span": span,
                          "batch_id": None, "ordinal": None})
        reopened.rotate()
        self.assertEqual(reopened.submit("X", RAW)["span"], span)
        with self.assertRaises(ValueError):
            reopened.submit("X", b"7\n")
        reopened.append(b"6\n")
        reopened.submit("Y", RAW)
        reopened.append(b"11\n")
        old = reopened.poll(chunk_size=1)
        self.assertEqual((old.lines[0].span, old.lines[0].report_key), (span, "X"))
        self.assertEqual(reopened.submission("X")["status"], "received")
        self.assertEqual(reopened.receipt(old.batch_id), old)

    def test_prefix_is_completed_in_place_after_reopen(self):
        interrupt(self.root, "submit", 1)
        self.assertEqual(self.station.workfile.path.read_bytes(), b"0")
        reopened = Station.initialize(self.root)
        self.assertEqual(reopened.workfile.path.read_bytes(), RAW)
        self.assertEqual(reopened.submission("X"),
                         {"status": "pending", "span": ByteSpan(1, 0, 3),
                          "batch_id": None, "ordinal": None})
        self.assertEqual(reopened.read(1, 0, 3), RAW)
        with self.assertRaises(ValueError):
            reopened.submit("X", b"7\n")
        cut = reopened.rotate()
        self.assertEqual(reopened.read(1, 0, cut.sealed.end), RAW)
        self.assertEqual(reopened.submit("X", RAW)["span"], ByteSpan(1, 0, 3))
        reopened.append(b"11\n")
        batches = []
        while (batch := reopened.poll(chunk_size=1)) is not None:
            batches.append(batch)
        self.assertEqual([(line.report_key, line.span) for line in batches[0].lines],
                         [("X", ByteSpan(1, 0, 3))])
        self.assertEqual(reopened.submission("X")["status"], "received")

    def test_fragment_that_is_not_a_prefix_is_discarded_for_resubmission(self):
        with self.station.capture.transaction() as db:
            db.execute("""INSERT INTO submissions
                          (report_key, raw, volume, start_byte, end_byte, complete)
                          VALUES ('Z', ?, 1, 0, 3, 0)""", (RAW,))
        with self.station.workfile.path.open("ab") as file:
            file.write(b"7")
        reopened = Station.initialize(self.root)
        self.assertEqual(reopened.workfile.path.read_bytes(), b"")
        self.assertEqual(reopened.submission("Z"),
                         {"status": "pending", "span": None,
                          "batch_id": None, "ordinal": None})
        with self.assertRaises(InvalidByteRange):
            reopened.read(1, 0, 1)
        with self.assertRaises(ValueError):
            reopened.submit("Z", b"7\n")
        self.assertEqual(reopened.submit("Z", RAW),
                         {"status": "pending", "span": ByteSpan(1, 0, 3),
                          "batch_id": None, "ordinal": None})
        self.assertEqual(reopened.read(1, 0, 3), RAW)
        batch = reopened.poll()
        self.assertEqual([(line.report_key, line.span) for line in batch.lines],
                         [("Z", ByteSpan(1, 0, 3))])
