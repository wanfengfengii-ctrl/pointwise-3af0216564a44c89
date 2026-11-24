from dataclasses import dataclass

from station.source.open import read_range
from station.source.positions import ByteSpan


@dataclass(frozen=True)
class EvidenceBytes:
    span: ByteSpan
    data: bytes


def read_evidence(source, spans):
    return tuple(EvidenceBytes(span, read_range(source, span)) for span in spans)
