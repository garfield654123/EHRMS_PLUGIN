# -*- coding: utf-8 -*-
"""custom_lookup 的單元測試：用暫存 git repo 測分支讀取，資料庫以替身取代。"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "servers" / "MEMORY"))

import custom_core  # noqa: E402
import custom_db  # noqa: E402
import custom_git  # noqa: E402

BRANCH = "12345678_ABC_測試公司"


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    *args], check=True, capture_output=True)


def _file_row(path, kind, description="邏輯說明"):
    return {"BRANCH_NAME": BRANCH, "CUSTOM_PATH": path, "STANDARD_PATH": None, "CUSTOM_TYPE": kind,
            "DESCRIPTION": description, "SA_DOC_PATH": None, "STD_BASELINE": "202607_000",
            "SCANNED_AT": None}


def _doc_row(path, analyzed, summary=None):
    return {"BRANCH_NAME": BRANCH, "DOC_PATH": path, "DOC_TYPE": "spec", "VERSION_LABEL": None,
            "IS_LATEST": True, "ANALYZED": analyzed, "SUMMARY": summary, "MAPPING_STATUS": "unmapped"}


class CustomLookupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        _git(self.repo, "init", "-b", "main")
        (self.repo / "README.md").write_text("root", encoding="utf-8")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-m", "init")
        _git(self.repo, "checkout", "-b", BRANCH)
        folder = self.repo / BRANCH
        (folder / "eHRMS").mkdir(parents=True)
        (folder / "SA").mkdir()
        (folder / "eHRMS" / "Leave.asp").write_text("x", encoding="utf-8")
        (folder / "eHRMS" / "Card.asp").write_text("x", encoding="utf-8")
        (folder / "SA" / "spec.docx").write_text("x", encoding="utf-8")
        (folder / "eHRMS" / "ERRORLOG").mkdir()
        (folder / "eHRMS" / "ERRORLOG" / "leave_error.txt").write_text("x", encoding="utf-8")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-m", "custom")
        _git(self.repo, "checkout", "main")

        custom_git._cache.update(dir=None, at=0.0, branches=None)
        patches = [
            mock.patch.dict(os.environ, {"CUSTOM_GIT_DIR": str(self.repo)}),
            mock.patch.object(custom_core.mdb, "db_enabled", return_value=True),
            mock.patch.object(custom_core.sa_db, "find_docs", return_value=[]),
            mock.patch.object(custom_core.db, "find_files", return_value=[]),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_tax_id_lists_program_files_without_sa_or_root_files(self):
        out = custom_core.lookup("12345678")
        self.assertIn(BRANCH, out)
        self.assertIn("eHRMS/Leave.asp", out)
        self.assertNotIn("README.md", out)
        self.assertNotIn("SA/spec.docx", out)
        self.assertNotIn("leave_error.txt", out)
        self.assertIn("分支檔案 2 個", out)
        self.assertIn("分支內有 1 份，尚未分析", out)
        self.assertIn("不代表都是客製", out)

    def test_company_name_and_brackets_match(self):
        self.assertIn(BRANCH, custom_core.lookup("【測試公司】"))

    def test_path_keyword_filters_files(self):
        out = custom_core.lookup("12345678", "leave")
        self.assertIn("Leave.asp", out)
        self.assertNotIn("Card.asp", out)
        self.assertIn("沒有路徑符合", custom_core.lookup("12345678", "nothing"))

    def test_lookup_does_not_switch_branch(self):
        custom_core.lookup("12345678")
        head = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "--abbrev-ref", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(head, "main")

    def test_not_found_does_not_claim_standard_version(self):
        out = custom_core.lookup("99999999")
        self.assertIn("查無", out)
        self.assertIn("可能是標準版", out)

    def test_unreadable_repo_says_cannot_judge(self):
        with mock.patch.dict(os.environ, {"CUSTOM_GIT_DIR": str(self.repo / "missing")}):
            out = custom_core.lookup("99999999")
        self.assertIn("目前無法判斷", out)
        self.assertNotIn("標準版", out)

    def test_missing_table_is_noted_and_git_still_used(self):
        error = RuntimeError("('42S02', '無效的物件名稱 HRMS_CUSTOM')")
        with mock.patch.object(custom_core.db, "find_files", side_effect=error):
            out = custom_core.lookup("12345678")
        self.assertIn("沒有 HRMS_CUSTOM 表", out)
        self.assertIn("eHRMS/Leave.asp", out)

    def test_analyzed_rows_replace_git_list_and_hide_false_positives(self):
        rows = [_file_row("x/Real.cls", "standard", "改了計算"), _file_row("x/Plugin.asp", "pure"),
                _file_row("x/Old.cls", "version_lag"), _file_row("x/Dep.bas", "std_attached")]
        with mock.patch.object(custom_core.db, "find_files", return_value=rows):
            out = custom_core.lookup("12345678")
        self.assertIn("真客製 2 個，版本落後 1 個，標準附屬 1 個", out)
        self.assertIn("[標準客製] x/Real.cls：改了計算", out)
        self.assertNotIn("x/Old.cls", out)
        self.assertNotIn("x/Dep.bas", out)
        self.assertNotIn("eHRMS/Leave.asp", out)

    def test_sa_docs_show_summary_only_when_analyzed(self):
        docs = [_doc_row("SA/spec.docx", True, "假勤客製規格"), _doc_row("SA/old.docx", False)]
        with mock.patch.object(custom_core.sa_db, "find_docs", return_value=docs):
            out = custom_core.lookup("12345678")
        self.assertIn("SA 文件 2 份（已分析 1 份）", out)
        self.assertIn("SA/spec.docx：假勤客製規格", out)
        self.assertIn("SA/old.docx（未分析）", out)

    def test_db_only_branch_is_reported_when_git_unavailable(self):
        rows = [_file_row("x/Real.cls", "standard")]
        with mock.patch.dict(os.environ, {"CUSTOM_GIT_DIR": str(self.repo / "missing")}), \
                mock.patch.object(custom_core.db, "find_files", return_value=rows):
            out = custom_core.lookup("12345678")
        self.assertIn("x/Real.cls", out)
        self.assertIn("讀不到 CUSTOM_GIT", out)

    def test_empty_keyword(self):
        self.assertIn("請提供", custom_core.lookup("  "))

    def test_like_treats_wildcards_literally(self):
        self.assertEqual(custom_db.like_pattern("a_b%c[d"), "%a[_]b[%]c[[]d%")

    def test_branch_files_rejects_unknown_branch(self):
        self.assertIsNone(custom_git.branch_files("--output=x"))


if __name__ == "__main__":
    unittest.main()
