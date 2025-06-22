#!/usr/bin/env python3
"""cleanup_workspace.py

Consolidate and clean an AdaSchool-style workspace.

Steps (with confirmation prompts):
1. Identify top-level directories that are exact duplicates (identical contents) and rename them with a _duplicate<N> suffix.
2. Remove directories that are empty *or* whose only contents are a single node_modules/ tree.
3. Remove every nested .git directory so we can start fresh.
4. If requested, initialise a new Git repository at the workspace root and commit the current state.

The script is designed to run from the workspace root (same directory as this file).
"""
from __future__ import annotations

import filecmp
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

# --------------------------- helpers --------------------------- #

BLUE = "\033[1;34m"
YELLOW = "\033[1;33m"
RED = "\033[1;31m"
RESET = "\033[0m"

# CLI flags will be populated in main()
ARGS: argparse.Namespace

def info(msg: str) -> None:
    print(f"{BLUE}[INFO]{RESET} {msg}")

def warn(msg: str) -> None:
    print(f"{YELLOW}[WARN]{RESET} {msg}")

def err(msg: str) -> None:
    print(f"{RED}[ERR ]{RESET} {msg}", file=sys.stderr)


def confirm(prompt: str) -> bool:
    """Ask the user to confirm; obey --yes / --dry-run flags."""
    if ARGS.yes:
        return True
    if ARGS.dry_run:
        warn(f"(dry-run) {prompt} -> automatically 'No'")
        return False
    reply = input(f"{prompt} [y/N] ").strip().lower()
    return reply in {"y", "yes"}


# --------------------- duplicate detection --------------------- #

def dirs_identical(dir1: Path, dir2: Path) -> bool:
    """Recursively check if two directories have identical contents."""
    comp = filecmp.dircmp(dir1, dir2, ignore=[".git"])
    if comp.left_only or comp.right_only or comp.diff_files or comp.funny_files:
        return False
    # Recurse into common subdirectories
    for sub in comp.common_dirs:
        if not dirs_identical(dir1 / sub, dir2 / sub):
            return False
    return True


# -------------------- prune criteria helpers ------------------- #

def is_empty_or_node_only(directory: Path) -> bool:
    """Return True if dir is empty *or* contains only node_modules/ (recursively)."""
    entries = list(directory.iterdir())
    if not entries:
        return True  # completely empty

    # If everything is within a node_modules tree
    def contains_non_node(p: Path) -> bool:
        if p.is_dir():
            if p.name != "node_modules":
                return True
            # inside node_modules; still need to check children
            for child in p.iterdir():
                if contains_non_node(child):
                    return True
            return False
        # file
        return p.name != "node_modules"

    for ent in entries:
        if contains_non_node(ent):
            return False
    return True


# --------------------------- main ------------------------------ #

def gather_toplevel_dirs(root: Path) -> List[Path]:
    return [d for d in root.iterdir() if d.is_dir() and not d.name.startswith(".") and d.name != ".git"]


def rename_duplicates(dirs: List[Path]) -> List[Tuple[Path, Path]]:
    canonical: List[Path] = []
    rename_pairs: List[Tuple[Path, Path]] = []
    for current in dirs:
        duplicate_of = next((c for c in canonical if dirs_identical(current, c)), None)
        if duplicate_of is not None:
            suffix = 1
            new_name = current.with_name(current.name + f"_duplicate{suffix}")
            while new_name.exists():
                suffix += 1
                new_name = current.with_name(current.name + f"_duplicate{suffix}")
            rename_pairs.append((current, new_name))
        else:
            canonical.append(current)
    return rename_pairs


def remove_nested_git(root: Path) -> List[Path]:
    removed: List[Path] = []
    for git_dir in root.rglob(".git"):
        removed.append(git_dir)
        if not ARGS.dry_run:
            shutil.rmtree(git_dir)
    return removed


def init_git_repo(root: Path) -> None:
    # Create .gitignore
    gitignore = root / ".gitignore"
    if ARGS.dry_run:
        warn("(dry-run) Would write .gitignore and initialise repo")
        return

    gitignore.write_text(
        """# Node
node_modules/

# Python
venv/
__pycache__/
*.py[cod]

# macOS
.DS_Store

# Logs
auto.log
*.log
""",
        encoding="utf-8",
    )
    if not (root / ".git").exists():
        subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL)
        info("Initialised new Git repository.")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    try:
        subprocess.run(["git", "commit", "-m", "Workspace cleanup"], cwd=root, check=True, stdout=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        info("Nothing new to commit.")


def main() -> None:
    global ARGS
    parser = argparse.ArgumentParser(description="Clean up AdaSchool workspace")
    parser.add_argument("--dry-run", action="store_true", help="Show actions but don't modify anything")
    parser.add_argument("-y", "--yes", action="store_true", help="Assume yes for all confirmations")
    ARGS = parser.parse_args()

    root = Path(__file__).resolve().parent
    info(f"Workspace root: {root}")

    top_dirs = gather_toplevel_dirs(root)
    if not top_dirs:
        warn("No top-level project directories found.")
        return

    # ---------------------------------------------------- duplicates
    rename_plans = rename_duplicates(top_dirs)
    if rename_plans:
        info(f"Found {len(rename_plans)} duplicate folders:")
        for old, new in rename_plans:
            print(f"  - {old.name} -> {new.name}")
        if confirm("Rename duplicates?"):
            for old, new in rename_plans:
                if not ARGS.dry_run:
                    old.rename(new)
            info("Duplicates renamed.")
        else:
            warn("Duplicate-renaming skipped.")
    else:
        info("No duplicate folders detected.")

    # Refresh dir list after potential renames
    top_dirs = gather_toplevel_dirs(root)

    # ---------------------------------------------------- prune dirs
    prune_targets = [d for d in top_dirs if is_empty_or_node_only(d)]
    if prune_targets:
        info(f"Directories eligible for removal: {len(prune_targets)}")
        for d in prune_targets:
            print(f"  - {d.name}")
        if confirm("Remove these directories?"):
            for d in prune_targets:
                if not ARGS.dry_run:
                    shutil.rmtree(d)
            info("Pruned directories removed.")
        else:
            warn("Prune step skipped.")
    else:
        info("No empty/node_modules-only directories to prune.")

    # ---------------------------------------------------- strip .git
    nested_git_dirs = list(root.rglob(".git"))
    if nested_git_dirs:
        info(f"Nested .git directories found: {len(nested_git_dirs)}")
        for g in nested_git_dirs:
            print(f"  - {g.relative_to(root)}")
        if confirm("Remove all nested .git directories?"):
            removed = remove_nested_git(root)
            info(f"Removed {len(removed)} nested .git directories.")
        else:
            warn("Removal of nested .git directories skipped.")
    else:
        info("No nested .git directories found.")

    # ---------------------------------------------------- git init
    if confirm("Initialise/commit root-level Git repository?"):
        try:
            init_git_repo(root)
            info("Root repository ready.")
        except Exception as exc:
            err(f"Git initialisation failed: {exc}")
    else:
        warn("Git init step skipped.")

    info("Dry-run complete." if ARGS.dry_run else "Workspace cleanup complete.")


if __name__ == "__main__":
    main() 