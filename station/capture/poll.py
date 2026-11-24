import sqlite3

from station.capture.checkpoints import CheckpointMoved, load_checkpoint, save_checkpoint
from station.capture.lines import LineAssembler
from station.capture.receipts import write_batch
from station.source.chunks import ReadBindingChanged, read_chunk
from station.source.open import open_read
from station.source.positions import SourcePoint


class PollCommitFailed(RuntimeError):
    pass


def _collect(source, start, chunk_size, after_chunk):
    point = start
    while True:
        session = open_read(source, point.volume, point.offset)
        assembler = LineAssembler(point)
        lines = []
        try:
            while chunk := read_chunk(source, session, chunk_size):
                lines.extend(assembler.feed(chunk))
                if after_chunk is not None:
                    after_chunk(chunk)
        except ReadBindingChanged:
            assembler.discard()
            point = start
            continue

        if session.archive_ref is not None and assembler.has_partial:
            raise OSError(f"sealed volume {point.volume} ends in an incomplete line")
        if lines:
            return tuple(lines), SourcePoint(point.volume, lines[-1].span.end)
        if session.archive_ref is None:
            return (), point
        successor = source.catalog.successor(point.volume)
        if successor is None:
            raise OSError(f"sealed volume {point.volume} has no registered successor")
        point = SourcePoint(successor.volume, 0)


def poll_next_batch(source, store, *, chunk_size=4096, after_chunk=None):
    if type(chunk_size) is not int or chunk_size < 1:
        raise ValueError("capture chunk size must be a positive integer")
    checkpoint = load_checkpoint(store)
    lines, ending = _collect(source, checkpoint.point, chunk_size, after_chunk)
    if ending == checkpoint.point:
        return None
    try:
        with store.transaction() as db:
            batch = write_batch(db, checkpoint.point, ending, lines)
            save_checkpoint(db, checkpoint, ending)
    except (sqlite3.Error, CheckpointMoved) as error:
        raise PollCommitFailed(f"failed to commit capture from {checkpoint.point}") from error
    return batch
