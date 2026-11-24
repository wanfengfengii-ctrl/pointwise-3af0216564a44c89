from station.source.chunks import ReadBindingChanged, read_chunk
from station.source.positions import ByteSpan, validate_span
from station.source.session import ReadSession


class UnknownVolume(KeyError):
    pass


def open_read(source, volume, start=0, end=None):
    with source.gate.hold():
        entry = source.catalog.lookup(volume)
        if entry is None:
            raise UnknownVolume(volume)
        available = source.workfile.size() if entry.is_active else entry.end
        span = ByteSpan(volume, start, available if end is None else end)
        validate_span(span, available)
        return ReadSession(span, entry.archive_ref)


def read_range(source, span):
    with source.gate.hold():
        entry = source.catalog.lookup(span.volume)
        if entry is None:
            raise UnknownVolume(span.volume)
        available = source.workfile.size() if entry.is_active else entry.end
        validate_span(span, available)
        session = ReadSession(span, entry.archive_ref)
    while True:
        data = bytearray()
        try:
            while chunk := read_chunk(source, session, 65536):
                data.extend(chunk.data)
        except ReadBindingChanged:
            session = open_read(source, span.volume, span.start, span.end)
        else:
            return bytes(data)
