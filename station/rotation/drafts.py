from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile


@dataclass(frozen=True)
class RotationDraft:
    volume: int
    prefix_end: int
    path: Path


def create_draft(directory, volume, prefix_end):
    with NamedTemporaryFile(dir=directory, prefix="prefix-", delete=False) as file:
        return RotationDraft(volume, prefix_end, Path(file.name))


def write_prefix(draft, blocks, *, after_block=None):
    copied = 0
    with draft.path.open("wb", buffering=0) as file:
        for data in blocks:
            if copied + len(data) > draft.prefix_end:
                raise OSError("precopy exceeded the captured prefix")
            if file.write(data) != len(data):
                raise OSError("short write of rotation prefix")
            copied += len(data)
            if after_block is not None:
                after_block(draft, copied)
    if copied != draft.prefix_end:
        raise OSError("precopy ended before the captured prefix")


def discard_draft(draft):
    draft.path.unlink(missing_ok=True)
