"""Reconcile obsolete library directories without overwriting existing content."""
import errno
import filecmp
import os
from pathlib import Path
import re
import shutil


RESERVED = {'.quarantine', 'error', 'possibleDuplicates'}


def move_file_no_replace(source, destination):
    source, destination = Path(source), Path(destination)
    # Hard linking is atomic and refuses an existing destination. For a
    # cross-filesystem move, exclusive creation supplies the same guarantee.
    try:
        os.link(source, destination, follow_symlinks=False)
    except OSError as error:
        if error.errno not in {errno.EXDEV, errno.EOPNOTSUPP, errno.ENOSYS, errno.EPERM}:
            raise
        created = False
        try:
            with source.open('rb') as incoming, destination.open('xb') as outgoing:
                created = True
                shutil.copyfileobj(incoming, outgoing)
                outgoing.flush()
                os.fsync(outgoing.fileno())
            shutil.copystat(source, destination, follow_symlinks=False)
        except Exception:
            if created:
                destination.unlink()
            raise
    source.unlink()


def series_key(name):
    return ''.join(char for char in name.casefold() if char.isalnum())


def reconcile_directories(root, planner, target_dirs, source_targets, moved_sources,
                          dry_run=False, blocked_dirs=(), pending_files=None,
                          scan_directories=True, planned_files=None):
    root = Path(os.path.abspath(root))
    supplied = list(target_dirs) + list(blocked_dirs) + list(moved_sources)
    for source, destinations in source_targets.items():
        supplied.extend([source, *destinations])
    for mapping in (pending_files or {}, planned_files or {}):
        supplied.extend(mapping.keys())
        supplied.extend(mapping.values())
    for value in supplied:
        if os.path.commonpath([str(root), os.path.abspath(value)]) != str(root):
            message = 'Refusing reconciliation path outside the selected library root'
            print(f'FAILED    : {message}')
            return [message]
    targets = {Path(path).absolute() for path in target_dirs if not Path(path).is_symlink()}
    blocked = {Path(path).absolute() for path in blocked_dirs}
    departed = {Path(path).absolute() for path in moved_sources} if dry_run else set()
    hints = {Path(path).absolute(): {Path(dest).absolute() for dest in dests}
             for path, dests in source_targets.items()}
    errors = []
    reserved_paths = set()
    # A dry run tracks virtual destinations so collisions between two source
    # folders are handled exactly as they are in an actual run.
    virtual_files = {Path(dest).absolute(): Path(src).absolute()
                     for dest, src in (planned_files or {}).items()} if dry_run else {}
    virtual_dirs = set(targets)

    def exists(path):
        return path in reserved_paths or path in virtual_files or path in virtual_dirs or os.path.lexists(path)

    def directory(path):
        return not path.is_symlink() and (path.is_dir() or path in virtual_dirs)

    def log(action, path, destination=None):
        message = f'{action:<10}: {path.relative_to(root)}'
        if destination is not None:
            message += f' -> {destination.relative_to(root)}'
        print(message)

    def parents(path):
        relative = path.relative_to(root)
        current = root
        for part in relative.parts:
            current /= part
            if current.is_symlink() or (os.path.lexists(current) and not current.is_dir()):
                raise OSError(f'Unsafe destination directory: {current}')
            if not dry_run:
                current.mkdir(exist_ok=True)
            virtual_dirs.add(current)

    def move_file(source, destination):
        parents(destination.parent)
        if dry_run:
            virtual_files[destination] = source
            departed.add(source)
            return
        move_file_no_replace(source, destination)

    def quarantine(source):
        destination = root / '.quarantine' / source.relative_to(root)
        original = destination
        suffix = 1
        while exists(destination):
            destination = original.with_name(f'{original.name} ({suffix})')
            suffix += 1
        parents(destination.parent)
        reserved_paths.add(destination)
        if not dry_run:
            if source.is_symlink():
                os.symlink(os.readlink(source), destination)
                source.unlink()
            elif source.is_dir():
                shutil.move(str(source), str(destination))
            elif source.is_file():
                move_file(source, destination)
            else:
                os.rename(source, destination)
        departed.add(source)
        log('QUARANTINE', source, destination)

    def entries(path):
        return [item for item in sorted(path.iterdir()) if item not in departed]

    def remove_empty(path):
        if entries(path):
            return False
        if not dry_run:
            path.rmdir()
        departed.add(path)
        log('REMOVE DIR', path)
        return True

    def merge_entry(item, dest):
        for ancestor in dest.parents:
            if ancestor == root:
                break
            if ancestor.is_symlink():
                quarantine(item)
                return
        if item.is_symlink():
            quarantine(item)
        elif item.is_dir():
            if exists(dest) and not directory(dest):
                quarantine(item)
            else:
                merge(item, dest)
        elif not item.is_file():
            quarantine(item)
        elif not exists(dest):
            move_file(item, dest)
            log('MOVE FILE', item, dest)
        else:
            comparison = virtual_files.get(dest, dest)
            if not dest.is_symlink() and comparison.is_file() and filecmp.cmp(item, comparison, shallow=False):
                if not dry_run:
                    item.unlink()
                departed.add(item)
                log('DUPLICATE', item, dest)
            else:
                quarantine(item)

    def merge(source, destination):
        if remove_empty(source):
            return
        parents(destination)
        for item in entries(source):
            merge_entry(item, destination / item.name)
        remove_empty(source)

    def match(source):
        if source in hints:
            candidates = hints[source] & targets
        else:
            candidates = set()
            name = source.name
            forms = {name, name.replace('.', ' ').replace('[', '(').replace(']', ')')}
            for form in forms:
                plan = planner(form)
                if not plan:
                    continue
                wanted = series_key(plan[0])
                has_year = re.search(r'\(\d{4}\)$', plan[0])
                for target in targets:
                    comparable = target.name if has_year else re.sub(r'\s*\(\d{4}\)$', '', target.name)
                    if series_key(comparable) == wanted:
                        candidates.add(target)
        return next(iter(candidates)) if len(candidates) == 1 else None

    def visit(source):
        if source in departed or source in targets:
            return
        if source in blocked or any(source in path.parents for path in blocked):
            print(f'KEEP FAILED: {source.relative_to(root)} (archive operation failed)')
            return
        if source.is_symlink():
            quarantine(source)
            return
        destination = match(source)
        if destination is not None and source not in destination.parents:
            merge(source, destination)
            return
        # A grouping folder may contain individually matchable release folders.
        for child in entries(source):
            if child.is_symlink() or child.is_dir():
                visit(child)
        if not remove_empty(source):
            quarantine(source)

    try:
        top_level = [path for path in sorted(root.iterdir())
                     if path.name not in RESERVED and (path.is_dir() or path.is_symlink())]
        if not scan_directories:
            top_level = []
        discovered = {}
        for path in top_level:
            if path.is_symlink():
                continue
            plan = planner(path.name)
            if (plan and re.search(r'\(\d{4}\)$', path.name)
                    and plan[0].casefold() == path.name.casefold()):
                discovered.setdefault(series_key(path.name), set()).add(path)
        for key, candidates in discovered.items():
            # Archive-derived destinations take precedence over spelling
            # variants left behind with metadata only. Without that evidence,
            # conflicting canonical-looking variants are not guessed between.
            if any(series_key(target.name) == key for target in targets):
                continue
            if len(candidates) == 1:
                targets.update(candidates)
        virtual_dirs.update(targets)
        for src, dest in (pending_files or {}).items():
            source = Path(src).absolute()
            try:
                merge_entry(source, Path(dest).absolute())
            except OSError as error:
                blocked.add(source.parent)
                errors.append(f'{source.relative_to(root)} (reconciliation failed: {error})')
                print(f'FAILED    : {errors[-1]}')
        for source in top_level:
            try:
                visit(source)
            except OSError as error:
                errors.append(f'{source.relative_to(root)} (reconciliation failed: {error})')
                print(f'FAILED    : {errors[-1]}')
    except OSError as error:
        errors.append(f'directory reconciliation failed: {error}')
        print(f'FAILED    : {errors[-1]}')
    return errors
