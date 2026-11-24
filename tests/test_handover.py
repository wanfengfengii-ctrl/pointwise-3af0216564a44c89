import json

from station import ByteSpan, InvalidParents, Station, UnknownReport
from station.source.evidence import EvidenceBytes
from tests.fixtures import StationCase, report_count


class HandoverTests(StationCase):
    def _first_report(self):
        for raw in (b"7\n", b"12\n", b"12\n"):
            self.station.append(raw)
        first = self.station.poll(chunk_size=2)
        return first, self.station.issue([first.batch_id])

    def test_v7_peak_bytes_and_historical_read(self):
        first, report = self._first_report()
        self.station.rotate()
        self.station.append(b"29\n")
        later = self.station.poll()
        newer = self.station.issue([later.batch_id])
        reopened = Station.initialize(self.root)
        actual = reopened.get(report.report_id)
        self.assertEqual(actual, report)
        self.assertEqual(actual.parent_ids, (first.batch_id,))
        self.assertEqual(actual.summary.sample_count, 3)
        self.assertEqual(actual.summary.peak_value, 12)
        spans = (ByteSpan(1, 2, 5), ByteSpan(1, 5, 8))
        self.assertEqual(actual.summary.peak_spans, spans)
        evidence = reopened.peak_evidence(report.report_id)
        self.assertEqual(evidence, tuple(EvidenceBytes(span, b"12\n") for span in spans))
        self.assertEqual(reopened.get(newer.report_id), newer)
        self.assertNotEqual(actual.report_id, newer.report_id)
        self.assertEqual(newer.summary.peak_value, 29)
        print("POINTWISE_SCENARIO " + json.dumps({
            "case_id": "v7",
            "input": {"old_rows": ["7\n", "12\n", "12\n"], "new_row": "29\n"},
            "expected": {"sample_count": 3, "peak_ms": 12, "spans": [[1, 2, 5], [1, 5, 8]]},
            "actual": {"sample_count": actual.summary.sample_count,
                       "peak_ms": actual.summary.peak_value,
                       "spans": [[span.volume, span.start, span.end] for span in actual.summary.peak_spans]},
            "downstream_expected": ["12\n", "12\n"],
            "downstream_actual": [item.data.decode("ascii") for item in evidence],
            "code_paths": ["station/review/summary.py", "station/review/peak.py",
                           "station/review/reports.py", "station/source/evidence.py"],
            "oracle": "Count three old lines, choose the two 12s, and slice the original ASCII log.",
        }, sort_keys=True))

    def test_v7_parent_order_controls_tie_order(self):
        self.station.append(b"7\n")
        self.station.append(b"012\n")
        first = self.station.poll()
        self.station.rotate()
        self.station.append(b"12\n")
        second = self.station.poll()
        report = self.station.issue([second.batch_id, first.batch_id])
        self.assertEqual(report.parent_ids, (second.batch_id, first.batch_id))
        self.assertEqual(report.summary.sample_count, 3)
        self.assertEqual(report.summary.peak_value, 12)
        self.assertEqual(report.summary.peak_spans, (ByteSpan(2, 0, 3), ByteSpan(1, 2, 6)))
        self.assertEqual([item.data for item in self.station.peak_evidence(report.report_id)],
                         [b"12\n", b"012\n"])

    def test_v7_empty_batch_and_zero(self):
        self.station.rotate()
        empty = self.station.poll()
        report = self.station.issue([empty.batch_id])
        self.assertEqual(report.summary.sample_count, 0)
        self.assertIsNone(report.summary.peak_value)
        self.assertEqual(report.summary.peak_spans, ())
        self.assertEqual(self.station.peak_evidence(report.report_id), ())
        self.station.append(b"00\n")
        zero = self.station.poll()
        with_zero = self.station.issue([empty.batch_id, zero.batch_id])
        self.assertEqual(with_zero.summary.sample_count, 1)
        self.assertEqual(with_zero.summary.peak_value, 0)
        self.assertEqual(with_zero.summary.peak_spans, (ByteSpan(2, 0, 3),))

    def test_precise_large_integer_through_capture_and_review(self):
        raw = b"0" + b"9" * 4400 + b"\n"
        self.station.append(b"100000000000000000000\n")
        address = self.station.append(raw)
        batch = self.station.poll(chunk_size=127)
        report = self.station.issue([batch.batch_id])
        expected = 10**4400 - 1
        reopened = Station.initialize(self.root)
        self.assertTrue(reopened.receipt(batch.batch_id).lines[1].value == expected)
        self.assertTrue(reopened.get(report.report_id).summary.peak_value == expected)
        self.assertEqual(reopened.peak_evidence(report.report_id), (EvidenceBytes(address, raw),))

    def test_v8_missing_duplicate_and_empty_parents_preserve_old_report(self):
        first, report = self._first_report()
        legitimate = self.station.peak_evidence(report.report_id)
        requests = ((first.batch_id, 9999), (first.batch_id, first.batch_id), ())
        for parents in requests:
            with self.subTest(parents=parents), self.assertRaises(InvalidParents):
                self.station.issue(parents)
            self.assertEqual(report_count(self.station), 1)
            self.assertEqual(self.station.get(report.report_id), report)
            self.assertEqual(self.station.peak_evidence(report.report_id), legitimate)

    def test_v8_unknown_report(self):
        _, report = self._first_report()
        for operation in (self.station.get, self.station.peak_evidence):
            with self.assertRaises(UnknownReport):
                operation(report.report_id + 1)
        self.assertEqual(report_count(self.station), 1)
