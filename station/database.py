from contextlib import closing, contextmanager
import sqlite3


@contextmanager
def connect_database(path, *, mode="ro"):
    uri = f"{path.resolve().as_uri()}?mode={mode}"
    with closing(sqlite3.connect(uri, uri=True, isolation_level=None)) as db:
        db.execute("PRAGMA foreign_keys = ON")
        yield db


@contextmanager
def write_transaction(path, *, create=False):
    mode = "rwc" if create else "rw"
    with connect_database(path, mode=mode) as db, db:
        db.execute("BEGIN IMMEDIATE")
        yield db


def valid_id(number):
    return type(number) is int and 1 <= number <= 2**63 - 1
