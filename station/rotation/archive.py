from pathlib import Path


class ArchiveStore:
    def __init__(self, directory):
        self.directory = Path(directory)

    def path_for(self, reference):
        if (not isinstance(reference, str) or not reference.startswith("volume-")
                or not reference.endswith(".log") or not reference[7:-4].isascii()
                or not reference[7:-4].isdigit()):
            raise ValueError("invalid archive reference")
        return self.directory / reference

    def extent(self, reference):
        path = self.path_for(reference)
        if path.is_symlink() or not path.is_file():
            raise OSError(f"missing sealed original {reference}")
        return path.stat().st_size

    def finalize(self, draft, tail, final_end):
        reference = f"volume-{draft.volume}.log"
        target = self.path_for(reference)
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"sealed original already exists for volume {draft.volume}")
        if draft.path.stat().st_size != draft.prefix_end or draft.prefix_end > final_end:
            raise OSError("rotation prefix cannot cover the cut")
        copied = draft.prefix_end
        with draft.path.open("ab") as file:
            for data in tail:
                if copied + len(data) > final_end:
                    raise OSError("archive tail exceeds the cut")
                if file.write(data) != len(data):
                    raise OSError("short write of archive tail")
                copied += len(data)
        if copied != final_end or draft.path.stat().st_size != final_end:
            raise OSError("archive does not cover the complete cut")
        draft.path.replace(target)
        return reference

    def read_bytes(self, reference, start, end):
        with self.path_for(reference).open("rb") as file:
            file.seek(start)
            data = file.read(end - start)
        if len(data) != end - start:
            raise OSError("sealed original ended before the confirmed extent")
        return data
