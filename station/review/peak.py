from dataclasses import dataclass

from station.source.positions import ByteSpan


@dataclass(frozen=True)
class PeakResult:
    value: int | None
    spans: tuple[ByteSpan, ...]


def find_peak(lines):
    peak = None
    spans = []
    for line in lines:
        if peak is None or line.value > peak:
            peak = line.value
            spans = [line.span]
        elif line.value == peak:
            spans.append(line.span)
    return PeakResult(peak, tuple(spans))
