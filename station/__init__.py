from dataclasses import dataclass
from pathlib import Path
import sqlite3

from station.capture.checkpoints import load_checkpoint
from station.capture.poll import PollCommitFailed as PollCommitFailed
from station.capture.poll import poll_next_batch
from station.capture.receipts import get_batch
from station.capture.store import CaptureStore
from station.recorder.format import InvalidGustLine as InvalidGustLine
from station.recorder.gate import WorkfileGate
from station.recorder.ingest import append_observation
from station.recorder.workfile import WorkFile
from station.review.publish import InvalidParents as InvalidParents
from station.review.publish import issue_report
from station.review.readback import UnknownReport as UnknownReport
from station.review.readback import get_report, read_peak_evidence
from station.review.reports import ReportStore
from station.rotation.archive import ArchiveStore
from station.rotation.catalog import VolumeCatalog
from station.rotation.prepare import PrecopyFailed as PrecopyFailed
from station.rotation.seal import rotate_workfile
from station.source.chunks import SourceFiles
from station.source.open import UnknownVolume as UnknownVolume
from station.source.open import read_range
from station.source.positions import ByteSpan as ByteSpan
from station.source.positions import InvalidByteRange as InvalidByteRange
from station.source.positions import SourcePoint as SourcePoint


class StationStateError(ValueError):
    pass


def _check_work_name(name):
    if (not isinstance(name, str) or not name or name in {".", ".."}
            or Path(name).name != name or "\x00" in name):
        raise StationStateError("work_name must name one file under live")


@dataclass(frozen=True)
class Station:
    root: Path
    source: SourceFiles
    capture: CaptureStore
    reports: ReportStore

    @classmethod
    def initialize(cls, directory, work_name=None):
        if work_name is not None:
            _check_work_name(work_name)
        root = Path(directory).resolve()
        gate = WorkfileGate.for_station(root)
        catalog = VolumeCatalog(root / "volumes.sqlite3")
        capture = CaptureStore(root / "capture.sqlite3")
        reports = ReportStore(root / "reports.sqlite3")
        archives = ArchiveStore(root / "sealed")

        try:
            with gate.hold():
                root.mkdir(parents=True, exist_ok=True)
                create = not any(root.iterdir())
                for path in (root / "live", root / "drafts", archives.directory):
                    if create:
                        path.mkdir()
                    elif path.is_symlink() or not path.is_dir():
                        raise StationStateError(f"missing station directory {path.name}")
                if not create:
                    for db in (catalog, capture, reports):
                        if db.path.is_symlink() or not db.path.is_file():
                            raise StationStateError(f"missing station ledger {db.path.name}")
                if create:
                    name = catalog.initialize(work_name or "gust.log", create=True)
                else:
                    name = catalog.initialize(work_name)
                _check_work_name(name)
                workfile = WorkFile.initialize(root / "live" / name, create=create)
                capture.initialize(create=create)
                reports.initialize(create=create)
                volumes = catalog.entries()
                for entry in volumes[:-1]:
                    if archives.extent(entry.archive_ref) != entry.end:
                        raise StationStateError(f"incomplete sealed volume {entry.volume}")
                point = load_checkpoint(capture).point
                entry = catalog.lookup(point.volume)
                if entry is None:
                    raise StationStateError("checkpoint refers to an unknown volume")
                extent = workfile.size() if entry.is_active else entry.end
                if not 0 <= point.offset <= extent:
                    raise StationStateError("checkpoint is outside its source volume")
        except StationStateError:
            raise
        except (OSError, sqlite3.Error, ValueError) as error:
            raise StationStateError(f"cannot initialize station {root.name}: {error}") from error

        return cls(root, SourceFiles(gate, workfile, catalog, archives), capture, reports)

    @property
    def workfile(self):
        return self.source.workfile

    @property
    def catalog(self):
        return self.source.catalog

    def append(self, raw):
        return append_observation(self.source.gate, self.workfile, self.catalog, raw)

    def rotate(self, *, block_size=65536, after_prefix_block=None, after_prepare=None):
        return rotate_workfile(
            self.source.gate, self.workfile, self.catalog, self.source.archives,
            self.root / "drafts", block_size=block_size,
            after_prefix_block=after_prefix_block, after_prepare=after_prepare,
        )

    def read(self, volume, start, end):
        return read_range(self.source, ByteSpan(volume, start, end))

    def poll(self, *, chunk_size=4096, after_chunk=None):
        return poll_next_batch(self.source, self.capture, chunk_size=chunk_size,
                               after_chunk=after_chunk)

    def checkpoint(self):
        return load_checkpoint(self.capture).point

    def receipt(self, batch_id):
        return get_batch(self.capture, batch_id)

    def issue(self, parent_ids):
        return issue_report(self.capture, self.reports, parent_ids)

    def get(self, report_id):
        return get_report(self.reports, report_id)

    def peak_evidence(self, report_id):
        return read_peak_evidence(self.reports, self.source, report_id)
