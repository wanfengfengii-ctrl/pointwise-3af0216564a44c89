from dataclasses import dataclass

from station.recorder.gate import WorkfileGate
from station.recorder.workfile import WorkFile
from station.rotation.archive import ArchiveStore
from station.rotation.catalog import VolumeCatalog
from station.source.positions import ByteSpan
from station.source.session import ReadChunk


class ReadBindingChanged(ValueError):
    pass


@dataclass(frozen=True)
class SourceFiles:
    gate: WorkfileGate
    workfile: WorkFile
    catalog: VolumeCatalog
    archives: ArchiveStore


def read_chunk(source, session, limit):
    if type(limit) is not int or limit < 1:
        raise ValueError("read chunk size must be a positive integer")
    if session.invalidated:
        raise ReadBindingChanged(f"read binding expired for volume {session.span.volume}")
    remaining = session.remaining_span()
    if remaining.length == 0:
        return None
    end = min(remaining.start + limit, remaining.end)
    if session.archive_ref is None:
        with source.gate.hold():
            if source.catalog.active().volume != remaining.volume:
                session.invalidate()
                raise ReadBindingChanged(f"work file moved past volume {remaining.volume}")
            data = source.workfile.read_bytes(remaining.start, end)
    else:
        data = source.archives.read_bytes(session.archive_ref, remaining.start, end)
    session.advance(end)
    return ReadChunk(ByteSpan(remaining.volume, remaining.start, end), data)
