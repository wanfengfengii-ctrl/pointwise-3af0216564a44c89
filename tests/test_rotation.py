from concurrent.futures import ThreadPoolExecutor

from station import ByteSpan, InvalidByteRange, PrecopyFailed, UnknownVolume
from tests.fixtures import Pause, StationCase


class RotationTests(StationCase):
    def test_v3_final_extent_and_in_place(self):
        self.station.append(b"7\n")
        stat = self.station.workfile.path.stat()
        with ThreadPoolExecutor(max_workers=1) as workers, Pause() as prepared:
            pending = workers.submit(self.station.rotate, after_prepare=prepared)
            draft = prepared.wait()
            self.assertEqual((draft.volume, draft.prefix_end, draft.path.read_bytes()),
                             (1, 2, b"7\n"))
            self.assertEqual(self.station.append(b"12\n"), ByteSpan(1, 2, 5))
            self.assertEqual(self.station.read(1, 0, 5), b"7\n12\n")
            with self.assertRaises(UnknownVolume):
                self.station.read(2, 0, 0)
            prepared.release()
            cut = pending.result(timeout=15)
        self.assertEqual((cut.sealed.volume, cut.sealed.end, cut.active.volume), (1, 5, 2),
                         f"ROTATION_ARCHIVE_EXTENT sealed={cut.sealed!r}")
        self.assertEqual(self.station.catalog.lookup(1), cut.sealed)
        self.assertEqual(self.station.read(1, 0, 5), b"7\n12\n")
        self.assertEqual(self.station.read(2, 0, 0), b"")
        with self.assertRaises(InvalidByteRange):
            self.station.read(2, 0, 1)
        stat_after = self.station.workfile.path.stat()
        self.assertEqual((stat_after.st_dev, stat_after.st_ino), (stat.st_dev, stat.st_ino))
        self.assertEqual(self.station.append(b"7\n"), ByteSpan(2, 0, 2))
        self.assertEqual(self.station.read(1, 2, 5), b"12\n")

    def test_v4_failed_draft_keeps_active_volume(self):
        self.station.append(b"7\n")
        observed = []

        def stop_after_partial_write(draft, copied):
            observed.append((copied, draft.path.read_bytes()))
            raise OSError("fixture interrupted precopy after its first write")

        with self.assertRaises(PrecopyFailed):
            self.station.rotate(block_size=1, after_prefix_block=stop_after_partial_write)
        self.assertEqual(observed, [(1, b"7")])
        self.assertTrue(self.station.catalog.active().is_active)
        self.assertEqual(self.station.catalog.active().volume, 1)
        self.assertIsNone(self.station.catalog.lookup(2))
        self.assertEqual(list((self.root / "drafts").iterdir()), [])
        self.assertEqual(list((self.root / "sealed").iterdir()), [])
        self.assertEqual(self.station.append(b"12\n"), ByteSpan(1, 2, 5))
        self.assertEqual(self.station.read(1, 0, 5), b"7\n12\n")
        self.assertEqual(self.station.rotate().sealed.end, 5)
        self.assertEqual(self.station.read(1, 0, 5), b"7\n12\n")
