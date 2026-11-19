"""Decrypt a downloaded session directory. The passphrase comes from the environment."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from cloudos.glasses.archive.crypto import decrypt_bytes


def decrypt_tree(source: Path, dest: Path, passphrase: str) -> int:
    source = Path(source)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    for path in sorted(source.rglob("*.enc")):
        plain = decrypt_bytes(path.read_bytes(), passphrase)
        relative = path.relative_to(source).as_posix()[: -len(".enc")]
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(plain)
        count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Decrypt a glasses archive session")
    parser.add_argument("source", type=Path)
    parser.add_argument("dest", type=Path)
    args = parser.parse_args(argv)
    passphrase = os.environ.get("GLASSES_ARCHIVE_PASSPHRASE", "")
    if not passphrase:
        sys.stderr.write("Set GLASSES_ARCHIVE_PASSPHRASE. It is not read from the repository.\n")
        return 2
    count = decrypt_tree(args.source, args.dest, passphrase)
    sys.stdout.write(f"decrypted {count} files\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
