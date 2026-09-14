import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import rename_comics as renamer


class FilesystemFailureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.root = self.parent / 'Comics'
        self.root.mkdir()
        for mock in (
            patch.object(renamer, 'EXTERNAL_COMICS_DIR', None),
            patch.object(renamer, 'load_configuration', return_value={}),
            patch.dict('os.environ', {'COMICVINE_API_KEY': ''}),
        ):
            mock.start()
            self.addCleanup(mock.stop)

    def write(self, path, contents=b'archive contents'):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        return path

    def run_cli(self, root=None, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            status = renamer.main([str(root or self.root), '--recursive', *args])
        return status, output.getvalue()

    def snapshot(self, root):
        return {str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
                for path in root.rglob('*')}

    def test_failed_rename_is_nonzero_and_does_not_claim_quarantine_success(self):
        source = self.write(self.root / 'incoming/Saga 029 (2012).cbz')
        with patch.object(renamer.os, 'rename', side_effect=PermissionError('rename denied')), \
                patch.object(renamer.shutil, 'move', side_effect=PermissionError('quarantine denied')):
            status, output = self.run_cli()

        self.assertEqual(source.read_bytes(), b'archive contents')
        self.assertFalse((self.root / 'Saga (2012)/Saga #029 (2012).cbz').exists())
        self.assertNotEqual(status, 0, output)
        self.assertIn('Moved to error: 0', output)
        self.assertIn('FAILED', output)
        self.assertIn('rename denied', output)

    def test_failed_rename_removes_only_new_empty_destination_directory(self):
        self.write(self.root / 'incoming/Saga 029 (2012).cbz')
        with patch.object(renamer.os, 'rename', side_effect=PermissionError('rename denied')), \
                patch.object(renamer.shutil, 'move', side_effect=PermissionError('quarantine denied')):
            self.run_cli()

        self.assertFalse((self.root / 'Saga (2012)').exists())
        self.assertTrue((self.root / 'incoming').is_dir())

    def test_failed_rename_leaves_original_in_place_even_when_quarantine_is_writable(self):
        source = self.write(self.root / 'incoming/Saga 029 (2012).cbz')
        with patch.object(renamer.os, 'rename', side_effect=PermissionError('rename denied')):
            status, output = self.run_cli()

        self.assertTrue(source.exists(), output)
        self.assertEqual(source.read_bytes(), b'archive contents')
        self.assertFalse((self.root / 'error/Saga 029 (2012).cbz').exists())
        self.assertNotEqual(status, 0, output)

    def test_failed_rename_preserves_existing_destination_metadata(self):
        source = self.write(self.root / 'incoming/Saga 029 (2012).cbz')
        metadata = self.write(self.root / 'Saga (2012)/metadata.opf', b'existing metadata')
        with patch.object(renamer.os, 'rename', side_effect=PermissionError('rename denied')), \
                patch.object(renamer.shutil, 'move', side_effect=PermissionError('quarantine denied')):
            self.run_cli()

        self.assertEqual(source.read_bytes(), b'archive contents')
        self.assertEqual(metadata.read_bytes(), b'existing metadata')

    def test_failed_unparseable_move_is_nonzero_and_does_not_claim_success(self):
        source = self.write(self.root / '(digital).cbz')
        with patch.object(renamer.shutil, 'move', side_effect=PermissionError('quarantine denied')):
            status, output = self.run_cli()

        self.assertEqual(source.read_bytes(), b'archive contents')
        self.assertFalse((self.root / 'error/(digital).cbz').exists())
        self.assertNotEqual(status, 0, output)
        self.assertIn('Moved to error: 0', output)
        self.assertIn('FAILED', output)
        self.assertIn('quarantine denied', output)

    def test_rename_is_reported_only_after_move_succeeds(self):
        source = self.write(self.root / 'incoming/Saga 029 (2012).cbz')
        output = io.StringIO()
        observations = []
        original_rename = renamer.os.rename

        def observed_rename(src, dest):
            observations.append(output.getvalue())
            return original_rename(src, dest)

        with contextlib.redirect_stdout(output), patch.object(renamer.os, 'rename', side_effect=observed_rename):
            status = renamer.main([str(self.root), '--recursive', '--verbose'])

        self.assertEqual(status, 0)
        self.assertFalse(source.exists())
        self.assertEqual(len(observations), 1)
        self.assertNotIn('RENAME    :', observations[0])
        self.assertIn('RENAME    :', output.getvalue())

    def test_comics_root_keeps_archive_moves_separate_from_lowercase_sibling(self):
        lowercase_root = self.parent / 'comics'
        metadata = self.write(lowercase_root / 'Saga (2012)/metadata.opf', b'lowercase metadata')
        source = self.write(self.root / 'Saga Vol.2012/Saga Vol.2012 #029.cbz')
        lowercase_before = self.snapshot(lowercase_root)

        status, output = self.run_cli(self.root)

        self.assertEqual(status, 0, output)
        self.assertFalse(source.exists())
        self.assertEqual((self.root / 'Saga (2012)/Saga #029 (2012).cbz').read_bytes(), b'archive contents')
        self.assertEqual(self.snapshot(lowercase_root), lowercase_before)
        self.assertEqual(metadata.read_bytes(), b'lowercase metadata')

    def test_lowercase_metadata_only_root_does_not_scan_uppercase_sibling(self):
        lowercase_root = self.parent / 'comics'
        self.write(lowercase_root / 'Saga (2012)/metadata.opf', b'lowercase metadata')
        self.write(self.root / 'Saga Vol.2012/Saga Vol.2012 #029.cbz')
        uppercase_before = self.snapshot(self.root)

        status, output = self.run_cli(lowercase_root, '--verbose')

        self.assertEqual(status, 0, output)
        self.assertIn('found 0 comic file(s)', output)
        self.assertEqual(self.snapshot(self.root), uppercase_before)
        self.assertEqual(list(lowercase_root.rglob('*.cbz')), [])

    def test_zorro_leftover_empty_release_folders_removed_on_repeat(self):
        for issue in ('01', '02'):
            self.write(self.root / f'Zorro (2026)/Zorro #{int(issue):03d} (2026).cbr')
            (self.root / f'Zorro.{issue}.[of.03].[2026].[digital].[Son.of.Ultron-Empire]').mkdir()
        before = self.snapshot(self.root)
        status, output = self.run_cli(None, '--dry-run', '--verbose')
        self.assertEqual(status, 0, output)
        self.assertEqual(self.snapshot(self.root), before)
        self.assertEqual(output.count('REMOVE DIR:'), 2)
        status, output = self.run_cli(None, '--verbose')
        self.assertEqual(status, 0, output)
        self.assertIn('Skipped: 2', output)
        self.assertFalse(list(self.root.glob('Zorro.*')))
        self.assertEqual(len(list((self.root / 'Zorro (2026)').glob('*.cbr'))), 2)

    def test_zorro_release_metadata_is_preserved_and_reported(self):
        self.write(self.root / 'Zorro (2026)/Zorro #001 (2026).cbr')
        metadata = self.write(self.root / 'Zorro.01.[of.03].[2026].[digital].[Son.of.Ultron-Empire]/ComicInfo.xml', b'metadata')
        status, output = self.run_cli(None, '--verbose')
        self.assertEqual(status, 0, output)
        self.assertIn('KEEP DIR', output)
        self.assertEqual(metadata.read_bytes(), b'metadata')

    def test_move_removes_only_empty_source_directory(self):
        source = self.write(self.root / 'Zorro.01.[of.03].[2026].[digital]/Zorro 001 (2026).cbr')
        unrelated = self.root / 'Unrelated empty folder'
        unrelated.mkdir()
        status, output = self.run_cli()
        self.assertEqual(status, 0, output)
        self.assertFalse(source.parent.exists())
        self.assertTrue(unrelated.is_dir())
        self.assertEqual((self.root / 'Zorro (2026)/Zorro #001 (2026).cbr').read_bytes(), b'archive contents')

    def test_nested_source_directories_removed_children_first(self):
        self.write(self.root / 'incoming/Zorro 001 (2026).cbr')
        self.write(self.root / 'incoming/nested/Zorro 002 (2026).cbr')
        before = self.snapshot(self.root)
        status, preview = self.run_cli(None, '--dry-run', '--verbose')
        self.assertEqual(status, 0, preview)
        self.assertEqual(self.snapshot(self.root), before)
        self.assertIn('REMOVE DIR: incoming/nested (empty)', preview)
        self.assertIn('REMOVE DIR: incoming (empty)', preview)
        status, output = self.run_cli(None, '--verbose')
        self.assertEqual(status, 0, output)
        self.assertFalse((self.root / 'incoming').exists())
        self.assertEqual(len(list((self.root / 'Zorro (2026)').glob('*.cbr'))), 2)


if __name__ == '__main__':
    unittest.main()
