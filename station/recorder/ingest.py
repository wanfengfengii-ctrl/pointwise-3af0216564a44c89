from station.recorder.format import parse_gust_line
from station.recorder.writer import append_validated_line


def append_observation(gate, workfile, catalog, raw):
    parse_gust_line(raw)
    return append_validated_line(gate, workfile, catalog, raw)
