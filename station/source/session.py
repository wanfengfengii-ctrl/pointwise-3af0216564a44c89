from dataclasses import dataclass, field

from station.source.positions import ByteSpan


@dataclass(frozen=True)
class ReadChunk:
    span: ByteSpan
    data: bytes


@dataclass
class ReadSession:
    span: ByteSpan
    archive_ref: str | None
    next_offset: int = field(init=False)
    invalidated: bool = field(default=False, init=False)

    def __post_init__(self):
        self.next_offset = self.span.start

    def remaining_span(self):
        return ByteSpan(self.span.volume, self.next_offset, self.span.end)

    def advance(self, end):
        if self.invalidated or not self.next_offset < end <= self.span.end:
            raise ValueError("cannot advance the read session to that byte")
        self.next_offset = end

    def invalidate(self):
        self.invalidated = True
