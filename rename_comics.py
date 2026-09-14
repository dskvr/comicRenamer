#!/usr/bin/env python3
import argparse
import os
import re
import shutil
import sys
from typing import Optional, Tuple

from comicvine import ComicVine


COMIC_EXTENSIONS = {".cbr", ".cbz"}


def load_env_file(env_path: str = ".env") -> dict:
    """
    Load environment variables from a .env file.
    Returns a dictionary of key-value pairs.
    """
    env_vars = {}
    if os.path.isfile(env_path):
        try:
            with open(env_path, "r") as f:
                for line in f:
                    line = line.strip()
                    # Skip empty lines and comments
                    if not line or line.startswith("#"):
                        continue
                    # Parse KEY=VALUE format
                    if "=" in line:
                        key, value = line.split("=", 1)
                        key = key.strip()
                        value = value.strip().strip('"').strip("'")
                        env_vars[key] = value
        except Exception:
            # If we can't read the file, just return empty dict
            pass
    return env_vars


def load_configuration() -> dict:
    """Read script-local settings, with working-directory settings taking priority."""
    settings = load_env_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    settings.update(load_env_file())
    return settings


env_file_vars = load_configuration()
# Check .env file first, then environment variable
EXTERNAL_COMICS_DIR = env_file_vars.get("COMIC_SORTER_EXTERNAL_DIR") or os.environ.get("COMIC_SORTER_EXTERNAL_DIR")


def parse_annual_filename(stem: str) -> Optional[Tuple[str, str, int, Optional[str]]]:
    """
    Extract (base_title, full_title, issue_number, year) from a filename stem for annual issues.
    Handles patterns like "Title YYYY Annual 001 (YYYY)" where the year appears in the title.
    
    base_title is used for folder organization (e.g., "Absolute Batman")
    full_title includes the year and "Annual" for the filename (e.g., "Absolute Batman 2025 Annual")

    Expected pattern examples:
    - Title 2025 Annual 001 (2025)
    - Title 2025 Annual #001 (2025)
    - Absolute Batman 2025 Annual 001 (2025)

    Returns None if not parseable.
    """
    # Pattern: Title + YYYY + " Annual " + optional # + issue number + optional (YYYY)
    match = re.search(r"^(?P<title>.+?)\s+(?P<title_year>\d{4})\s+Annual\s+#?(?P<issue>\d{1,4})\s*(?:\((?P<year>\d{4})\)\)?)?", stem, flags=re.IGNORECASE)
    if not match:
        return None

    title_base = match.group("title").strip()
    title_year = match.group("title_year")
    issue_str = match.group("issue")
    year = match.group("year")  # May be None

    try:
        issue_num = int(issue_str)
    except ValueError:
        return None

    # Base title for folder organization (e.g., "Absolute Batman")
    base_title = re.sub(r"\s+", " ", title_base)
    
    # Full title for filename (e.g., "Absolute Batman 2025 Annual")
    full_title = f"{title_base} {title_year} Annual"
    full_title = re.sub(r"\s+", " ", full_title)

    return base_title, full_title, issue_num, year


def parse_filename(stem: str) -> Optional[Tuple[str, int, Optional[str]]]:
    """
    Extract (title, issue_number, year) from a filename stem.
    Year may be None if not present.

    Expected loose pattern examples:
    - Title 001 (2019)
    - Title #1 (2019)
    - Title #001 (2019) extra rip info
    - Title 02 (of 04) (2025) extra rip info
    - Title #01
    - Title 001

    Returns None if not parseable.
    """
    # Normalize stray double right parens around year by ignoring trailing parens
    # Regex: leading title (non-greedy) + optional # + issue digits + optional (of xx) + optional (year)
    match = re.search(r"^(?P<title>.+?)\s+#?(?P<issue>\d{1,4})\s*(?:\(of\s+\d+\s*\))?\s*(?:\((?P<year>\d{4})\)\)?)?", stem, flags=re.IGNORECASE)
    if not match:
        return None

    title = match.group("title").strip()
    issue_str = match.group("issue")
    year = match.group("year")  # May be None

    try:
        issue_num = int(issue_str)
    except ValueError:
        return None

    # Collapse internal whitespace in title
    title = re.sub(r"\s+", " ", title)

    return title, issue_num, year


