from station.capture.receipts import ReceiptLine
from station.recorder.format import parse_gust_line
from station.source.positions import ByteSpan


class LineAssembler:
    def __init__(self, start):
        self._start = start
        self._pending = bytearray()
        self.discard()

    @property
    def has_partial(self):
        return bool(self._pending)

    def feed(self, chunk):
        span = chunk.span
        if (span.volume != self._start.volume or span.start != self._next
                or span.length != len(chunk.data)):
            raise ValueError("line fragments must be contiguous bytes from one volume")

        lines = []
        offset = 0
        while offset < len(chunk.data):
            newline = chunk.data.find(b"\n", offset)
            if newline < 0:
                self._pending.extend(chunk.data[offset:])
                break
            offset_after_lf = newline + 1
            self._pending.extend(chunk.data[offset:offset_after_lf])
            end = span.start + offset_after_lf
            value = parse_gust_line(bytes(self._pending))
            lines.append(ReceiptLine(value, ByteSpan(span.volume, self._line_start, end)))
            self._pending.clear()
            self._line_start = end
            offset = offset_after_lf
        self._next = span.end
        return lines

    def discard(self):
        self._pending.clear()
        self._line_start = self._start.offset
        self._next = self._start.offset
