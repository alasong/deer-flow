#!/usr/bin/env python3
"""pdf-worktree.py — Git worktree lifecycle for PDD child-team compile isolation.

Supports three operations:
  create  <slug> <branch>    Create worktree + branch for child team
  export  <slug>              Export changes as patch (from worktree to parent)
  cleanup <slug>              Remove worktree + branch

Example:
  pdf-worktree.py create my-task pdf-my-task
  (do work in .fat/pdf/worktrees/my-task/)
  pdf-worktree.py export my-task   → .fat/pdf/patches/my-task.patch
  pdf-worktree.py cleanup my-task
"""

import os, sys, subprocess, shutil

WORKTREE_BASE = ".fat/pdf/worktrees"
PATCH_BASE = ".fat/pdf/patches"
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

def _git(*args):
    result = subprocess.run(["git"] + list(args), capture_output=True, text=True, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        print(f"git {' '.join(args)} failed: {result.stderr.strip()}", file=sys.stderr)
    return result

def cmd_create(slug, branch):
    worktree_path = os.path.join(PROJECT_ROOT, WORKTREE_BASE, slug)
    if os.path.exists(worktree_path):
        print(f"Worktree already exists at {worktree_path}")
        return 1

    os.makedirs(os.path.join(PROJECT_ROOT, WORKTREE_BASE), exist_ok=True)

    # Create branch from current HEAD
    r = _git("branch", branch)
    if r.returncode != 0:
        existing = _git("rev-parse", "--verify", branch)
        if existing.returncode != 0:
            print(f"Failed to create branch {branch}", file=sys.stderr)
            return 1
        print(f"Branch {branch} already exists")

    # Create worktree
    r = _git("worktree", "add", worktree_path, branch)
    if r.returncode != 0:
        print(f"Failed to create worktree at {worktree_path}", file=sys.stderr)
        return 1

    print(f"Worktree created at {worktree_path} (branch: {branch})")
    return 0

def cmd_export(slug):
    worktree_path = os.path.join(PROJECT_ROOT, WORKTREE_BASE, slug)
    if not os.path.exists(worktree_path):
        print(f"Worktree not found at {worktree_path}", file=sys.stderr)
        return 1

    os.makedirs(os.path.join(PROJECT_ROOT, PATCH_BASE), exist_ok=True)
    patch_path = os.path.join(PROJECT_ROOT, PATCH_BASE, f"{slug}.patch")

    # Diff worktree HEAD against main branch
    r = _git("diff", f"refs/heads/main...HEAD", "--", ".", f":(exclude).fat/pdf/")
    if r.returncode != 0:
        print("Failed to generate diff", file=sys.stderr)
        return 1

    with open(patch_path, "w") as f:
        f.write(r.stdout)

    if r.stdout.strip():
        print(f"Patch exported to {patch_path} ({len(r.stdout.splitlines())} lines)")
    else:
        print(f"No changes to export (empty diff)")
        os.remove(patch_path)

    return 0

def cmd_cleanup(slug):
    worktree_path = os.path.join(PROJECT_ROOT, WORKTREE_BASE, slug)
    if not os.path.exists(worktree_path):
        print(f"Worktree not found at {worktree_path}", file=sys.stderr)
        return 1

    # Get branch name from worktree before removing
    r = _git("-C", worktree_path, "rev-parse", "--symbolic-full-name", "HEAD")
    branch = None
    if r.returncode == 0:
        branch = r.stdout.strip().replace("refs/heads/", "")

    # Remove worktree
    r = _git("worktree", "remove", worktree_path)
    if r.returncode != 0:
        print(f"Failed to remove worktree, trying --force", file=sys.stderr)
        _git("worktree", "remove", "--force", worktree_path)

    # Delete branch if we know it
    if branch:
        _git("branch", "-D", branch)

    # Clean up patch
    patch_path = os.path.join(PROJECT_ROOT, PATCH_BASE, f"{slug}.patch")
    if os.path.exists(patch_path):
        os.remove(patch_path)

    print(f"Cleaned up worktree {slug}")
    return 0

def print_usage():
    print(__doc__)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "create" and len(sys.argv) >= 4:
        sys.exit(cmd_create(sys.argv[2], sys.argv[3]))
    elif cmd == "export" and len(sys.argv) >= 3:
        sys.exit(cmd_export(sys.argv[2]))
    elif cmd == "cleanup" and len(sys.argv) >= 3:
        sys.exit(cmd_cleanup(sys.argv[2]))
    else:
        print_usage()
        sys.exit(1)
