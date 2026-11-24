from dataclasses import dataclass

from station.review.peak import find_peak
from station.source.positions import ByteSpan


@dataclass(frozen=True)
class ShiftSummary:
    sample_count: int
    peak_value: int | None
    peak_spans: tuple[ByteSpan, ...]


def summarize_shift(batches):
    ordered = tuple(batches)
    peak = find_peak(line for batch in ordered for line in batch.lines)
    return ShiftSummary(sum(len(batch.lines) for batch in ordered), peak.value, peak.spans)