def parse_volume_filename(stem: str) -> Optional[Tuple[str, int, Optional[str]]]:
    """
    Extract (title, volume_number, year) from a filename stem for trades/volumes.

    Expected loose pattern examples:
    - Title v02 (2012)
    - Title v2 (2012) extra rip info

    Returns None if not parseable.
    """
    match = re.search(r"^(?P<title>.+?)\s+v(?:ol\.\s*)?(?P<vol>\d{1,4})(?!\w)\s*(?:\((?P<year>\d{4})\)\)?)?", stem, flags=re.IGNORECASE)
    if not match:
        return None

    title = match.group("title").strip()
    vol_str = match.group("vol")
    year = match.group("year")

    try:
        vol_num = int(vol_str)
    except ValueError:
        return None

    title = re.sub(r"\s+", " ", title)

    return title, vol_num, year


def parse_standalone_filename(stem: str) -> Optional[Tuple[str, str]]:
    """
    Extract (title, year) from a filename stem for standalone comics without issue numbers.

    Expected loose pattern examples:
    - Title (2025)
    - Title (2024) extra rip info
    - Title - Subtitle (2022) (digital) (extra info)

    Returns None if not parseable.
    """
    # Match: title (ending before year) + year in parentheses
    # We need to avoid matching issue numbers or volume numbers, so we check that
    # there's no #digit or vdigit or digit followed by (of xx) before the year
    match = re.search(r"^(?P<title>.+?)\s+\((?P<year>\d{4})\)", stem)
    if not match:
        return None

    title = match.group("title").strip()
    year = match.group("year")

    # Check that this doesn't match an issue or volume pattern
    # If title ends with a number pattern that could be an issue, this is probably a numbered issue
    # We'll let the other parsers handle those cases first
    if re.search(r"#?\d{1,4}\s*$", title):
        # Title ends with a number, might be an issue number
        return None
    if re.search(r"\s+v\d{1,4}\s*$", title, flags=re.IGNORECASE):
        # Title ends with volume pattern
        return None

    # Collapse internal whitespace in title
    title = re.sub(r"\s+", " ", title)

    return title, year


def parse_standalone_no_year_filename(stem: str) -> Optional[str]:
    """
    Extract title from a filename stem for standalone comics without issue numbers or years.
    Handles filenames that may have edition/subtitle info in parentheses.

    Expected loose pattern examples:
    - Title
    - Title (20th Anniversary Edition)
    - Title - Subtitle
    - Title - Subtitle (Special Edition)

    Returns None if not parseable (e.g., if it matches issue/volume patterns).
    """
    # Check that this doesn't match an issue pattern (issue number after # or space, before parentheses or end)
    # More specific: # followed by digits, or space followed by digits that are clearly an issue number
    if re.search(r"#\d{1,4}\s*(?:\(of\s+\d+\s*\))?\s*(?:\(\d{4}\))?", stem, flags=re.IGNORECASE):
        # Has issue number pattern with #
        return None
    # Check for standalone digits that look like issue numbers (after space, possibly before "of" or year)
    if re.search(r"\s+\d{1,4}\s*(?:\(of\s+\d+\s*\)|\(\d{4}\))", stem):
        # Has issue number pattern without #
        return None
    # Check for volume pattern (v followed by digits)
    if re.search(r"\s+v\d{1,4}\s*(?:\(\d{4}\))?", stem, flags=re.IGNORECASE):
        # Has volume pattern
        return None
    
    # If there's a 4-digit year in parentheses at the end, let parse_standalone_filename handle it
    if re.search(r"\(\d{4}\)", stem):
        return None

    # Extract the title part (everything before optional parentheses with non-year content)
    # Try to match title with optional parentheses content
    match = re.match(r"^(?P<title>[^(]+?)(?:\s+\([^)]+\))?\s*$", stem)
    if match:
        title = match.group("title").strip()
        # Collapse internal whitespace
        title = re.sub(r"\s+", " ", title)
        return title

    return None


def is_comic_file(path: str) -> bool:
    _, ext = os.path.splitext(path)
    return ext.lower() in COMIC_EXTENSIONS


def ensure_dir(path: str) -> None:
    if not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)


