from contextlib import contextmanager
from dataclasses import dataclass

from station.database import connect_database, valid_id, write_transaction


@dataclass(frozen=True)
class VolumeEntry:
    volume: int
    archive_ref: str | None
    end: int | None

    @property
    def is_active(self):
        return self.archive_ref is None


class VolumeCatalog:
    def __init__(self, path):
        self.path = path

    def initialize(self, work_name=None, *, create=False):
        if create:
            with write_transaction(self.path, create=True) as db:
                db.execute("""CREATE TABLE IF NOT EXISTS station (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    work_name TEXT NOT NULL
                )""")
                db.execute("""CREATE TABLE IF NOT EXISTS volumes (
                    volume INTEGER PRIMARY KEY CHECK(volume >= 1),
                    archive_ref TEXT UNIQUE,
                    end_byte INTEGER,
                    CHECK((archive_ref IS NULL AND end_byte IS NULL) OR
                          (archive_ref IS NOT NULL AND end_byte IS NOT NULL AND end_byte >= 0))
                )""")
                db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS one_active_volume
                              ON volumes ((1)) WHERE archive_ref IS NULL""")
                db.execute("INSERT OR IGNORE INTO station VALUES (1, ?)", (work_name,))
                db.execute("""INSERT INTO volumes VALUES (1, NULL, NULL)
                              ON CONFLICT(volume) DO NOTHING""")
        with connect_database(self.path) as db:
            row = db.execute("SELECT work_name FROM station WHERE singleton = 1").fetchone()
        if row is None:
            raise ValueError("missing work file registration")
        registered = row[0]
        if work_name is not None and registered != work_name:
            raise ValueError("requested work file does not match the station registration")
        return registered

    def entries(self):
        with connect_database(self.path) as db:
            rows = db.execute("SELECT volume, archive_ref, end_byte FROM volumes ORDER BY volume")
            entries = tuple(VolumeEntry(*row) for row in rows)
        if not entries:
            raise ValueError("volume catalog is empty")
        last = len(entries)
        for index, entry in enumerate(entries, 1):
            if entry.volume != index:
                raise ValueError("volume catalog has a gap")
            if index == last:
                if not entry.is_active or entry.end is not None:
                    raise ValueError("missing active volume")
            elif (entry.archive_ref != f"volume-{index}.log" or type(entry.end) is not int
                  or entry.end < 0):
                raise ValueError(f"incomplete registration for sealed volume {index}")
        return entries

    def active(self):
        with connect_database(self.path) as db:
            row = db.execute("""SELECT volume, archive_ref, end_byte FROM volumes
                                WHERE archive_ref IS NULL""").fetchone()
        if row is None:
            raise ValueError("missing active volume")
        return VolumeEntry(*row)

    def lookup(self, volume):
        if not valid_id(volume):
            return None
        with connect_database(self.path) as db:
            row = db.execute("""SELECT volume, archive_ref, end_byte FROM volumes
                                WHERE volume = ?""", (volume,)).fetchone()
        return None if row is None else VolumeEntry(*row)

    def successor(self, volume):
        return self.lookup(volume + 1)

    @contextmanager
    def publish_cut(self, volume, archive_ref, end):
        with write_transaction(self.path) as db:
            result = db.execute("""UPDATE volumes SET archive_ref = ?, end_byte = ?
                                   WHERE volume = ? AND archive_ref IS NULL""",
                                (archive_ref, end, volume))
            if result.rowcount != 1:
                raise ValueError("cut no longer refers to the active volume")
            db.execute("INSERT INTO volumes VALUES (?, NULL, NULL)", (volume + 1,))
            yield VolumeEntry(volume + 1, None, None)
