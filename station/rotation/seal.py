from dataclasses import dataclass

from station.rotation.catalog import VolumeEntry
from station.rotation.drafts import discard_draft
from station.rotation.prepare import prepare_rotation


class StaleDraft(ValueError):
    pass


class CutRecoveryFailed(OSError):
    pass


@dataclass(frozen=True)
class CutResult:
    sealed: VolumeEntry
    active: VolumeEntry


def seal_rotation(gate, workfile, catalog, archives, draft):
    with gate.hold():
        if catalog.active().volume != draft.volume:
            raise StaleDraft("rotation draft no longer belongs to the active volume")
        final_end = workfile.size()
        tail = workfile.iter_bytes(draft.prefix_end, final_end)
        reference = archives.finalize(draft, tail, final_end)
        cleared = False
        try:
            with catalog.publish_cut(draft.volume, reference, final_end) as active:
                workfile.truncate_in_place()
                cleared = True
        except BaseException:
            archive_path = archives.path_for(reference)
            if cleared:
                try:
                    workfile.restore_from(archive_path)
                except OSError as error:
                    raise CutRecoveryFailed(f"old bytes retained in {reference}") from error
            archive_path.unlink()
            raise
        return CutResult(VolumeEntry(draft.volume, reference, final_end), active)


def rotate_workfile(gate, workfile, catalog, archives, draft_dir, *, block_size=65536,
                    after_prefix_block=None, after_prepare=None):
    draft = prepare_rotation(gate, workfile, catalog, draft_dir, block_size=block_size,
                             after_prefix_block=after_prefix_block)
    try:
        if after_prepare is not None:
            after_prepare(draft)
        return seal_rotation(gate, workfile, catalog, archives, draft)
    finally:
        discard_draft(draft)
