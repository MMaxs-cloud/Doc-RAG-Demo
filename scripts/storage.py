"""Create verified offline bundles of SQLite, originals, derivatives and Qdrant."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path, PurePosixPath
import sqlite3
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile

APPLICATION = {"database", "documents", "canonical", "previews"}
ROOTS = {"application", "qdrant"}


def verify_database(path):
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as database:
        if database.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity check failed")


def entries(directory, prefix):
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symlinks are not supported: {path}")
        if path.is_file():
            yield path, f"{prefix}/{path.relative_to(directory).as_posix()}"


def copy_and_digest(source, target):
    digest = hashlib.sha256()
    while block := source.read(1024**2):
        digest.update(block)
        target.write(block)
    return digest.hexdigest()


def backup(application, qdrant, output):
    application, qdrant, output = (path.resolve() for path in (application, qdrant, output))
    if output.exists() or output.is_relative_to(qdrant) or any(
        output.is_relative_to(application / name) for name in APPLICATION
    ):
        raise ValueError("Use a fresh bundle path outside the backed-up directories")
    if not qdrant.is_dir():
        raise FileNotFoundError("Missing Qdrant directory")
    verify_database(application / "database" / "app.db")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        bundle = Path(temporary) / "backup.zip"
        files = list(entries(qdrant, "qdrant"))
        for folder in sorted(APPLICATION):
            files.extend(entries(application / folder, f"application/{folder}"))
        manifest = {}
        with ZipFile(bundle, "w", ZIP_DEFLATED, compresslevel=3) as archive:
            for path, name in files:
                with path.open("rb") as source, archive.open(name, "w", force_zip64=True) as target:
                    manifest[name] = copy_and_digest(source, target)
            archive.writestr("manifest.json", json.dumps({"format": 1, "hashes": manifest}, sort_keys=True))
        bundle.rename(output)


def validate_name(value):
    path = PurePosixPath(value)
    if (path.is_absolute() or path.as_posix() != value or "\\" in value or len(path.parts) < 2
        or path.parts[0] not in ROOTS or any(part in ("..", ".") or part.endswith((" ", ".")) or ":" in part or "\x00" in part for part in path.parts)
        or (path.parts[0] == "application" and path.parts[1] not in APPLICATION)):
        raise ValueError("Invalid backup path")
    return path


def restore(bundle, destination):
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError("Restore requires a new destination directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        stage = Path(temporary) / "restore"
        for name in ROOTS:
            (stage / name).mkdir(parents=True)
        with ZipFile(bundle) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            hashes = manifest["hashes"]
            names = archive.namelist()
            if manifest["format"] != 1 or len(names) != len(set(names)) or set(names) != {"manifest.json", *hashes}:
                raise ValueError("Backup manifest does not match the archive")
            for name, checksum in hashes.items():
                output = stage.joinpath(*validate_name(name).parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as source, output.open("xb") as target:
                    if copy_and_digest(source, target) != checksum:
                        raise ValueError(f"Backup digest mismatch: {name}")
        verify_database(stage / "application" / "database" / "app.db")
        stage.rename(destination)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    subcommands = cli.add_subparsers(dest="command", required=True)
    create = subcommands.add_parser("backup")
    create.add_argument("--application", type=Path, required=True)
    create.add_argument("--qdrant", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--services-stopped", action="store_true", required=True)
    recover = subcommands.add_parser("restore")
    recover.add_argument("bundle", type=Path)
    recover.add_argument("--destination", type=Path, required=True)
    args = cli.parse_args()
    if args.command == "backup":
        backup(args.application, args.qdrant, args.output)
    else:
        restore(args.bundle, args.destination)


if __name__ == "__main__":
    main()
