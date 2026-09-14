import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import rename_comics as renamer


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.external = patch.object(renamer, 'EXTERNAL_COMICS_DIR', None)
        self.external.start()
        self.addCleanup(self.external.stop)
        settings = patch.object(renamer, 'load_configuration', return_value={})
        settings.start()
        self.addCleanup(settings.stop)
        environment = patch.dict('os.environ', {'COMICVINE_API_KEY': ''})
        environment.start()
        self.addCleanup(environment.stop)

    def comic(self, relative):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'comic contents')
        return path

    def run_cli(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(renamer.main([str(self.root), *args]), 0)
        return output.getvalue()

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes() if p.is_file() else None
                for p in self.root.rglob('*')}

    def test_default_explains_nested_only_scan(self):
        self.comic('Series/Batman 001 (2025).cbz')
        before = self.snapshot()
        output = self.run_cli('--dry-run', '--verbose')
        self.assertIn('Renamed: 0  Skipped: 0', output)
        self.assertIn('Use --recursive', output)
        self.assertEqual(self.snapshot(), before)

    def test_recursive_dry_run_discovers_deep_and_uppercase_comics_without_writes(self):
        self.comic('Publisher/Series/Batman 001 (2025).CBZ')
        self.comic('Saga 002 (2025).cbr')
        before = self.snapshot()
        output = self.run_cli('--recursive', '--dry-run', '--verbose')
        self.assertIn('Renamed: 2', output)
        self.assertIn('Publisher/Series/Batman 001 (2025).CBZ', output)
        self.assertEqual(self.snapshot(), before)

    def test_recursive_move_and_repeat_preserve_contents(self):
        source = self.comic('Publisher/Series/Batman 001 (2025).cbz')
        output = self.run_cli('-r', '-v')
        destination = self.root / 'Batman (2025)/Batman #001 (2025).cbz'
        self.assertIn('Renamed: 1', output)
        self.assertFalse(source.exists())
        self.assertEqual(destination.read_bytes(), b'comic contents')
        self.assertIn('Renamed: 0  Skipped: 1', self.run_cli('-r'))

    def test_recursive_excludes_quarantine_hidden_and_directory_symlinks(self):
        for folder in ('error', 'possibleDuplicates', '.hidden', 'nested/error'):
            self.comic(folder + '/Batman 001 (2025).cbz')
        self.comic('nested/.hidden.cbz')
        self.comic('nested/notes.txt')
        with tempfile.TemporaryDirectory() as outside:
            (Path(outside) / 'Batman 001 (2025).cbz').write_bytes(b'external')
            (self.root / 'linked').symlink_to(outside, target_is_directory=True)
            output = self.run_cli('-r', '--dry-run', '-v')
        self.assertIn('found 0 comic file(s)', output)

    def test_default_still_processes_top_level_only(self):
        self.comic('Batman 001 (2025).cbz')
        self.comic('nested/Saga 001 (2025).cbz')
        self.assertIn('Renamed: 1', self.run_cli('--dry-run', '-v'))

    def test_existing_destination_is_not_overwritten(self):
        self.comic('incoming/Batman 001 (2025).cbz')
        original = self.comic('Batman (2025)/Batman #001 (2025).cbz')
        original.write_bytes(b'existing')
        self.run_cli('-r')
        self.assertEqual(original.read_bytes(), b'existing')
        self.assertEqual(len(list((self.root / 'Batman (2025)').glob('*.cbz'))), 2)

    def test_volume_year_labels(self):
        cases = [
            ('Batman Vol.2012', 'Batman (2012)', 'Batman (2012)'),
            ('Batman Vol.2012 #001', 'Batman (2012)', 'Batman #001 (2012)'),
            ('Batman VOL. 2012 001 (of 04) (digital)', 'Batman (2012)', 'Batman #001 (2012)'),
            ('Batman Vol.2012 #001 (2012)', 'Batman (2012)', 'Batman #001 (2012)'),
            ('Batman Vol.2012 #001 (2013)', 'Batman (2013)', 'Batman #001 (2013)'),
            ('Batman #001 Vol.2012', 'Batman (2012)', 'Batman #001 (2012)'),
        ]
        for stem, folder, filename in cases:
            with self.subTest(stem=stem):
                self.assertEqual(renamer.plan_new_name_and_title(stem), (folder, filename))

    def test_filename_and_matching_directory_precede_api(self):
        self.comic('Saga (2012)/Saga 029.cbz')
        self.comic('Saga Vol.2012/Saga 030.cbz')
        self.comic('Saga 2012/Saga 031.cbz')
        self.comic('Saga (2012)/Saga 032 (2014).cbz')
        client = Mock()
        with patch.object(renamer, 'ComicVine', return_value=client), patch.object(
                renamer, 'load_configuration', return_value={'COMICVINE_API_KEY': 'test'}):
            output = self.run_cli('-r', '--dry-run')
        client.lookup_year.assert_not_called()
        for issue in ('029', '030', '031'):
            self.assertIn(f'Saga (2012)/Saga #{issue} (2012).cbz', output)
        self.assertIn('Saga (2014)/Saga #032 (2014).cbz', output)

    def test_api_year_applied_to_filename_folder_and_dry_run(self):
        source = self.comic('Incoming (2020)/Saga 029.cbz')
        client = Mock()
        client.lookup_year.return_value = '2012'
        before = self.snapshot()
        with patch.object(renamer, 'ComicVine', return_value=client), patch.object(
                renamer, 'load_configuration', return_value={'COMICVINE_API_KEY': 'test'}):
            output = self.run_cli('-r', '--dry-run')
            self.assertIn('Saga (2012)/Saga #029 (2012).cbz', output)
            self.assertEqual(self.snapshot(), before)
            self.run_cli('-r')
        client.lookup_year.assert_called_with('Saga')
        self.assertFalse(source.exists())
        self.assertEqual((self.root / 'Saga (2012)/Saga #029 (2012).cbz').read_bytes(), b'comic contents')

    def test_yearless_volume_lookup_and_repeat(self):
        self.comic('incoming/Saga v02.cbz')
        client = Mock()
        client.lookup_year.return_value = '2012'
        with patch.object(renamer, 'ComicVine', return_value=client), patch.object(
                renamer, 'load_configuration', return_value={'COMICVINE_API_KEY': 'test'}):
            self.run_cli('-r')
            self.assertTrue((self.root / 'Saga (2012)/Saga Vol. 2 (2012).cbz').exists())
            self.assertIn('Renamed: 0  Skipped: 1', self.run_cli('-r'))
        client.lookup_year.assert_called_once_with('Saga')

    def test_unresolved_api_match_leaves_file_unchanged(self):
        source = self.comic('incoming/Saga 029.cbz')
        client = Mock()
        client.lookup_year.return_value = None
        with patch.object(renamer, 'ComicVine', return_value=client), patch.object(
                renamer, 'load_configuration', return_value={'COMICVINE_API_KEY': 'test'}):
            output = self.run_cli('-r')
        self.assertIn('Renamed: 0  Skipped: 1', output)
        self.assertIn('series year unresolved', output)
        self.assertEqual(source.read_bytes(), b'comic contents')

    def test_offline_option_avoids_client_and_preserves_yearless_behavior(self):
        self.comic('Saga 029.cbz')
        with patch.object(renamer, 'ComicVine') as client, patch.object(
                renamer, 'load_configuration', return_value={'COMICVINE_API_KEY': 'test'}):
            output = self.run_cli('--no-comicvine', '--dry-run')
        client.assert_not_called()
        self.assertIn('Saga/Saga #029.cbz', output)

    def test_volume_year_repair_on_disk(self):
        source = self.comic('Batman Vol.2012/Batman Vol.2012 #001.cbz')
        before = self.snapshot()
        self.assertIn('Batman (2012)/Batman #001 (2012).cbz', self.run_cli('-r', '--dry-run'))
        self.assertEqual(self.snapshot(), before)
        self.run_cli('-r')
        self.assertFalse(source.exists())
        self.assertEqual((self.root / 'Batman (2012)/Batman #001 (2012).cbz').read_bytes(), b'comic contents')
        self.assertIn('Renamed: 0  Skipped: 1', self.run_cli('-r'))

    def test_year_folders_for_supported_forms(self):
        cases = [
            ('Batman 001 (2025)', 'Batman (2025)', 'Batman #001 (2025)'),
            ('Batman 2025 Annual 001 (2025)', 'Batman (2025)', 'Batman 2025 Annual #001 (2025)'),
            ('Saga v02 (2012)', 'Saga (2012)', 'Saga Vol. 2 (2012)'),
            ('Watchmen (1987)', 'Watchmen (1987)', 'Watchmen (1987)'),
            ('Batman 001', 'Batman', 'Batman #001'),
        ]
        for stem, folder, filename in cases:
            with self.subTest(stem=stem):
                self.assertEqual(renamer.plan_new_name_and_title(stem), (folder, filename))

    def test_normalized_files_move_into_separate_year_folders(self):
        old = self.comic('Batman/Batman #001 (2025).cbz')
        loose = self.comic('Batman #001 (2016).cbz')
        before = self.snapshot()
        output = self.run_cli('-r', '--dry-run')
        self.assertIn('Renamed: 2', output)
        self.assertIn('Batman (2025)/Batman #001 (2025).cbz', output)
        self.assertEqual(self.snapshot(), before)
        self.run_cli('-r')
        self.assertFalse(old.exists())
        self.assertFalse(loose.exists())
        for year in (2016, 2025):
            self.assertEqual((self.root / f'Batman ({year})/Batman #001 ({year}).cbz').read_bytes(), b'comic contents')
        self.assertIn('Renamed: 0  Skipped: 2', self.run_cli('-r'))

    def test_duplicates_use_year_folder_and_leave_other_years(self):
        self.comic('incoming/Batman 001 (2025).cbz')
        self.comic('incoming/Batman 001 (2016).cbz')
        with tempfile.TemporaryDirectory() as external:
            folder = Path(external) / 'batman (2025)'
            folder.mkdir()
            (folder / 'Batman #001 (2025).cbr').write_bytes(b'external')
            with patch.object(renamer, 'EXTERNAL_COMICS_DIR', external):
                before = self.snapshot()
                self.assertIn('Possible duplicates: 1', self.run_cli('-r', '--dry-run'))
                self.assertEqual(self.snapshot(), before)
                self.assertIn('Possible duplicates: 1', self.run_cli('-r'))
        self.assertTrue((self.root / 'possibleDuplicates/Batman (2025)/Batman #001 (2025).cbz').exists())
        self.assertTrue((self.root / 'Batman (2016)/Batman #001 (2016).cbz').exists())


class ConfigurationTests(unittest.TestCase):
    def test_script_env_works_from_different_directory_and_cwd_overrides(self):
        import os
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'script'
            script.mkdir()
            cwd = root / 'comics'
            cwd.mkdir()
            (script / '.env').write_text('COMICVINE_API_KEY=script-key\n')
            previous = os.getcwd()
            try:
                os.chdir(cwd)
                with patch.object(renamer, '__file__', str(script / 'rename_comics.py')):
                    self.assertEqual(renamer.load_configuration()['COMICVINE_API_KEY'], 'script-key')
                    (cwd / '.env').write_text('COMICVINE_API_KEY=cwd-key\n')
                    self.assertEqual(renamer.load_configuration()['COMICVINE_API_KEY'], 'cwd-key')
            finally:
                os.chdir(previous)


if __name__ == '__main__':
    unittest.main()
