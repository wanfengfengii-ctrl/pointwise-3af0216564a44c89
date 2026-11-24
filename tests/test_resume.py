from concurrent.futures import ThreadPoolExecutor
import json

from station import ByteSpan, PollCommitFailed, SourcePoint, Station, StationStateError
from tests.fixtures import Pause, StationCase, capture_counts, refuse_receipt_line, trace


class ResumeTests(StationCase):
    def _across_cut(self, extra):
        self.station.append(b"7\n")
        first = self.station.poll()
        self.assertEqual(trace(first), ((1, 0, 2, 7),))
        chunks = []
        with ThreadPoolExecutor(max_workers=2) as workers, Pause() as prepared, Pause() as reading:
            turn = workers.submit(self.station.rotate, after_prepare=prepared)
            self.assertEqual(prepared.wait().path.read_bytes(), b"7\n")
            self.assertEqual(self.station.append(b"12\n"), ByteSpan(1, 2, 5))

            def during_chunk(chunk):
                chunks.append(chunk)
                if len(chunks) == 1:
                    reading(chunk)

            collecting = workers.submit(self.station.poll, chunk_size=1, after_chunk=during_chunk)
            paused = reading.wait()
            self.assertEqual((paused.span, paused.data), (ByteSpan(1, 2, 3), b"1"))
            prepared.release()
            turn.result(timeout=15)
            for raw in (b"7\n", b"29\n", *extra):
                self.station.append(raw)
            reading.release()
            old_tail = collecting.result(timeout=15)

        tail_trace = trace(old_tail)
        self.assertEqual(tail_trace, ((1, 2, 5, 12),),
                         f"ROTATION_CAPTURE_HANDOFF old-tail actual={tail_trace!r}")
        self.assertEqual(self.station.receipt(first.batch_id), first)
        self.assertEqual(old_tail.end, SourcePoint(1, 5))
        current = self.station.poll(chunk_size=1)
        self.assertIsNotNone(current)
        self.assertIsNone(self.station.poll())
        return tuple(row for batch in (first, old_tail, current) for row in trace(batch))

    def test_v5_rotation_capture_handoff(self):
        actual = self._across_cut(())
        expected = ((1, 0, 2, 7), (1, 2, 5, 12), (2, 0, 2, 7), (2, 2, 5, 29))
        self.assertEqual(actual, expected, f"ROTATION_CAPTURE_HANDOFF receipt trail actual={actual!r}")
        evidence = [self.station.read(volume, start, end).decode("ascii")
                    for volume, start, end, _ in actual]
        self.assertEqual(evidence, ["7\n", "12\n", "7\n", "29\n"])
        print("POINTWISE_SCENARIO " + json.dumps({
            "case_id": "v5",
            "input": {"precopy": "7\n", "late": "12\n", "paused": "1", "new": "7\n29\n"},
            "expected": expected,
            "actual": actual,
            "downstream_expected": ["7\n", "12\n", "7\n", "29\n"],
            "downstream_actual": evidence,
            "code_paths": ["station/rotation/seal.py", "station/source/chunks.py",
                           "station/capture/poll.py", "station/capture/receipts.py"],
            "oracle": "Count LF-inclusive ASCII byte lengths separately in each input volume.",
        }, sort_keys=True))

    def test_v5_reused_offsets_after_new_volume_outgrows_old(self):
        actual = self._across_cut((b"31\n",))
        self.assertEqual(actual, ((1, 0, 2, 7), (1, 2, 5, 12), (2, 0, 2, 7),
                                  (2, 2, 5, 29), (2, 5, 8, 31)))
        self.assertEqual(self.station.checkpoint(), SourcePoint(2, 8))
        self.assertEqual(self.station.read(1, 2, 5), b"12\n")

    def test_v5_empty_crossing(self):
        self.station.rotate()
        self.station.rotate()
        self.assertEqual(self.station.checkpoint(), SourcePoint(1, 0))
        batch = self.station.poll()
        self.assertEqual((batch.start, batch.end, batch.lines),
                         (SourcePoint(1, 0), SourcePoint(3, 0), ()))
        self.assertEqual(self.station.receipt(batch.batch_id), batch)
        self.assertIsNone(self.station.poll())
        self.station.append(b"7\n")
        self.assertEqual(trace(self.station.poll()), ((3, 0, 2, 7),))

    def test_v6_failed_commit_and_reinitialize(self):
        self.station.append(b"7\n")
        initial = self.station.poll()
        historic = self.station.issue([initial.batch_id])
        self.station.rotate(after_prepare=lambda _: self.station.append(b"12\n"))
        self.station.append(b"7\n")
        self.station.append(b"29\n")
        with refuse_receipt_line(self.station, 1, 2):
            with self.assertRaises(PollCommitFailed):
                self.station.poll(chunk_size=1)
            reopened = Station.initialize(self.root)
            self.assertEqual(reopened.receipt(initial.batch_id), initial)
            self.assertEqual(reopened.get(historic.report_id), historic)
            self.assertEqual(reopened.checkpoint(), SourcePoint(1, 2))
            self.assertEqual(capture_counts(reopened), (1, 1))
        reopened = Station.initialize(self.root, work_name="sensor.log")
        self.assertEqual(reopened.workfile.path, self.station.workfile.path)
        self.assertEqual(reopened.catalog.active().volume, 2)
        self.assertEqual(reopened.read(1, 0, 5), b"7\n12\n")
        self.assertEqual(reopened.read(2, 0, 5), b"7\n29\n")
        self.assertEqual(trace(reopened.poll()), ((1, 2, 5, 12),))
        self.assertEqual(trace(reopened.poll()), ((2, 0, 2, 7), (2, 2, 5, 29)))
        self.assertIsNone(reopened.poll())
        self.assertEqual(capture_counts(reopened), (3, 4))
        self.assertEqual(reopened.get(historic.report_id), historic)

    def test_v6_partial_commit_rolls_back_header_and_early_line(self):
        self.station.append(b"7\n")
        initial = self.station.poll()
        self.station.append(b"12\n")
        self.station.append(b"13\n")
        self.station.rotate()
        with refuse_receipt_line(self.station, 1, 5):
            with self.assertRaises(PollCommitFailed):
                self.station.poll()
            self.assertEqual(capture_counts(self.station), (1, 1))
            self.assertEqual(self.station.checkpoint(), initial.end)
            with self.assertRaises(KeyError):
                self.station.receipt(initial.batch_id + 1)
        reopened = Station.initialize(self.root)
        self.assertEqual(trace(reopened.poll()), ((1, 2, 5, 12), (1, 5, 8, 13)))
        crossing = reopened.poll()
        self.assertEqual((crossing.lines, crossing.end), ((), SourcePoint(2, 0)))
        self.assertIsNone(reopened.poll())
        self.assertEqual(capture_counts(reopened), (3, 3))

    def test_v6_reinitialize_refuses_mismatch_and_missing_workfile(self):
        self.station.append(b"7\n")
        with self.assertRaises(StationStateError):
            Station.initialize(self.root, work_name="another.log")
        self.assertEqual(self.station.read(1, 0, 2), b"7\n")
        original = self.station.workfile.path
        retained = original.with_suffix(".retained")
        original.rename(retained)
        with self.assertRaises(StationStateError):
            Station.initialize(self.root)
        self.assertFalse(original.exists())
        self.assertEqual(retained.read_bytes(), b"7\n")
