from dataclasses import dataclass

from station.database import valid_id
from station.recorder.format import decode_gust_value, encode_gust_value
from station.source.positions import ByteSpan, SourcePoint


@dataclass(frozen=True)
class ReceiptLine:
    value: int
    span: ByteSpan
    report_key: str | None = None


@dataclass(frozen=True)
class ReceiptBatch:
    batch_id: int
    start: SourcePoint
    end: SourcePoint
    lines: tuple[ReceiptLine, ...]


def write_batch(db, start, end, lines, report_keys=None):
    report_keys = report_keys or {}
    cursor = db.execute("""INSERT INTO receipt_batches
                           (start_volume, start_byte, end_volume, end_byte) VALUES (?, ?, ?, ?)""",
                        (start.volume, start.offset, end.volume, end.offset))
    batch_id = cursor.lastrowid
    keys = tuple(report_keys.get(line.span) for line in lines)
    db.executemany("""INSERT INTO receipt_lines
                       (batch_id, ordinal, value, volume, start_byte, end_byte, report_key)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                   ((batch_id, ordinal, encode_gust_value(line.value), line.span.volume,
                     line.span.start, line.span.end, keys[ordinal])
                    for ordinal, line in enumerate(lines)))
    return ReceiptBatch(batch_id, start, end,
                        tuple(ReceiptLine(line.value, line.span, keys[ordinal])
                              for ordinal, line in enumerate(lines)))


def get_batch(store, batch_id):
    if not valid_id(batch_id):
        raise KeyError(batch_id)
    with store.connect_read() as db:
        header = db.execute("""SELECT start_volume, start_byte, end_volume, end_byte
                               FROM receipt_batches WHERE batch_id = ?""", (batch_id,)).fetchone()
        if header is None:
            raise KeyError(batch_id)
        detail = db.execute("""SELECT value, volume, start_byte, end_byte, report_key
                               FROM receipt_lines
                               WHERE batch_id = ? ORDER BY ordinal""", (batch_id,))
        lines = tuple(ReceiptLine(decode_gust_value(value), ByteSpan(volume, start, end), key)
                      for value, volume, start, end, key in detail)
    return ReceiptBatch(batch_id, SourcePoint(*header[:2]), SourcePoint(*header[2:]), lines)
