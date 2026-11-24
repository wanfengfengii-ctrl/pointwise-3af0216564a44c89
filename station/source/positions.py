from dataclasses import dataclass


class InvalidByteRange(ValueError):
    pass


@dataclass(frozen=True, order=True)
class SourcePoint:
    volume: int
    offset: int


@dataclass(frozen=True)
class ByteSpan:
    volume: int
    start: int
    end: int

    @property
    def length(self):
        return self.end - self.start


def validate_span(span, available):
    if (type(span.start) is not int or type(span.end) is not int
            or not 0 <= span.start <= span.end <= available):
        raise InvalidByteRange(f"invalid interval in volume {span.volume}, available [0,{available})")
