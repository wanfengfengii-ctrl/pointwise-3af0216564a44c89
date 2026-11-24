from station.rotation.drafts import create_draft, discard_draft, write_prefix


class PrecopyFailed(OSError):
    pass


def prepare_rotation(gate, workfile, catalog, directory, *, block_size=65536,
                     after_prefix_block=None):
    if type(block_size) is not int or block_size < 1:
        raise ValueError("precopy block size must be a positive integer")
    with gate.hold():
        volume = catalog.active().volume
        prefix_end = workfile.size()

    def blocks():
        for start in range(0, prefix_end, block_size):
            end = min(start + block_size, prefix_end)
            with gate.hold():
                if catalog.active().volume != volume:
                    raise PrecopyFailed("active volume changed during precopy")
                data = workfile.read_bytes(start, end)
            yield data

    draft = None
    try:
        draft = create_draft(directory, volume, prefix_end)
        write_prefix(draft, blocks(), after_block=after_prefix_block)
        return draft
    except BaseException as error:
        if draft is not None:
            discard_draft(draft)
        if isinstance(error, OSError) and not isinstance(error, PrecopyFailed):
            raise PrecopyFailed(f"could not copy volume {volume}: {error}") from error
        raise