def check_external_duplicate(title: str, desired_filename: str) -> bool:
    """
    Check if a comic with the given title and filename already exists in the external comics directory.
    Case-insensitive matching for both folder and filename.
    File extensions are ignored when comparing filenames.
    
    Returns True if a file with the same stem (name without extension) exists in EXTERNAL_COMICS_DIR/<series folder>/ (case-insensitive)
    """
    if not EXTERNAL_COMICS_DIR or not os.path.isdir(EXTERNAL_COMICS_DIR):
        # External directory doesn't exist, treat as no duplicate
        return False
    
    # Extract stem (filename without extension) from desired_filename for comparison
    desired_stem, _ = os.path.splitext(desired_filename)
    desired_stem_lower = desired_stem.lower()
    
    # Case-insensitive search for title folder
    title_lower = title.lower()
    try:
        for folder_name in os.listdir(EXTERNAL_COMICS_DIR):
            folder_path = os.path.join(EXTERNAL_COMICS_DIR, folder_name)
            if os.path.isdir(folder_path) and folder_name.lower() == title_lower:
                # Found matching folder (case-insensitive)
                # Now check for filename stem (case-insensitive, ignoring extension)
                try:
                    for file_name in os.listdir(folder_path):
                        if os.path.isfile(os.path.join(folder_path, file_name)):
                            file_stem, _ = os.path.splitext(file_name)
                            if file_stem.lower() == desired_stem_lower:
                                return True
                except OSError:
                    # Can't read folder, skip it
                    pass
                break
    except OSError:
        # Can't read external directory
        pass
    
    return False


def unique_destination_path(base_dir: str, desired_name: str, ext: str) -> str:
    """
    Return a unique destination path by appending " (1)", "(2)", ... if needed.
    """
    candidate = os.path.join(base_dir, desired_name + ext)
    if not os.path.exists(candidate):
        return candidate

    counter = 1
    while True:
        candidate = os.path.join(base_dir, f"{desired_name} ({counter}){ext}")
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def format_issue(issue_num: int) -> str:
    # Use #XXX for <= 999, otherwise use the full number (e.g., #1000)
    if issue_num <= 999:
        return f"#{issue_num:03d}"
    return f"#{issue_num}"


def capitalize_title(title: str) -> str:
    """
    Capitalize a comic title, preserving word boundaries.
    Example: "batman - dark victory" -> "Batman - Dark Victory"
    """
    # Split on whitespace and capitalize each word
    words = title.split()
    capitalized_words = [word.capitalize() for word in words]
    return " ".join(capitalized_words)


def plan_new_name_and_title(stem: str) -> Optional[Tuple[str, str]]:
    """Return (series_folder, desired_stem) for either issue, volume, or standalone forms."""
    # Some releases encode a year as a volume label rather than (YYYY).
    volume_year = re.search(r"(?<!\w)vol\.\s*(?P<year>\d{4})(?!\w)", stem, re.IGNORECASE)
    if volume_year:
        stem = (stem[:volume_year.start()] + " " + stem[volume_year.end():]).strip()
        stem = re.sub(r"\s+", " ", stem)
        # Preserve an explicit publication year when both forms are present.
        if not re.search(r"\(\d{4}\)", stem):
            metadata = re.search(r"\s*\((?!of\s+\d+\s*\))", stem, re.IGNORECASE)
            position = metadata.start() if metadata else len(stem)
            stem = stem[:position].rstrip() + f" ({volume_year.group('year')})" + stem[position:]

    vol = parse_volume_filename(stem)
    if vol:
        title, vol_num, year = vol
        title = capitalize_title(title)
        if year:
            return f"{title} ({year})", f"{title} Vol. {vol_num} ({year})"
        return title, f"{title} Vol. {vol_num}"

    # Check for annual issues before regular issues (annual has more specific pattern)
    annual = parse_annual_filename(stem)
    if annual:
        base_title, full_title, issue_num, year = annual
        # Use base_title for folder, full_title for filename
        base_title = capitalize_title(base_title)
        full_title = capitalize_title(full_title)
        if year:
            return f"{base_title} ({year})", f"{full_title} {format_issue(issue_num)} ({year})"
        else:
            return base_title, f"{full_title} {format_issue(issue_num)}"

    iss = parse_filename(stem)
    if iss:
        title, issue_num, year = iss
        title = capitalize_title(title)
        if year:
            return f"{title} ({year})", f"{title} {format_issue(issue_num)} ({year})"
        else:
            return title, f"{title} {format_issue(issue_num)}"

    standalone = parse_standalone_filename(stem)
    if standalone:
        title, year = standalone
        title = capitalize_title(title)
        return f"{title} ({year})", f"{title} ({year})"

    standalone_no_year = parse_standalone_no_year_filename(stem)
    if standalone_no_year:
        title = capitalize_title(standalone_no_year)
        return title, title

    return None


