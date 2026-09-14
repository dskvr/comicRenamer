# Comic Renamer

A Python script to automatically rename and organize comic book files (`.cbz` and `.cbr`) into a consistent, normalized format. The script intelligently parses various filename formats and organizes comics into folders by title and year, with built-in duplicate detection against an external comics directory.

## Features

- **Smart filename parsing** - Handles multiple filename formats:

  - Issues: `Title #001 (2025)`, `Title 001 (2019)`, `Title #1 (2019)`, etc.
  - Volumes: `Title v02 (2012)`, `Title Vol. 2 (2012)`, etc.
  - Standalone comics: `Title (2025)`, `Title - Subtitle (2024)`, etc.
  - Files without years or issue numbers

- **Automatic organization** - Groups comics into folders by title and year

- **Duplicate detection** - Checks against an external comics directory (ignores file extensions)

- **Case-insensitive matching** - Works regardless of filename capitalization

- **Dry-run mode** - Preview changes before applying them

- **Missing years** - Reuse a matching series directory's year, then optionally
  look up the series' first publication year on Comic Vine

- **Error handling** - Moves unparseable files to an `error/` directory

- **Summary reports** - Shows detailed statistics and lists of errors/duplicates

## Requirements

- Python 3.6 or higher
- No external dependencies (uses only standard library)

## Installation

1. Clone this repository:

   ```bash
   git clone https://github.com/jungleberrydev/comicRenamer.git
   cd comicRenamer
   ```

2. (Optional) Set up the external comics directory for duplicate detection:

   **Option 1: Using a `.env` file (Recommended)**

   Copy the example file and edit it:

   ```bash
   cp .env.example .env
   # Then edit .env and set your path
   ```

   The `.env` file is already in `.gitignore`, so your personal path won't be committed.

   **Option 2: Using environment variable**

   ```bash
   export COMIC_SORTER_EXTERNAL_DIR="/path/to/your/external/comics"
   ```

   To make this permanent, add it to your shell profile (e.g., `~/.zshrc` or `~/.bashrc`):

   ```bash
   echo 'export COMIC_SORTER_EXTERNAL_DIR="/path/to/your/external/comics"' >> ~/.zshrc
   ```

The script checks `.env` in the working directory, then `.env` beside the script,
then falls back to the environment variable.

## Usage

### Basic Usage

```bash
python3 rename_comics.py [directory]
```

If no directory is specified, it defaults to the current working directory.
By default, only files directly inside that directory are processed. To include
comics inside subdirectories, add `--recursive` (`-r`):

```bash
python3 rename_comics.py /mnt/user/media/Comics --recursive --dry-run --verbose
```

Files are organized into `Title (Year)/` folders under the selected directory,
using the year parsed from each filename. Different years get separate folders;
files without a parsed year use `Title/`. Already-normalized filenames are moved
if they are in the wrong folder. Use `--recursive` to reorganize existing title
folders. Recursive runs also reconcile leftover directories, including metadata
and nested content, regardless of the comic title. A source is matched using its
archive destinations or an unambiguous series title/year match, allowing dotted
release names and punctuation/spacing variants of already-renamed folders.

Missing destination files are moved recursively. Same-name files are compared
byte-for-byte: identical source copies are removed; differing source files go
to `.quarantine/` without overwriting the destination. Old directories are
removed only after their contents have been handled. Unmatched or ambiguous
directories go to `.quarantine/`, with numbered suffixes if needed to preserve
earlier quarantined content. Hidden content inside old folders is included;
symlinks are quarantined as links, never followed during reconciliation.

The library root, canonical series folders, and managed `error/`,
`possibleDuplicates/`, and `.quarantine/` directories are not treated as old
release folders. Failed archive operations preserve their source directories
for inspection and return a nonzero status. `OK` describes an archive's name
and location; subsequent `MOVE FILE`, `DUPLICATE`, `QUARANTINE`, and `REMOVE DIR`
messages describe directory reconciliation. Dry runs preview these actions
without writing or deleting anything.

