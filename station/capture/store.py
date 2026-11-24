from station.database import connect_database, write_transaction


_SUBMISSIONS_SCHEMA = """CREATE TABLE IF NOT EXISTS submissions (
    report_key TEXT PRIMARY KEY,
    raw BLOB NOT NULL,
    volume INTEGER,
    start_byte INTEGER,
    end_byte INTEGER,
    complete INTEGER NOT NULL DEFAULT 0 CHECK(complete IN (0, 1)),
    CHECK((volume IS NULL AND start_byte IS NULL AND end_byte IS NULL) OR
          (volume IS NOT NULL AND start_byte IS NOT NULL AND end_byte IS NOT NULL
           AND end_byte > start_byte))
)"""

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
        report_key TEXT,
        PRIMARY KEY(batch_id, ordinal),
        UNIQUE(volume, start_byte, end_byte)
    )""",
)

_INDEXES = (
    """CREATE UNIQUE INDEX IF NOT EXISTS one_received_report_key
       ON receipt_lines(report_key) WHERE report_key IS NOT NULL""",
)


class CaptureStore:
    def __init__(self, path):
        self.path = path

    def initialize(self, *, create=False):
        with write_transaction(self.path, create=create) as db:
            if create:
                for statement in _SCHEMA:
                    db.execute(statement)
                db.execute("INSERT OR IGNORE INTO checkpoint VALUES (1, 1, 0)")
            db.execute(_SUBMISSIONS_SCHEMA)
            columns = {row[1] for row in db.execute("PRAGMA table_info(receipt_lines)")}
            if "report_key" not in columns:
                db.execute("ALTER TABLE receipt_lines ADD COLUMN report_key TEXT")
            for statement in _INDEXES:
                db.execute(statement)
        with self.connect_read() as db:
            row = db.execute("SELECT volume, offset FROM checkpoint WHERE singleton = 1").fetchone()
            if row is None:
                raise ValueError("missing capture checkpoint")
            db.execute("SELECT batch_id, start_volume, start_byte, end_volume, end_byte "
                       "FROM receipt_batches LIMIT 0")
            db.execute("SELECT batch_id, ordinal, value, volume, start_byte, end_byte, report_key "
                       "FROM receipt_lines LIMIT 0")
            db.execute("SELECT report_key, raw, volume, start_byte, end_byte, complete "
                       "FROM submissions LIMIT 0")

    def transaction(self):
        return write_transaction(self.path)

    def connect_read(self):
        return connect_database(self.path)
