#!/usr/bin/env python3
"""
Batch convert BLIF files in a directory to AIG files using ABC.

Usage:
  python tools/blif_to_aig.py <input_dir> [output_dir]

- <input_dir>: directory containing .blif files (recursively processed).
- [output_dir]: optional directory where .aig files are written.
                Defaults to "<input_dir>/aig_out".

Notes:
- Only files matching "*_rebuilt.blif" are converted.
- Output filenames drop the "_rebuilt" suffix, e.g., "foo_rebuilt.blif" -> "foo.aig".
"""

import subprocess
import sys
from pathlib import Path


def quote_for_abc(path: Path) -> str:
    """Wrap a path in double quotes so ABC handles spaces."""
    escaped = str(path).replace('"', r"\"")
    return f'"{escaped}"'


def ensure_abc_in_path() -> None:
    """Fail fast if ABC is not available."""
    result = subprocess.run(["which", "abc"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        sys.exit("Error: 'abc' binary not found in PATH. Install ABC or adjust PATH before running.")


def convert_blif_to_aig(blif_path: Path, out_path: Path) -> None:
    """Invoke ABC to read BLIF and write AIG."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["abc", "-c", f"read_blif {quote_for_abc(blif_path)}; strash; write_aiger {quote_for_abc(out_path)}"]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"ABC failed on {blif_path}:\n{result.stdout}")
    if not out_path.exists():
        raise RuntimeError(f"ABC reported success but did not create '{out_path}'. Output:\n{result.stdout}")


def main(argv: list[str]) -> None:
    if len(argv) < 2 or len(argv) > 3:
        sys.exit(__doc__)

    input_dir = Path(argv[1]).expanduser().resolve()
    if not input_dir.is_dir():
        sys.exit(f"Error: input directory '{input_dir}' does not exist or is not a directory.")

    output_dir = Path(argv[2]).expanduser().resolve() if len(argv) == 3 else input_dir / "aig_out"

    ensure_abc_in_path()

    blif_files = sorted(input_dir.rglob("*_rebuilt.blif"))
    if not blif_files:
        sys.exit(f"No *_rebuilt.blif files found under '{input_dir}'.")

    print(f"[i] Converting {len(blif_files)} BLIF file(s) from '{input_dir}' to '{output_dir}'")
    failures: list[str] = []

    for blif_file in blif_files:
        relative = blif_file.relative_to(input_dir)
        stem_without_rebuilt = relative.stem.replace("_rebuilt", "", 1)
        out_file = output_dir / relative.with_name(f"{stem_without_rebuilt}.aig")
        try:
            convert_blif_to_aig(blif_file, out_file)
            print(f"[ok] {relative} -> {out_file.relative_to(output_dir)}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{relative}: {exc}")
            print(f"[fail] {relative}: {exc}")

    if failures:
        sys.exit(f"Completed with {len(failures)} failure(s):\n" + "\n".join(failures))


if __name__ == "__main__":
    main(sys.argv)
