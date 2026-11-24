from station.source.positions import ByteSpan


def append_validated_line(gate, workfile, catalog, raw):
    with gate.hold():
        volume = catalog.active().volume
        start, end = workfile.append_bytes(raw)
        return ByteSpan(volume, start, end)
