"""Git diff extraction and parsing."""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

from testwise.exceptions import DiffError
from testwise.models import DiffFile, DiffResult

logger = logging.getLogger(__name__)

# Status letter mapping from git diff --name-status
_STATUS_MAP = {
    "A": "added",
    "M": "modified",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
}


def get_diff(
    base_ref: str | None = None,
    head_ref: str | None = None,
    repo_path: Path | None = None,
) -> DiffResult:
    """Extract a structured diff between two git refs.

    If refs are not provided, auto-detects from GitHub Actions environment
    or falls back to HEAD~1.
    """
    if base_ref is None:
        base_ref = _detect_base_ref()
    if head_ref is None:
        head_ref = os.environ.get("GITHUB_SHA", "HEAD")

    cwd = str(repo_path) if repo_path else None

    # Get file-level stats
    files = _get_changed_files(base_ref, head_ref, cwd)

    # Get patches
    patches = _get_patches(base_ref, head_ref, cwd)

    # Merge patches into file objects
    for f in files:
        f.patch = patches.get(f.path, "")

    total_add = sum(f.additions for f in files)
    total_del = sum(f.deletions for f in files)

    return DiffResult(
        base_ref=base_ref,
        head_ref=head_ref,
        files=files,
        total_additions=total_add,
        total_deletions=total_del,
    )


def filter_diff_files(
    files: list[DiffFile],
    include: list[str],
    exclude: list[str],
) -> list[DiffFile]:
    """Filter diff files using include/exclude glob patterns."""
    from fnmatch import fnmatch

    result = files

    if include:
        result = [f for f in result if any(fnmatch(f.path, p) for p in include)]

    if exclude:
        result = [f for f in result if not any(fnmatch(f.path, p) for p in exclude)]

    return result


def truncate_diff(diff: DiffResult, max_lines: int) -> DiffResult:
    """Truncate patch content when total lines exceed budget.

    Removes patches from least-important files first:
    1. Documentation files
    2. Config files
    3. Other source files
    4. Files in test-adjacent directories (kept longest)
    """
    total_lines = sum(f.patch.count("\n") for f in diff.files)
    if total_lines <= max_lines:
        return diff

    # Sort files by priority (lowest priority truncated first)
    def priority(f: DiffFile) -> int:
        path = f.path.lower()
        if any(path.endswith(ext) for ext in (".md", ".rst", ".txt", ".adoc")):
            return 0  # docs
        if any(seg in path for seg in ("config", ".yml", ".yaml", ".toml", ".json", ".ini")):
            return 1  # config
        if "test" in path:
            return 3  # test-adjacent
        return 2  # other source

    sorted_files = sorted(diff.files, key=priority)

    # Remove patches starting from lowest priority until under budget
    truncated = {f.path: f.patch for f in diff.files}
    current = total_lines

    for f in sorted_files:
        if current <= max_lines:
            break
        patch_lines = truncated[f.path].count("\n")
        if patch_lines > 0:
            # Replace patch with a summary
            truncated[f.path] = f"[patch truncated: +{f.additions}/-{f.deletions} lines]"
            current -= patch_lines

    new_files = []
    for f in diff.files:
        new_files.append(
            DiffFile(
                path=f.path,
                status=f.status,
                additions=f.additions,
                deletions=f.deletions,
                patch=truncated[f.path],
                old_path=f.old_path,
            )
        )

    return DiffResult(
        base_ref=diff.base_ref,
        head_ref=diff.head_ref,
        files=new_files,
        total_additions=diff.total_additions,
        total_deletions=diff.total_deletions,
    )


def _detect_base_ref() -> str:
    """Auto-detect the base ref from environment."""
    # GitHub Actions pull_request event
    base = os.environ.get("GITHUB_BASE_REF")
    if base:
        return f"origin/{base}"

    # GitHub Actions push event
    before = os.environ.get("GITHUB_EVENT_BEFORE")
    if before and before != "0" * 40:
        return before

    # Local fallback
    return "HEAD~1"


def _get_changed_files(base_ref: str, head_ref: str, cwd: str | None) -> list[DiffFile]:
    """Get list of changed files with stats."""
    try:
        # Get name-status for file status
        status_result = subprocess.run(
            ["git", "diff", "--name-status", "--diff-filter=AMDRC", f"{base_ref}..{head_ref}"],
            capture_output=True,
            text=True,
            cwd=cwd,
        )
        if status_result.returncode != 0:
            raise DiffError(f"git diff --name-status failed: {status_result.stderr.strip()}")

        # Get numstat for line counts
        numstat_result = subprocess.run(
            ["git", "diff", "--numstat", f"{base_ref}..{head_ref}"],
            capture_output=True,
            text=True,
            cwd=cwd,
        )

        # Parse name-status
        status_map: dict[str, tuple[str, str | None]] = {}
        for line in status_result.stdout.strip().splitlines():
            if not line:
                continue
            parts = line.split("\t")
            status_letter = parts[0][0]  # R100 -> R
            status = _STATUS_MAP.get(status_letter, "modified")
            if status_letter in ("R", "C") and len(parts) >= 3:
                old_path, new_path = parts[1], parts[2]
                status_map[new_path] = (status, old_path)
            elif len(parts) >= 2:
                status_map[parts[1]] = (status, None)

        # Parse numstat
        numstat_map: dict[str, tuple[int, int]] = {}
        for line in numstat_result.stdout.strip().splitlines():
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 3:
                add_str, del_str, path = parts[0], parts[1], parts[2]
                # Binary files show "-" for add/del
                additions = int(add_str) if add_str != "-" else 0
                deletions = int(del_str) if del_str != "-" else 0
                numstat_map[path] = (additions, deletions)

        # Combine
        files = []
        for path, (status, old_path) in status_map.items():  # type: ignore[assignment]
            additions, deletions = numstat_map.get(path, (0, 0))
            files.append(
                DiffFile(
                    path=path,
                    status=status,  # type: ignore[arg-type]
                    additions=additions,
                    deletions=deletions,
                    old_path=old_path,
                )
            )

        return files

    except FileNotFoundError as e:
        raise DiffError("git not found. Is git installed?") from e
    except subprocess.SubprocessError as e:
        raise DiffError(f"git diff failed: {e}") from e


def _get_patches(base_ref: str, head_ref: str, cwd: str | None) -> dict[str, str]:
    """Get the actual diff patches per file."""
    try:
        result = subprocess.run(
            ["git", "diff", "--no-color", f"{base_ref}..{head_ref}"],
            capture_output=True,
            text=True,
            cwd=cwd,
        )
        if result.returncode != 0:
            logger.warning("git diff for patches failed: %s", result.stderr.strip())
            return {}

        return _parse_unified_diff(result.stdout)

    except (FileNotFoundError, subprocess.SubprocessError):
        logger.warning("Failed to get patches", exc_info=True)
        return {}


def _parse_unified_diff(raw: str) -> dict[str, str]:
    """Parse unified diff output into per-file patches."""
    patches: dict[str, str] = {}
    current_file: str | None = None
    current_patch: list[str] = []

    for line in raw.splitlines(keepends=True):
        if line.startswith("diff --git"):
            # Save previous file's patch
            if current_file:
                patches[current_file] = "".join(current_patch)
            # Extract file path from "diff --git a/path b/path"
            match = re.search(r" b/(.+)$", line.strip())
            current_file = match.group(1) if match else None
            current_patch = [line]
        elif current_file:
            current_patch.append(line)

    # Save last file
    if current_file:
        patches[current_file] = "".join(current_patch)

    return patches