def print_summary_table(errors: list, duplicates: list) -> None:
    """Print a nice table of errors and duplicates."""
    if not errors and not duplicates:
        return
    
    print("\n" + "=" * 80)
    
    if errors:
        print("\n📋 FILES REQUIRING ATTENTION:")
        print("-" * 80)
        for i, error_file in enumerate(errors, 1):
            print(f"  {i:2d}. {error_file}")
        print(f"\n  Total: {len(errors)} file(s)")
    
    if duplicates:
        print("\n🔄 POSSIBLE DUPLICATES:")
        print("-" * 80)
        for i, dup_file in enumerate(duplicates, 1):
            print(f"  {i:2d}. {dup_file}")
        print(f"\n  Total: {len(duplicates)} file(s)")
    
    print("=" * 80 + "\n")


def directory_year(title: str, src_path: str, target_dir: str) -> Optional[str]:
    """Find a year on the nearest matching series folder, up to the scan root."""
    directory = os.path.abspath(os.path.dirname(src_path))
    target_dir = os.path.abspath(target_dir)
    while True:
        match = re.fullmatch(r"(.+?)\s+(?:\((\d{4})\)|Vol\.\s*(\d{4})|(\d{4}))",
                             os.path.basename(directory), re.IGNORECASE)
        if match and capitalize_title(match[1]) == title:
            return next(year for year in match.groups()[1:] if year)
        if directory == target_dir or os.path.dirname(directory) == directory:
            return None
        directory = os.path.dirname(directory)


