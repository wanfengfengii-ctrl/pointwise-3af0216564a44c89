from dataclasses import dataclass

from station.source.positions import ByteSpan


@dataclass(frozen=True)
class SubmissionRecord:
    report_key: str
    raw: bytes
    volume: int | None
    start: int | None
    end: int | None
    complete: bool

    @property
    def span(self):
        if self.volume is None:
            return None
        return ByteSpan(self.volume, self.start, self.end)

    @property
    def known_span(self):
        return self.span if self.complete else None


_SELECT = ("SELECT report_key, raw, volume, start_byte, end_byte, complete "
           "FROM submissions")


def validate_report_key(report_key):
    if not isinstance(report_key, str) or not report_key:
        raise ValueError("a report key is a non-empty string")
    try:
        report_key.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("a report key must be valid UTF-8") from error
    return report_key


def load_record(db, report_key):
    row = db.execute(_SELECT + " WHERE report_key = ?", (report_key,)).fetchone()
    return None if row is None else SubmissionRecord(*row)


def load_records(db):
    rows = db.execute(_SELECT + " ORDER BY report_key").fetchall()
    return tuple(SubmissionRecord(*row) for row in rows)


def plan_original(db, report_key, raw, volume, start):
    db.execute("""INSERT INTO submissions
                  (report_key, raw, volume, start_byte, end_byte, complete)
                  VALUES (?, ?, ?, ?, ?, 0)
                  ON CONFLICT(report_key) DO UPDATE SET
                      raw = excluded.raw, volume = excluded.volume,
                      start_byte = excluded.start_byte, end_byte = excluded.end_byte,
                      complete = 0""",
               (report_key, raw, volume, start, start + len(raw)))


def mark_complete(db, report_key):
    db.execute("UPDATE submissions SET complete = 1 WHERE report_key = ?", (report_key,))


def clear_span(db, report_key):
    db.execute("""UPDATE submissions
                  SET volume = NULL, start_byte = NULL, end_byte = NULL, complete = 0
                  WHERE report_key = ?""", (report_key,))


def delete_record(db, report_key):
    db.execute("DELETE FROM submissions WHERE report_key = ?", (report_key,))


def received_location(db, report_key):
    return db.execute("""SELECT batch_id, ordinal FROM receipt_lines
                         WHERE report_key = ? ORDER BY batch_id, ordinal LIMIT 1""",
                      (report_key,)).fetchone()


def state_from(db, report_key):
    record = load_record(db, report_key)
    if record is None:
        return {"status": "unknown", "span": None, "batch_id": None, "ordinal": None}
    span = record.known_span
    if span is not None:
        received = received_location(db, report_key)
        if received is not None:
            batch_id, ordinal = received
            return {"status": "received", "span": span,
                    "batch_id": batch_id, "ordinal": ordinal}
    return {"status": "pending", "span": span, "batch_id": None, "ordinal": None}


def span_keys(db, spans):
    """Map proven original spans in this batch to their report keys.

    A key only labels a line once its original is known to be fully written;
    reopen reconciliation proves any line completed before a crash.
    """
    wanted = tuple(spans)
    if not wanted:
        return {}
    rows = db.execute(_SELECT + " WHERE complete = 1 AND volume IS NOT NULL").fetchall()
    lookup = {(row[2], row[3], row[4]): row[0] for row in rows}
    return {span: lookup[(span.volume, span.start, span.end)]
            for span in wanted if (span.volume, span.start, span.end) in lookup}