### Options

- `--dry-run` - Preview changes without modifying files
- `--verbose` or `-v` - Show detailed output for each file processed
- `--recursive` or `-r` - Include comics in subdirectories
- `--comicvine` - Enable missing-year lookups using `COMICVINE_API_KEY`
- `--no-comicvine` - Keep Comic Vine requests disabled (the default)

### Examples

```bash
# Process current directory
python3 rename_comics.py

# Process a specific directory
python3 rename_comics.py /path/to/comics

# Preview changes first (recommended)
python3 rename_comics.py --dry-run

# Verbose output
python3 rename_comics.py --verbose
```

## Filename Format Support

The script recognizes and normalizes various filename patterns:

### Issues

- `Batman #001 (2025).cbz` → `Batman (2025)/Batman #001 (2025).cbz`
- `Batman 001 (2019).cbr` → `Batman (2019)/Batman #001 (2019).cbr`
- `Spider-Man #1 (2020).cbz` → `Spider-Man (2020)/Spider-Man #001 (2020).cbz`
- `Title 02 (of 04) (2025).cbz` → `Title (2025)/Title #002 (2025).cbz`

### Volumes

- `Watchmen v02 (2012).cbr` → `Watchmen (2012)/Watchmen Vol. 2 (2012).cbr`
- `Saga Vol. 1 (2012).cbz` → `Saga (2012)/Saga Vol. 1 (2012).cbz`

Four-digit year labels such as `Vol.2012` and `Vol. 2012` are treated as
years, not volume numbers:

- `Batman Vol.2012 #001.cbz` → `Batman (2012)/Batman #001 (2012).cbz`
- `Batman Vol.2012.cbz` → `Batman (2012)/Batman (2012).cbz`

If an explicit `(year)` is also present, it takes precedence. Run with
`--recursive` to repair filenames and reconcile leftover directories. Missing
metadata moves into the matching series folder; conflicts are quarantined.

### Standalone

- `Batman Annual (2025).cbz` → `Batman Annual (2025)/Batman Annual (2025).cbz`
- `Special Edition (2024).cbr` → `Special Edition (2024)/Special Edition (2024).cbr`

## Output Organization

The script organizes files as follows:

```
directory/
├── Title Name (2025)/
│   ├── Title Name #001 (2025).cbz
│   └── Title Name #002 (2025).cbz
├── Title Name (2020)/
│   └── Title Name Vol. 1 (2020).cbz
├── error/
│   └── (unparseable files)
└── possibleDuplicates/
    └── (folders containing duplicates)
```

## Duplicate Detection

The script checks for duplicates by:

1. Comparing filenames (without extensions) against the external comics directory
2. Matching the `Title (Year)` folder (or `Title` without a year) and normalized filename stem
3. Case-insensitive comparison
4. Moving entire title folders to `possibleDuplicates/` if any duplicates are found

**Note:** Duplicate detection is optional. If the `COMIC_SORTER_EXTERNAL_DIR` environment variable is not set, the script will automatically skip duplicate checking and work normally.

## Configuration

### Comic Vine Missing-Year Lookup (Optional)

