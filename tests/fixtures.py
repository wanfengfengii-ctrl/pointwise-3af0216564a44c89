from contextlib import contextmanager
from tempfile import TemporaryDirectory
from threading import Event
import unittest

from station import Station
from station.database import connect_database


class StationCase(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory(prefix="gust-test-"))
        self.station = Station.initialize(directory, work_name="sensor.log")
        self.root = self.station.root


class Pause:
    def __init__(self):
        self.ready = Event()
        self.continue_event = Event()
        self.value = None

    def __enter__(self):
        return self

    def __exit__(self, *_exception):
        self.release()

    def __call__(self, value):
        self.value = value
        self.ready.set()
        if not self.continue_event.wait(15):
            raise TimeoutError("event fixture was not released")

    def wait(self):
        if not self.ready.wait(15):
            raise TimeoutError("business operation did not reach the event fixture")
        return self.value

    def release(self):
        self.continue_event.set()


def trace(batch):
    if batch is None:
        return ()
    return tuple((line.span.volume, line.span.start, line.span.end, line.value)
                 for line in batch.lines)


def capture_counts(station):
    with station.capture.connect_read() as db:
        return (db.execute("SELECT COUNT(*) FROM receipt_batches").fetchone()[0],
                db.execute("SELECT COUNT(*) FROM receipt_lines").fetchone()[0])


def report_count(station):
    with connect_database(station.reports.path) as db:
        return db.execute("SELECT COUNT(*) FROM reports").fetchone()[0]


@contextmanager
def refuse_receipt_line(station, volume, start):
    with station.capture.transaction() as db:
        db.execute(f"""CREATE TRIGGER fixture_refuse_receipt BEFORE INSERT ON receipt_lines
                      WHEN NEW.volume = {int(volume)} AND NEW.start_byte = {int(start)}
                      BEGIN SELECT RAISE(ABORT, 'fixture rejected receipt write'); END""")
    try:
        yield
    finally:
        with station.capture.transaction() as db:
            db.execute("DROP TRIGGER fixture_refuse_receipt")
