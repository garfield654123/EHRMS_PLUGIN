# -*- coding: utf-8 -*-
"""repo 相對路徑正規化：寫入端把關，DB 裡永遠不出現本機絕對路徑。

HRMS_JIRA.CHANGED_FILES 與 HRMS_MEMORY.ENTRY_PATH 是跨機器共用的知識，
本機絕對路徑（C:/D/EHRMS_GIT/...）換台電腦就對不上；本模組在唯一寫入口
（jira_log / remember）做確定性轉換：
- 反斜線一律轉正斜線
- 絕對路徑以錨點資料夾（REPO_ROOT_MARKERS，預設 EHRMS_GIT,CUSTOM_GIT）剝除本機前綴：
  第一個錨點視為主 repo（剝掉錨點本身，如 VB/EHRMS/xx.cls）；
  其餘錨點保留名稱前綴以區分 repo（如 CUSTOM_GIT/23019591_Skhb/...）
- 剝不掉的絕對路徑回錯誤，由呼叫端拒絕寫入
"""
import os
import re

# 錨點資料夾名（逗號分隔可多個，第一個為主 repo）
_MARKERS = [m.strip() for m in
            os.getenv("REPO_ROOT_MARKERS", "EHRMS_GIT,CUSTOM_GIT").split(",") if m.strip()]

_ABS_RE = re.compile(r"^([A-Za-z]:/|//|/)")  # 磁碟機、UNC、POSIX 絕對路徑


def normalize(path):
    """單一路徑正規化。回 (repo相對路徑, None) 或 (None, 錯誤說明)。"""
    p = (path or "").strip().replace("\\", "/")
    if not p:
        return "", None
    if not _ABS_RE.match(p):
        return p.lstrip("./"), None
    low = p.lower()
    for idx, marker in enumerate(_MARKERS):
        i = low.find("/" + marker.lower() + "/")
        if i >= 0:
            rel = p[i + len(marker) + 2:]
            # 主 repo 剝掉錨點；其他 repo 保留名稱前綴以區分
            return (rel if idx == 0 else f"{marker}/{rel}"), None
    return None, f"「{path}」是絕對路徑且找不到錨點（{'、'.join(_MARKERS)}）"


def normalize_lines(text):
    """多行「路徑 :: 函式」正規化。回 (結果字串, 錯誤列表)。"""
    out, errs = [], []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if "::" in s:
            p, rest = s.split("::", 1)
            np, err = normalize(p)
            if err:
                errs.append(err)
            else:
                out.append(f"{np} :: {rest.strip()}")
        else:
            np, err = normalize(s)
            if err:
                errs.append(err)
            else:
                out.append(np)
    return "\n".join(out), errs
