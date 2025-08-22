import os
import sys
from pathlib import Path
import zipfile
import datetime

"""
Export only the engine/game code into a single monolithic text file, and also produce a zip archive
containing just the engine/game code.

Definition of "engine/game code":
- Included roots (allowlist): 'engine/', 'rpg/'
- Everything else is excluded from both the monolith and the zip (e.g., data/, scripts/, ui/, exports/, etc.)

Usage:
- Double click (on systems that run .py with Python) or run:
    python scripts/export_monolith.py

Outputs:
- exports/monolith.txt         : A giant text file with each included source file concatenated with clear separators.
- exports/monolith.zip         : A zip archive containing only the included engine/game code.

Notes:
- Binary files will be SKIPPED in the monolith to keep it human/AI readable.
- Line separators clearly mark each file boundary with its relative path.
- Deterministic ordering (sorted paths) for reproducibility.
"""

# Project root is the parent of the 'scripts' directory.
THIS_FILE = Path(__file__).resolve()
PROJECT_ROOT = THIS_FILE.parent.parent
EXPORT_DIR = PROJECT_ROOT / "exports"
MONOLITH_PATH = EXPORT_DIR / "monolith.txt"
ZIP_PATH = EXPORT_DIR / "monolith.zip"

# File extensions commonly treated as text (best-effort)
TEXT_EXTS = {
    ".py", ".txt", ".md", ".json", ".yml", ".yaml", ".ini", ".cfg",
    ".toml", ".csv", ".tsv", ".xml", ".html", ".htm", ".css", ".js", ".ts",
    ".tsx", ".jsx", ".env", ".gitignore", ".gitattributes", ".sh", ".bat",
    ".ps1", ".pyi",
}

# Allowlist roots to include in exports (engine/game code only)
INCLUDE_ROOTS = {"engine", "rpg"}

# Folders to always skip if encountered under included roots
SKIP_DIRS = {
    ".git", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".idea", ".vscode", "node_modules", "dist", "build", "exports"
}

# Specific files to exclude from the monolith output
SKIP_FILES = {
    "Follow this",
}

def is_text_file(path: Path) -> bool:
    # Heuristic: use extension; fallback to small binary sniffing
    if path.suffix.lower() in TEXT_EXTS:
        return True
    try:
        with open(path, "rb") as f:
            chunk = f.read(2048)
        # If there are null bytes, likely binary
        if b"\x00" in chunk:
            return False
        # Try decoding as utf-8 ignoring errors; if too many replacements would occur, treat as binary
        try:
            chunk.decode("utf-8")
            return True
        except UnicodeDecodeError:
            return False
    except Exception:
        # If unreadable, treat as binary to be safe
        return False

def dump_text(fp_out, rel_path: str, abs_path: Path):
    fp_out.write("\n")
    fp_out.write("=" * 100 + "\n")
    fp_out.write(f"FILE: {rel_path}\n")
    fp_out.write("=" * 100 + "\n\n")
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            fp_out.write(f.read())
    except Exception as e:
        fp_out.write(f"[ERROR] Failed to read as text: {e}\n")

def dump_binary(fp_out, rel_path: str, abs_path: Path):
    # Skip binary content in monolith; add a placeholder note only
    fp_out.write("\n")
    fp_out.write("=" * 100 + "\n")
    fp_out.write(f"FILE (binary skipped): {rel_path}\n")
    fp_out.write("=" * 100 + "\n\n")
    fp_out.write("[NOTE] Binary content omitted for readability.\n")

def build_monolith():
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().isoformat()
    with open(MONOLITH_PATH, "w", encoding="utf-8") as out:
        out.write("# Monolithic export of engine/game code\n")
        out.write(f"# Project root: {PROJECT_ROOT}\n")
        out.write(f"# Generated: {timestamp}\n")
        out.write("# Included roots: engine/, rpg/\n")
        out.write("# Order: sorted paths\n")
        out.write("\n")

        all_paths = []

        # Walk only included roots
        for root_name in sorted(INCLUDE_ROOTS):
            root_dir = PROJECT_ROOT / root_name
            if not root_dir.exists():
                continue
            for root, dirs, files in os.walk(root_dir):
                # prune unwanted dirs
                pruned = []
                for d in list(dirs):
                    if d in SKIP_DIRS:
                        pruned.append(d)
                for d in pruned:
                    dirs.remove(d)

                root_path = Path(root)
                for name in files:
                    p = root_path / name
                    # Skip outputs themselves if they end up under included roots (unlikely)
                    if p == MONOLITH_PATH or p == ZIP_PATH:
                        continue
                    try:
                        rel = p.relative_to(PROJECT_ROOT).as_posix()
                    except ValueError:
                        continue
                    # Skip specific files by name
                    if p.name in SKIP_FILES:
                        continue
                    all_paths.append((rel, p))

        # Sort by path to keep deterministic
        all_paths.sort(key=lambda t: t[0])

        for rel, p in all_paths:
            if is_text_file(p):
                dump_text(out, rel, p)
            else:
                dump_binary(out, rel, p)

    return MONOLITH_PATH

def build_zip():
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    # Create a zip archive containing only included roots
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root_name in sorted(INCLUDE_ROOTS):
            root_dir = PROJECT_ROOT / root_name
            if not root_dir.exists():
                continue
            for root, dirs, files in os.walk(root_dir):
                # prune unwanted dirs
                pruned = []
                for d in list(dirs):
                    if d in SKIP_DIRS:
                        pruned.append(d)
                for d in pruned:
                    dirs.remove(d)

                root_path = Path(root)
                for name in files:
                    p = root_path / name
                    if p == ZIP_PATH:
                        continue
                    try:
                        rel = p.relative_to(PROJECT_ROOT)
                    except ValueError:
                        continue
                    # Skip specific files by name
                    if p.name in SKIP_FILES:
                        continue
                    zf.write(p, rel)

    return ZIP_PATH

def main():
    print("Exporting monolithic text file ...")
    mono = build_monolith()
    print(f"Wrote: {mono}")

    print("Exporting full project zip ...")
    z = build_zip()
    print(f"Wrote: {z}")

    print("Done.")

if __name__ == "__main__":
    # Ensure we are running from project root context for relative paths
    os.chdir(PROJECT_ROOT)
    main()
