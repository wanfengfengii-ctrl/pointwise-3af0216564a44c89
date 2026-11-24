from decimal import Decimal


class InvalidGustLine(ValueError):
    pass


def encode_gust_value(value: int) -> str:
    return format(Decimal(value), "f")


def decode_gust_value(value: str) -> int:
    return int(Decimal(value))


def parse_gust_line(raw: bytes) -> int:
    if not isinstance(raw, bytes) or not raw.endswith(b"\n") or not raw[:-1].isdigit():
        raise InvalidGustLine("expected ASCII digits followed by one LF")
    return decode_gust_value(raw[:-1].decode("ascii"))
