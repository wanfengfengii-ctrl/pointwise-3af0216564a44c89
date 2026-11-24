from station.database import connect_database, write_transaction


_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS checkpoint (
        singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
        volume INTEGER NOT NULL CHECK(volume >= 1),
        offset INTEGER NOT NULL CHECK(offset >= 0)
    )""",
    """CREATE TABLE IF NOT EXISTS receipt_batches (
        batch_id INTEGER PRIMARY KEY,
        start_volume INTEGER NOT NULL,
        start_byte INTEGER NOT NULL,
        end_volume INTEGER NOT NULL,
        end_byte INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS receipt_lines (
        batch_id INTEGER NOT NULL REFERENCES receipt_batches(batch_id),
        ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
        value TEXT NOT NULL,
        volume INTEGER NOT NULL CHECK(volume >= 1),
        start_byte INTEGER NOT NULL CHECK(start_byte >= 0),
        end_byte INTEGER NOT NULL CHECK(end_byte > start_byte),
        PRIMARY KEY(batch_id, ordinal),
        UNIQUE(volume, start_byte, end_byte)
    )""",
)


class CaptureStore:
    def __init__(self, path):
        self.path = path

    def initialize(self, *, create=False):
        if create:
            with write_transaction(self.path, create=True) as db:
                for statement in _SCHEMA:
                    db.execute(statement)
                db.execute("INSERT OR IGNORE INTO checkpoint VALUES (1, 1, 0)")
        with self.connect_read() as db:
            row = db.execute("SELECT volume, offset FROM checkpoint WHERE singleton = 1").fetchone()
            if row is None:
                raise ValueError("missing capture checkpoint")
            db.execute("SELECT batch_id, start_volume, start_byte, end_volume, end_byte "
                       "FROM receipt_batches LIMIT 0")
            db.execute("SELECT batch_id, ordinal, value, volume, start_byte, end_byte "
                       "FROM receipt_lines LIMIT 0")

    def transaction(self):
        return write_transaction(self.path)

    def connect_read(self):
        return connect_database(self.path)
