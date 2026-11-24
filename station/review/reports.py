from dataclasses import dataclass

from station.database import connect_database, valid_id, write_transaction
from station.recorder.format import decode_gust_value, encode_gust_value
from station.review.summary import ShiftSummary
from station.source.positions import ByteSpan


_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS reports (
        report_id INTEGER PRIMARY KEY,
        sample_count INTEGER NOT NULL CHECK(sample_count >= 0),
        peak_value TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS report_parents (
        report_id INTEGER NOT NULL REFERENCES reports(report_id),
        ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
        batch_id INTEGER NOT NULL CHECK(batch_id >= 1),
        PRIMARY KEY(report_id, ordinal),
        UNIQUE(report_id, batch_id)
    )""",
    """CREATE TABLE IF NOT EXISTS report_peaks (
        report_id INTEGER NOT NULL REFERENCES reports(report_id),
        ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
        volume INTEGER NOT NULL CHECK(volume >= 1),
        start_byte INTEGER NOT NULL CHECK(start_byte >= 0),
        end_byte INTEGER NOT NULL CHECK(end_byte > start_byte),
        PRIMARY KEY(report_id, ordinal)
    )""",
)


@dataclass(frozen=True)
class StoredReport:
    report_id: int
    parent_ids: tuple[int, ...]
    summary: ShiftSummary


class ReportStore:
    def __init__(self, path):
        self.path = path

    def initialize(self, *, create=False):
        if create:
            with write_transaction(self.path, create=True) as db:
                for statement in _SCHEMA:
                    db.execute(statement)
        with connect_database(self.path) as db:
            db.execute("SELECT report_id, sample_count, peak_value FROM reports LIMIT 0")
            db.execute("SELECT report_id, ordinal, batch_id FROM report_parents LIMIT 0")
            db.execute("SELECT report_id, ordinal, volume, start_byte, end_byte "
                       "FROM report_peaks LIMIT 0")

    def save(self, parents, summary):
        parents = tuple(parents)
        value = None if summary.peak_value is None else encode_gust_value(summary.peak_value)
        with write_transaction(self.path) as db:
            cursor = db.execute("INSERT INTO reports (sample_count, peak_value) VALUES (?, ?)",
                                (summary.sample_count, value))
            report_id = cursor.lastrowid
            db.executemany("INSERT INTO report_parents VALUES (?, ?, ?)",
                           ((report_id, index, parent) for index, parent in enumerate(parents)))
            db.executemany("INSERT INTO report_peaks VALUES (?, ?, ?, ?, ?)",
                           ((report_id, index, span.volume, span.start, span.end)
                            for index, span in enumerate(summary.peak_spans)))
        return StoredReport(report_id, parents, summary)

    def load(self, report_id):
        if not valid_id(report_id):
            return None
        with connect_database(self.path) as db:
            header = db.execute("SELECT sample_count, peak_value FROM reports WHERE report_id = ?",
                                (report_id,)).fetchone()
            if header is None:
                return None
            parents = db.execute("""SELECT batch_id FROM report_parents WHERE report_id = ?
                                    ORDER BY ordinal""", (report_id,))
            parent_ids = tuple(row[0] for row in parents)
            peaks = db.execute("""SELECT volume, start_byte, end_byte FROM report_peaks
                                  WHERE report_id = ? ORDER BY ordinal""", (report_id,))
            spans = tuple(ByteSpan(*row) for row in peaks)
        sample_count, value = header
        peak = None if value is None else decode_gust_value(value)
        return StoredReport(report_id, parent_ids, ShiftSummary(sample_count, peak, spans))