Add your [Comic Vine API key](https://comicvine.gamespot.com/api/) to `.env`
beside `rename_comics.py`:

```dotenv
COMICVINE_API_KEY="your-api-key"
```

Lookup is opt-in: a key alone does not enable network requests. Add `--comicvine`
to enable it, including from a different working directory:

```bash
python3 /mnt/cache/scripts/comicRenamer/rename_comics.py \
  /mnt/user/media/Comics --recursive --comicvine --dry-run --verbose
```

Year precedence is:

1. A parsed filename year, including `Vol.2012` labels.
2. The nearest matching series directory, such as `Saga (2012)`, `Saga Vol.2012`,
   or `Saga 2012`, up to and including the selected directory. Unrelated folder
   names do not supply years.
3. Comic Vine's series/volume `start_year` (the series' first publication year,
   not the individual issue's year).

For example, `Saga #029.cbz` can become `Saga (2012)/Saga #029 (2012).cbz`.
Existing filename years are never replaced with an API result. Issues, volumes,
and standalone titles can receive a missing year.

Lookup requires exactly one matching Comic Vine volume name, ignoring case and
whitespace. Multiple editions, missing years, incomplete searches, and no matches
do not rename the archive and report `SKIP ... (series year unresolved)`. In a
recursive run, an unmatched source folder is subsequently moved to `.quarantine/`
with its contents intact. Network,
authentication, or API failures disable further requests for that run, while
files with local years continue processing. Without `--comicvine`, files without
years retain the normal title-only behavior, even when a key is configured.
`--comicvine` without a key fails before processing or moving any files.

Results, including misses, are cached in memory for the run. Requests are spaced
at least 18.1 seconds apart to respect Comic Vine's published 200 requests per
resource per hour; large collections can take time. Searches inspect up to five
pages and never choose a match from incomplete results. Dry runs still make API
requests but do not write files or caches. Requests send series titles to Comic
Vine; the key is never included in diagnostic output.

Keep `rename_comics.py`, `comicvine.py`, and `directory_reconcile.py` together
when copying the script.

### External Comics Directory (Optional)

Duplicate detection is **completely optional**. If you don't want to check for duplicates, you can simply leave the configuration unset. The script will work normally and skip duplicate checking.

**Preferred Method: `.env` file**

1. Copy the example file:

   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and set your external comics directory:
   ```bash
   COMIC_SORTER_EXTERNAL_DIR="/Volumes/External Drive/Comics"
   ```

The `.env` file is automatically ignored by git, so your personal path won't be committed to the repository.

**Alternative: Environment Variable**

You can also set it as an environment variable:

```bash
export COMIC_SORTER_EXTERNAL_DIR="/Volumes/External Drive/Comics"
```

**Priority**: The script checks in this order:

1. `.env` file (if it exists)
2. `.env` beside the script, for settings absent from the working-directory file
3. Environment variable `COMIC_SORTER_EXTERNAL_DIR`
4. If none is set, duplicate checking is skipped

This directory is used for duplicate detection. The script will check if files with the same title and issue number already exist there (ignoring file extensions like `.cbz` vs `.cbr`). If the configuration is not set or the directory doesn't exist, duplicate checking is automatically skipped.

## Output

Failed renames leave the original file in place and print `FAILED` even without
`--verbose`. The script reports `RENAME` only after a successful actual move
(or as a preview during `--dry-run`). Failed moves are counted under `Failed`,
not `Moved to error`, and errors produce a nonzero exit status. Only newly
created empty destination folders are removed after a failed rename; existing
folders and metadata are never deleted as failure cleanup.

The script provides a summary at the end:

```
Renamed: 45  Skipped: 12  Moved to error: 3  Possible duplicates: 8

================================================================================

🔄 POSSIBLE DUPLICATES:
--------------------------------------------------------------------------------
  1. Batman #001 (2025).cbz → Batman #001 (2025).cbz
  2. Spider-Man #005 (2024).cbr → Spider-Man #005 (2024).cbr
  ...

  Total: 8 file(s)

📋 ERRORS (Unparseable files):
--------------------------------------------------------------------------------
  1. corrupted_file.cbz
  2. weird_format.txt
  ...

  Total: 3 file(s)
================================================================================
```

## File Extensions

Supported formats:

- `.cbz` (Comic Book ZIP)
- `.cbr` (Comic Book RAR)

## Tips

1. **Always use `--dry-run` first** to preview changes before processing
2. **Use `--verbose`** to see detailed information about each file
3. **Back up your files** before running the script (especially on large collections)
4. **Set the external directory** environment variable for duplicate detection

## License

This project is open source and available for use.

## Contributing

Contributions are welcome! Feel free to open issues or submit pull requests.