def process_directory(target_dir: str, dry_run: bool, verbose: bool, recursive: bool = False,
                      comicvine: Optional[ComicVine] = None) -> Tuple[int, int, int, int, list, list]:
    """Process files in target_dir. Returns (renamed_count, skipped_count, error_count, duplicates_count, errors_list, duplicates_list)."""
    error_dir = os.path.join(target_dir, "error")
    duplicates_dir = os.path.join(target_dir, "possibleDuplicates")
    if not dry_run:
        ensure_dir(error_dir)
        ensure_dir(duplicates_dir)

    renamed = 0
    skipped = 0
    errored = 0
    duplicates = 0
    errors_list = []
    duplicates_list = []
    folders_with_duplicates = set()  # Track title folders that contain duplicates

    # Snapshot inputs before moving files or creating output folders.
    sources = []
    scanned_dirs = []
    moved_sources = set()
    def scan_error(error):
        raise error

    for root, dirs, files in os.walk(target_dir, onerror=scan_error):
        if root != target_dir:
            scanned_dirs.append(root)
        dirs[:] = sorted(d for d in dirs if not d.startswith('.')
                         and d not in {"error", "possibleDuplicates"}) if recursive else []
        for name in sorted(files):
            path = os.path.join(root, name)
            if not name.startswith('.') and os.path.isfile(path) and is_comic_file(path):
                sources.append(path)

    if verbose:
        print(f"SCAN      : {target_dir} ({'recursive' if recursive else 'top-level only'}); found {len(sources)} comic file(s)")
    if not sources:
        print(f"No .cbz or .cbr files found in {target_dir}."
              + (" Use --recursive to include subdirectories." if not recursive else ""))

    for src_path in sources:
        entry = os.path.relpath(src_path, target_dir)

        stem, ext = os.path.splitext(os.path.basename(src_path))
        plan = plan_new_name_and_title(stem)
        if plan and not re.search(r"\(\d{4}\)$", plan[1]):
            year = directory_year(plan[0], src_path, target_dir)
            source = "directory"
            if not year and comicvine is not None:
                year = comicvine.lookup_year(plan[0])
                source = "Comic Vine series start year"
                if not year:
                    skipped += 1
                    print(f"SKIP      : {entry} (series year unresolved)")
                    continue
            if year:
                if verbose or dry_run:
                    print(f"YEAR      : {entry} -> {year} ({source})")
                plan = (f"{plan[0]} ({year})", f"{plan[1]} ({year})")
        desired_stem = None if not plan else plan[1]

        if not desired_stem:
            # Move to error
            dest_path = unique_destination_path(error_dir, stem, ext)
            if verbose or dry_run:
                print(f"Unparseable -> {os.path.relpath(dest_path, target_dir)}")
            if not dry_run:
                try:
                    shutil.move(src_path, dest_path)
                except Exception as e:
                    print(f"FAILED    : {entry} -> {dest_path} ({e})")
                    errors_list.append(f"{entry} (move to error failed: {e})")
                    continue
            errored += 1
            errors_list.append(entry)
            continue

        # Skip only when both the filename and series folder are correct.
        title_dir = os.path.join(target_dir, plan[0])
        if stem == desired_stem and os.path.abspath(os.path.dirname(src_path)) == os.path.abspath(title_dir):
            skipped += 1
            if verbose:
                print(f"OK        : {entry}")
            continue

        # Place files into the planned series folder.
        created_title_dir = not os.path.exists(title_dir)
        dest_path = unique_destination_path(title_dir, desired_stem, ext)
        
        if not dry_run:
            try:
                ensure_dir(title_dir)
                os.rename(src_path, dest_path)
                renamed += 1
            except Exception as e:
                print(f"FAILED    : {src_path} -> {dest_path} ({e}); original left in place")
                if created_title_dir:
                    try:
                        os.rmdir(title_dir)
                    except OSError:
                        pass  # Never remove an existing or nonempty directory.
                errors_list.append(f"{entry} (rename failed: {e})")
                continue
        else:
            # In dry run, count as renamed (would be renamed)
            renamed += 1
        moved_sources.add(src_path)

        if verbose or dry_run:
            print(f"RENAME    : {entry} -> {os.path.relpath(dest_path, target_dir)}")
        if verbose:
            print(f"FOLDER    : {os.path.relpath(title_dir, target_dir)}")

        # Check if this comic already exists in the external comics directory
        # Do this after renaming/moving so the file is properly organized first
        desired_filename = desired_stem + ext
        is_duplicate = check_external_duplicate(plan[0], desired_filename)
        
        if (verbose or dry_run) and EXTERNAL_COMICS_DIR:
            # Show duplicate check information
            external_path = os.path.join(EXTERNAL_COMICS_DIR, plan[0], desired_filename)
            if is_duplicate:
                print(f"DUPLICATE : Found in external directory")
                print(f"           External: {external_path}")
                print(f"           Note: Entire folder '{plan[0]}' will be moved to possibleDuplicates")
            else:
                print(f"CHECK     : Not found in external directory")
                print(f"           External: {external_path}")
        
        if is_duplicate:
            # Count as duplicate (in both dry run and actual run)
            duplicates += 1
            duplicates_list.append(f"{entry} → {desired_stem + ext}")
            # Mark this title folder as having duplicates (will move entire folder later)
            folders_with_duplicates.add(plan[0])

    # After processing all files, move entire folders that contain duplicates
    for title in folders_with_duplicates:
        title_dir = os.path.join(target_dir, title)
        if dry_run or os.path.isdir(title_dir):
            # Calculate destination for the entire folder
            duplicate_folder_dest = os.path.join(duplicates_dir, title)
            if verbose or dry_run:
                print(f"\nMOVING FOLDER: {title} -> possibleDuplicates/{title} (contains duplicates)")
            if not dry_run:
                try:
                    # If destination exists, merge or rename
                    if os.path.exists(duplicate_folder_dest):
                        # Move contents individually
                        for item in os.listdir(title_dir):
                            src_item = os.path.join(title_dir, item)
                            dst_item = os.path.join(duplicate_folder_dest, item)
                            if os.path.isfile(src_item):
                                if not os.path.exists(dst_item):
                                    shutil.move(src_item, dst_item)
                                else:
                                    # File exists, use unique name
                                    base, ext = os.path.splitext(item)
                                    counter = 1
                                    while os.path.exists(dst_item):
                                        dst_item = os.path.join(duplicate_folder_dest, f"{base} ({counter}){ext}")
                                        counter += 1
                                    shutil.move(src_item, dst_item)
                        # Remove empty source folder
                        try:
                            os.rmdir(title_dir)
                        except OSError:
                            pass  # Folder not empty, leave it
                    else:
                        # Move entire folder
                        shutil.move(title_dir, duplicate_folder_dest)
                except Exception as e:
                    if verbose:
                        print(f"WARNING   : Could not move folder {title} to possibleDuplicates: {e}")

    # Prune only empty source folders. Recover empty dotted release folders
    # left by earlier runs when the matching normalized series already exists.
    source_dirs = {os.path.dirname(path) for path in moved_sources}
    for directory in scanned_dirs:
        name = os.path.basename(directory)
        if re.search(r"\.\d{1,4}\.\[", name) and re.search(r"\[\d{4}\]", name):
            release_stem = name.replace(".", " ").replace("[", "(").replace("]", ")")
            release_plan = plan_new_name_and_title(release_stem)
            if release_plan and os.path.isdir(os.path.join(target_dir, release_plan[0])):
                source_dirs.add(directory)
    removed_dirs = set()
    for directory in sorted(source_dirs, key=lambda path: (-path.count(os.sep), path)):
        if directory == target_dir or not os.path.isdir(directory) or os.path.islink(directory):
            continue
        remaining = [name for name in os.listdir(directory)
                     if not (dry_run and (os.path.join(directory, name) in moved_sources
                                          or os.path.join(directory, name) in removed_dirs))]
        if remaining:
            if verbose or dry_run:
                print(f"KEEP DIR  : {os.path.relpath(directory, target_dir)} ({len(remaining)} remaining entries; not deleted)")
            continue
        if not dry_run:
            try:
                os.rmdir(directory)
            except OSError as e:
                print(f"WARNING   : Could not remove empty source directory {directory} ({e})")
                continue
        removed_dirs.add(directory)
        if verbose or dry_run:
            print(f"REMOVE DIR: {os.path.relpath(directory, target_dir)} (empty)")

    return renamed, skipped, errored, duplicates, errors_list, duplicates_list


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Normalize to issues 'Title #XXX (YEAR)', volumes 'Title Vol. N (YEAR)', or standalone 'Title (YEAR)'; move files into ./<Title> (<YEAR>) (or ./<Title> without a year); unparseable files to ./error"
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=os.getcwd(),
        help="Target directory (defaults to current working directory)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show planned changes without modifying files")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print detailed actions")
    parser.add_argument("--recursive", "-r", action="store_true", help="Include subdirectories; organize renamed comics under the target directory")
    comicvine_options = parser.add_mutually_exclusive_group()
    comicvine_options.add_argument("--comicvine", action="store_true", help="Look up missing series years using COMICVINE_API_KEY")
    comicvine_options.add_argument("--no-comicvine", action="store_true", help="Keep Comic Vine lookup disabled (the default)")

    args = parser.parse_args(argv)

    target_dir = os.path.abspath(args.directory)
    if not os.path.isdir(target_dir):
        print(f"Not a directory: {target_dir}", file=sys.stderr)
        return 2

    settings = load_configuration()
    api_key = settings.get("COMICVINE_API_KEY") or os.environ.get("COMICVINE_API_KEY")
    if args.comicvine and not api_key:
        print("--comicvine requires COMICVINE_API_KEY in .env or the environment", file=sys.stderr)
        return 2
    comicvine = ComicVine(api_key) if args.comicvine else None
    renamed, skipped, errored, duplicates, errors_list, duplicates_list = process_directory(target_dir, args.dry_run, args.verbose, args.recursive, comicvine)
    failed = len(errors_list) - errored
    print(f"\nRenamed: {renamed}  Skipped: {skipped}  Moved to error: {errored}  Possible duplicates: {duplicates}  Failed: {failed}")
    
    # Print summary tables
    print_summary_table(errors_list, duplicates_list)
    
    return 1 if errors_list else 0


if __name__ == "__main__":
    sys.exit(main())
