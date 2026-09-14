import contextlib
import errno
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from directory_reconcile import reconcile_directories
from rename_comics import plan_new_name_and_title


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, data=b'metadata'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def run_reconcile(self, dry=False, targets=(), hints=None):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            errors = reconcile_directories(str(self.root), plan_new_name_and_title,
                                           targets, hints or {}, set(), dry_run=dry)
        self.assertEqual(errors, [], output.getvalue())
        return output.getvalue()

    def snapshot(self):
        return {str(p.relative_to(self.root)): ('link', str(p.readlink())) if p.is_symlink()
                else p.read_bytes() if p.is_file() else None for p in self.root.rglob('*')}

    def test_title_independent_matching_with_punctuation_and_nested_files(self):
        cases = [
            ('Young.Hellboy-Thrilling.Sky.Adventures.01.[of.04].[2026].[digital].[Son.of.Ultron-Empire]', 'Young Hellboy - Thrilling Sky Adventures (2026)'),
            ('Something.Completely.Different.12.[of.20].[2001].[scan]', 'Something Completely Different (2001)'),
            ('Saga Vol.2012', 'Saga (2012)'),
            ('arbitrary series #007 (1999)', 'Arbitrary Series (1999)'),
        ]
        for old, new in cases:
            self.write(f'{new}/existing.cbz', b'archive')
            self.write(f'{old}/nested/deeper/.metadata.xml', old.encode())
        before = self.snapshot()
        preview = self.run_reconcile(dry=True)
        self.assertEqual(self.snapshot(), before)
        actual = self.run_reconcile()
        self.assertEqual(preview, actual)
        for old, new in cases:
            self.assertFalse((self.root / old).exists())
            self.assertEqual((self.root / new / 'nested/deeper/.metadata.xml').read_bytes(), old.encode())
            self.assertEqual((self.root / new / 'existing.cbz').read_bytes(), b'archive')

    def test_identical_files_removed_differing_files_quarantined(self):
        self.write('Saga (2012)/same.xml', b'same')
        self.write('Saga (2012)/cover.jpg', b'correct cover')
        old = 'Saga.01.[2012]'
        self.write(f'{old}/same.xml', b'same')
        self.write(f'{old}/cover.jpg', b'other cover')
        before = self.snapshot()
        preview = self.run_reconcile(dry=True)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(preview, self.run_reconcile())
        self.assertFalse((self.root / old).exists())
        self.assertEqual((self.root / 'Saga (2012)/cover.jpg').read_bytes(), b'correct cover')
        self.assertEqual((self.root / f'.quarantine/{old}/cover.jpg').read_bytes(), b'other cover')
        self.assertFalse((self.root / f'.quarantine/{old}/same.xml').exists())

    def test_unmatched_and_ambiguous_folders_go_to_quarantine(self):
        self.write('Unknown/.hidden/file.nfo')
        self.write('Saga/metadata.xml')
        self.write('Saga (2012)/one.cbz')
        self.write('Saga (2020)/two.cbz')
        before = self.snapshot()
        self.run_reconcile(dry=True)
        self.assertEqual(self.snapshot(), before)
        self.run_reconcile()
        self.assertFalse((self.root / 'Unknown').exists())
        self.assertFalse((self.root / 'Saga').exists())
        self.assertEqual((self.root / '.quarantine/Unknown/.hidden/file.nfo').read_bytes(), b'metadata')
        self.assertEqual((self.root / '.quarantine/Saga/metadata.xml').read_bytes(), b'metadata')
        quarantined = self.snapshot()
        self.run_reconcile()
        self.assertEqual(self.snapshot(), quarantined)

    def test_quarantine_collision_preserves_both_versions(self):
        self.write('.quarantine/Unknown/file.nfo', b'old')
        self.write('Unknown/file.nfo', b'new')
        self.run_reconcile()
        self.assertEqual((self.root / '.quarantine/Unknown/file.nfo').read_bytes(), b'old')
        self.assertEqual((self.root / '.quarantine/Unknown (1)/file.nfo').read_bytes(), b'new')

    def test_source_and_destination_symlinks_never_followed(self):
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside)
            (external / 'file').write_bytes(b'external')
            self.write('Saga (2012)/anchor.cbz')
            (self.root / 'Saga (2012)/nested').symlink_to(external, target_is_directory=True)
            self.write('Saga.01.[2012]/nested/file', b'incoming')
            (self.root / 'UnknownLink').symlink_to(external, target_is_directory=True)
            self.run_reconcile()
            self.assertEqual((external / 'file').read_bytes(), b'external')
            self.assertTrue((self.root / '.quarantine/UnknownLink').is_symlink())
            self.assertEqual((self.root / '.quarantine/Saga.01.[2012]/nested/file').read_bytes(), b'incoming')

    def test_quarantine_symlink_rejected_without_touching_source(self):
        with tempfile.TemporaryDirectory() as outside:
            (self.root / '.quarantine').symlink_to(outside, target_is_directory=True)
            original = self.write('Unknown/file')
            with contextlib.redirect_stdout(io.StringIO()):
                errors = reconcile_directories(str(self.root), plan_new_name_and_title, set(), {}, set())
            self.assertTrue(errors)
            self.assertTrue(original.exists())
            self.assertEqual(list(Path(outside).iterdir()), [])

    def test_multiple_sources_share_virtual_destination_in_dry_run(self):
        self.write('Saga (2012)/anchor.cbz')
        self.write('Saga.01.[2012]/metadata.xml', b'first')
        self.write('Saga.02.[2012]/metadata.xml', b'second')
        before = self.snapshot()
        preview = self.run_reconcile(dry=True)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(preview, self.run_reconcile())
        self.assertEqual((self.root / 'Saga (2012)/metadata.xml').read_bytes(), b'first')
        self.assertEqual((self.root / '.quarantine/Saga.02.[2012]/metadata.xml').read_bytes(), b'second')

    def test_cross_filesystem_file_move_uses_exclusive_copy(self):
        self.write('Saga (2012)/anchor.cbz')
        self.write('Saga.01.[2012]/metadata.xml', b'data')
        with patch('directory_reconcile.os.link', side_effect=OSError(errno.EXDEV, 'different filesystem')):
            self.run_reconcile()
        self.assertEqual((self.root / 'Saga (2012)/metadata.xml').read_bytes(), b'data')
        self.assertFalse((self.root / 'Saga.01.[2012]').exists())

    def test_authoritative_move_hint_handles_arbitrary_source_names(self):
        self.write('Saga (2012)/anchor.cbz')
        self.write('random release directory/nested/file')
        source = self.root / 'random release directory'
        target = self.root / 'Saga (2012)'
        self.run_reconcile(targets={str(target)}, hints={str(source): {str(target)}})
        self.assertFalse(source.exists())
        self.assertEqual((target / 'nested/file').read_bytes(), b'metadata')

    def test_outside_target_rejected_before_mutations(self):
        self.write('old/file')
        before = self.snapshot()
        outside = self.root / '..' / 'outside-library'
        with contextlib.redirect_stdout(io.StringIO()):
            errors = reconcile_directories(str(self.root), plan_new_name_and_title,
                                           {str(outside)}, {}, set())
        self.assertTrue(errors)
        self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
