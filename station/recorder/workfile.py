from dataclasses import dataclass
import os
from pathlib import Path
from shutil import copyfileobj


@dataclass(frozen=True)
class WorkFile:
    path: Path

    @classmethod
    def initialize(cls, path, *, create=False):
        path = Path(path)
        if create:
            with path.open("xb"):
                pass
        if path.is_symlink() or not path.is_file():
            raise OSError(f"work file is missing or invalid: {path.name}")
        return cls(path)

    def size(self):
        return self.path.stat().st_size

    def read_bytes(self, start, end):
        with self.path.open("rb") as file:
            file.seek(start)
            data = file.read(end - start)
        if len(data) != end - start:
            raise OSError("working log ended before the confirmed extent")
        return data

    def iter_bytes(self, start, end, block_size=65536):
        if not 0 <= start <= end or block_size < 1:
            raise ValueError("invalid working log interval or block size")
        with self.path.open("rb") as file:
            file.seek(start)
            remaining = end - start
            while remaining:
                data = file.read(min(remaining, block_size))
                if not data:
                    raise OSError("working log ended before the cut")
                remaining -= len(data)
                yield data

    def append_bytes(self, data):
        with self.path.open("r+b", buffering=0) as file:
            start = file.seek(0, os.SEEK_END)
            try:
                if file.write(data) != len(data):
                    raise OSError("short append to working log")
            except BaseException:
                file.truncate(start)
                raise
        return start, start + len(data)

    def truncate_in_place(self):
        os.truncate(self.path, 0)

    def restore_from(self, archive_path):
        with archive_path.open("rb") as source, self.path.open("r+b") as target:
            copyfileobj(source, target)
            target.truncate()
