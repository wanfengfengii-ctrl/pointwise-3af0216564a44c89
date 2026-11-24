from concurrent.futures import ThreadPoolExecutor

from station import (ByteSpan, InvalidByteRange, InvalidGustLine, PollCommitFailed,
                     SourcePoint, UnknownVolume)
from tests.fixtures import Pause, StationCase, capture_counts, trace


class CaptureTests(StationCase):
    def test_v1_duplicate_values_keep_both_source_spans(self):
        self.assertIsNone(self.station.poll())
        self.assertEqual(self.station.append(b"7\n"), ByteSpan(1, 0, 2))
        self.assertEqual(self.station.append(b"7\n"), ByteSpan(1, 2, 4))
        batch = self.station.poll()
        self.assertEqual(trace(batch), ((1, 0, 2, 7), (1, 2, 4, 7)))
        self.assertEqual(self.station.receipt(batch.batch_id), batch)
        self.assertEqual(self.station.checkpoint(), SourcePoint(1, 4))
        self.assertEqual(self.station.read(1, 0, 4), b"7\n7\n")
        self.assertIsNone(self.station.poll())
        self.assertEqual(capture_counts(self.station), (1, 2))

    def test_v1_split_line_and_original_leading_zeroes(self):
        self.station.append(b"00012\n")
        self.station.append(b"0\n")
        fragments = []
        batch = self.station.poll(chunk_size=2, after_chunk=fragments.append)
        self.assertEqual([c.data for c in fragments], [b"00", b"01", b"2\n", b"0\n"])
        self.assertEqual([c.span for c in fragments], [ByteSpan(1, 0, 2), ByteSpan(1, 2, 4),
                                                       ByteSpan(1, 4, 6), ByteSpan(1, 6, 8)])
        self.assertEqual(trace(batch), ((1, 0, 6, 12), (1, 6, 8, 0)))
        self.assertEqual(self.station.read(1, 0, 6), b"00012\n")

    def test_v1_range_errors(self):
        self.station.append(b"7\n")
        self.assertEqual(self.station.read(1, 2, 2), b"")
        with self.assertRaises(UnknownVolume):
            self.station.read(2, 0, 0)
        for start, end in [(-1, 1), (2, 1), (0, 3), (0, None), (False, 1)]:
            with self.subTest(start=start, end=end), self.assertRaises(InvalidByteRange):
                self.station.read(1, start, end)

    def test_v2_invalid_line_leaves_no_bytes(self):
        for raw in (b"-1\n", b"+1\n", b"7\r\n", b"7", b"\n", b"7\n9\n", b"\xff\n"):
            with self.subTest(raw=raw), self.assertRaises(InvalidGustLine):
                self.station.append(raw)
        self.assertEqual(self.station.workfile.size(), 0)
        self.assertEqual(self.station.append(b"7\n"), ByteSpan(1, 0, 2))
        self.assertEqual(self.station.read(1, 0, 2), b"7\n")
        self.assertEqual(trace(self.station.poll()), ((1, 0, 2, 7),))

    def test_concurrent_polls_commit_at_most_one_copy(self):
        self.station.append(b"7\n")
        with ThreadPoolExecutor(max_workers=1) as workers, Pause() as reading:
            waiting = workers.submit(self.station.poll, after_chunk=reading)
            self.assertEqual(reading.wait().data, b"7\n")
            winner = self.station.poll()
            reading.release()
            with self.assertRaises(PollCommitFailed):
                waiting.result(timeout=15)
        self.assertEqual(trace(winner), ((1, 0, 2, 7),))
        self.assertEqual(capture_counts(self.station), (1, 1))
        self.assertEqual(self.station.checkpoint(), SourcePoint(1, 2))
