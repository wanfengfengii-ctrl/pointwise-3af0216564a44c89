from dataclasses import dataclass

from station.source.positions import SourcePoint


class CheckpointMoved(ValueError):
    pass


@dataclass(frozen=True)
class CaptureCheckpoint:
    point: SourcePoint


def load_checkpoint(store):
    with store.connect_read() as db:
        row = db.execute("SELECT volume, offset FROM checkpoint WHERE singleton = 1").fetchone()
    if row is None:
        raise ValueError("missing capture checkpoint")
    return CaptureCheckpoint(SourcePoint(*row))


def save_checkpoint(db, expected, proposed):
    if proposed <= expected.point:
        raise ValueError("a receipt must advance the capture checkpoint")
    cursor = db.execute("""UPDATE checkpoint SET volume = ?, offset = ?
                           WHERE singleton = 1 AND volume = ? AND offset = ?""",
                        (proposed.volume, proposed.offset,
                         expected.point.volume, expected.point.offset))
    if cursor.rowcount != 1:
        raise CheckpointMoved("another collector advanced the checkpoint")
