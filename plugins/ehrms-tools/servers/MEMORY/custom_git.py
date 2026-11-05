# -*- coding: utf-8 -*-
"""CUSTOM_GIT 唯讀查詢：列客製分支與分支內的檔案。

CUSTOM_GIT 只有一份共用工作目錄，這裡只用 branch／ls-tree 讀 git 物件，
不 checkout、不 fetch，不影響正在使用這個 repo 的人或排程。
資料以本機最後一次 git fetch 為準。
"""
import os
import subprocess
import time

DEFAULT_DIR = r"C:\D\CUSTOM_GIT"
_CACHE_SECONDS = 600
_TIMEOUT = 20

_cache = {"dir": None, "at": 0.0, "branches": None}


def repo_dir():
    return os.getenv("CUSTOM_GIT_DIR") or DEFAULT_DIR


def _git(*args):
    # stdin 必須隔開：MCP 走 stdio，子行程不可碰到協定串流
    return subprocess.run(
        ["git", "-C", repo_dir(), "-c", "core.quotepath=off", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, timeout=_TIMEOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def list_branches():
    """客製分支名稱（統編_代號_公司名，去掉 origin/ 後去重）。讀不到 repo 回傳 None。"""
    now = time.monotonic()
    if (_cache["dir"] == repo_dir() and _cache["branches"] is not None
            and now - _cache["at"] < _CACHE_SECONDS):
        return _cache["branches"]
    try:
        out = _git("branch", "-a", "--format=%(refname:short)")
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    names = set()
    for line in out.stdout.splitlines():
        name = line.strip()
        if name.startswith("origin/"):
            name = name[len("origin/"):]
        if name[:8].isdigit() and "_" in name:
            names.add(name)
    _cache.update(dir=repo_dir(), at=now, branches=sorted(names))
    return _cache["branches"]


def matches(branch, keyword):
    """統編（分支名第一段）完全相符，或關鍵字出現在分支名稱內。"""
    return branch.split("_", 1)[0] == keyword or keyword.lower() in branch.lower()


def branch_files(branch):
    """分支資料夾內的檔案（相對於「分支名/」）。讀不到回傳 None。
    只接受 list_branches 回傳過的名稱，名稱不會來自外部輸入。"""
    if branch not in (list_branches() or []):
        return None
    prefix = branch + "/"
    for ref in (f"origin/{branch}", branch):
        try:
            out = _git("ls-tree", "-r", "--name-only", ref)
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode == 0:
            return sorted(p[len(prefix):] for p in out.stdout.splitlines() if p.startswith(prefix))
    return None
