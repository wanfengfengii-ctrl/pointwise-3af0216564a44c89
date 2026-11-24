from pathlib import Path
import unittest

from station import ByteSpan, InvalidGustLine, PollCommitFailed, SourcePoint, Station
from tests.fixtures import StationCase, capture_counts, refuse_receipt_line, trace


def state(status, span=None, batch_id=None, ordinal=None):
    return dict(status=status, span=span, batch_id=batch_id, ordinal=ordinal)


class SubmissionTests(StationCase):
    def test_unknown_pending_received_and_duplicate_rules(self):
        self.assertEqual(self.station.submission("X"), state("unknown"))
        self.assertEqual(self.station.submit("X", b"07\n"),
                         state("pending", ByteSpan(1, 0, 3)))
        self.assertEqual(self.station.submit("X", b"07\n"),
                         state("pending", ByteSpan(1, 0, 3)))
        with self.assertRaises(ValueError):
            self.station.submit("X", b"7\n")
        self.assertEqual(self.station.submission("X"), state("pending", ByteSpan(1, 0, 3)))
        with self.assertRaises(InvalidGustLine):
            self.station.submit("B", b"-1\n")
        self.assertEqual(self.station.submission("B"), state("unknown"))
        self.assertEqual(self.station.workfile.size(), 3)

    def test_same_bytes_under_different_keys_are_separate_lines(self):
        self.station.submit("X", b"7\n")
        self.station.submit("Y", b"7\n")
        self.assertEqual(self.station.read(1, 0, 4), b"7\n7\n")
        batch = self.station.poll(chunk_size=1)
        self.assertEqual(trace(batch), ((1, 0, 2, 7), (1, 2, 4, 7)))
        self.assertEqual([line.report_key for line in batch.lines], ["X", "Y"])
        self.assertEqual(self.station.submission("X"),
                         state("received", ByteSpan(1, 0, 2), batch.batch_id, 0))
        self.assertEqual(self.station.submission("Y"),
                         state("received", ByteSpan(1, 2, 4), batch.batch_id, 1))

    def test_receipt_batch_round_trips_report_keys(self):
        self.station.submit("X", b"7\n")
        self.station.append(b"9\n")
        batch = self.station.poll()
        self.assertEqual([line.report_key for line in batch.lines], ["X", None])
        self.assertEqual(self.station.receipt(batch.batch_id), batch)
        reopened = Station.initialize(self.root)
        self.assertEqual([line.report_key for line in reopened.receipt(batch.batch_id).lines],
                         ["X", None])
        self.assertEqual(reopened.submission("X"),
                         state("received", ByteSpan(1, 0, 2), batch.batch_id, 0))

    def test_failed_receipt_keeps_key_pending_and_checkpoint(self):
        self.station.submit("X", b"7\n")
        self.station.append(b"11\n")
        before = capture_counts(self.station)
        with refuse_receipt_line(self.station, 1, 2):
            with self.assertRaises(PollCommitFailed):
                self.station.poll(chunk_size=1)
            self.assertEqual(self.station.checkpoint(), SourcePoint(1, 0))
            self.assertEqual(capture_counts(self.station), before)
            self.assertEqual(self.station.submission("X"), state("pending", ByteSpan(1, 0, 2)))
            size = self.station.workfile.size()
            self.assertEqual(self.station.submit("X", b"7\n")["status"], "pending")
            self.assertEqual(self.station.workfile.size(), size)
        batch = self.station.poll()
        self.assertEqual(trace(batch), ((1, 0, 2, 7), (1, 2, 5, 11)))
        self.assertEqual(self.station.submission("X"),
                         state("received", ByteSpan(1, 0, 2), batch.batch_id, 0))

    def test_ordinary_lines_have_no_key(self):
        self.station.append(b"7\n")
        batch = self.station.poll()
        self.assertIsNone(batch.lines[0].report_key)
        self.assertIsNone(self.station.receipt(batch.batch_id).lines[0].report_key)

    def test_rotation_keeps_sealed_span_and_new_volume_span(self):
        self.station.submit("X", b"07\n")
        self.station.rotate()
        self.station.submit("Y", b"07\n")
        old = self.station.poll()
        new = self.station.poll()
        self.assertEqual(self.station.submission("X"),
                         state("received", ByteSpan(1, 0, 3), old.batch_id, 0))
        self.assertEqual(self.station.submission("Y"),
                         state("received", ByteSpan(2, 0, 3), new.batch_id, 0))
        self.assertEqual(self.station.read(1, 0, 3), b"07\n")
        self.assertEqual(self.station.read(2, 0, 3), b"07\n")

    def test_late_submission_during_rotation_stays_in_old_volume(self):
        self.station.append(b"7\n")
        self.station.submit("X", b"07\n")
        self.station.rotate(after_prepare=lambda _draft: self.station.submit("Y", b"07\n"))
        self.assertEqual(self.station.submission("X")["span"], ByteSpan(1, 2, 5))
        self.assertEqual(self.station.submission("Y")["span"], ByteSpan(1, 5, 8))
        self.assertEqual(self.station.read(1, 0, 8), b"7\n07\n07\n")
        old = self.station.poll(chunk_size=1)
        self.assertEqual([(line.span, line.report_key) for line in old.lines],
                         [(ByteSpan(1, 0, 2), None), (ByteSpan(1, 2, 5), "X"),
                          (ByteSpan(1, 5, 8), "Y")])
        self.assertEqual(self.station.submission("X")["status"], "received")
        self.assertEqual(self.station.submission("Y")["status"], "received")

    def test_database_migrates_pre_submission_ledger(self):
        self.station.append(b"7\n")
        first = self.station.poll()
        self._downgrade_capture_ledger()
        reopened = Station.initialize(self.root)
        self.assertEqual([(line.value, line.report_key)
                          for line in reopened.receipt(first.batch_id).lines], [(7, None)])
        self.assertEqual(reopened.submit("N", b"9\n")["status"], "pending")
        second = reopened.poll()
        self.assertEqual([line.report_key for line in second.lines], ["N"])

    def _downgrade_capture_ledger(self):
        import sqlite3
        db = sqlite3.connect(self.root / "capture.sqlite3")
        try:
            db.execute("DROP TABLE submissions")
            db.execute("ALTER TABLE receipt_lines RENAME TO old_lines")
            db.execute("""CREATE TABLE receipt_lines (
                batch_id INTEGER NOT NULL REFERENCES receipt_batches(batch_id),
                ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
                value TEXT NOT NULL,
                volume INTEGER NOT NULL CHECK(volume >= 1),
                start_byte INTEGER NOT NULL CHECK(start_byte >= 0),
                end_byte INTEGER NOT NULL CHECK(end_byte > start_byte),
                PRIMARY KEY(batch_id, ordinal), UNIQUE(volume, start_byte, end_byte))""")
            db.execute("""INSERT INTO receipt_lines
                          SELECT batch_id, ordinal, value, volume, start_byte, end_byte
                          FROM old_lines""")
            db.execute("DROP TABLE old_lines")
            db.commit()
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
