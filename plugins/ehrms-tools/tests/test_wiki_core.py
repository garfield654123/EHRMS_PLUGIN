# -*- coding: utf-8 -*-
"""wiki_core 與 sop 查詢的單元測試（不連資料庫）。

以 fixtures/wiki_card.md（〈打卡〉範例頁，新模板）測頁面解析、記憶編號換新、
搜尋與章節讀取；資料庫存取以替身取代。
"""
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "servers" / "MEMORY"))

import refdata_core  # noqa: E402
import wiki_core  # noqa: E402

CARD = (Path(__file__).resolve().parent / "fixtures" / "wiki_card.md").read_text(encoding="utf-8")

OLD_FORMAT = """# 刷卡比對規則

> **來源**：`raw/card-match.md`（2026-02-24 編譯）
> **標籤**：`刷卡` `比對`

## 摘要

刷卡比對根據員工刷卡資料與班別設定，自動判定出勤狀態。

## 出勤異常類型

應刷未刷、遲到、早退、曠職。
"""


def _row(content, **kw):
    row = {"wiki_id": 1, "git_path": "wiki/出勤管理/打卡.md", "doc_type": "article",
           "topic_code": "CARD", "customer_code": "", "title": "打卡（刷卡）",
           "keywords": "打卡,刷卡,刷卡異常", "description": "", "content": content,
           "git_commit": "draft", "open_review_count": 0}
    row.update(kw)
    return row


class ParsePageTests(unittest.TestCase):
    def setUp(self):
        self.p = wiki_core.parse_page(CARD)

    def test_front_matter(self):
        self.assertEqual(self.p["meta"]["menu_paths"],
                         ["出勤管理›出勤刷卡", "出勤管理›手機打卡", "出勤管理›員工卡號管理"])
        self.assertTrue(self.p["summary"].startswith("員工打卡資料從哪裡來"))
        self.assertFalse(self.p["body"].startswith("---"))

    def test_rules_and_memory_ids(self):
        rules = {r["id"]: r for r in self.p["rules"]}
        self.assertEqual(len(rules), 10)
        self.assertTrue(rules["R7"]["title"].startswith("已經請假或申請加班"))
        self.assertEqual(rules["R7"]["memory_ids"], [98])          # 不含 FAQ 編號
        self.assertEqual(rules["R2"]["memory_ids"], [106, 110])

    def test_halves_and_sections(self):
        self.assertIn("## 規則", self.p["halves"]["上半"])
        self.assertNotIn("## 程式地圖", self.p["halves"]["上半"])
        self.assertIn("## 程式地圖", self.p["halves"]["下半"])
        titles = [s["title"] for s in self.p["sections"]]
        self.assertIn("關鍵資料表", titles)
        self.assertNotIn("適用情境", titles)                         # code block 內的標題不算

    def test_old_format_page(self):
        p = wiki_core.parse_page(OLD_FORMAT)
        self.assertEqual(p["rules"], [])
        self.assertTrue(p["summary"].startswith("刷卡比對根據"))
        self.assertEqual(p["halves"]["上半"], p["halves"]["下半"])

    def test_memory_id_range(self):
        self.assertEqual(wiki_core._memory_ids("記憶 #92–#95、#134"), [92, 93, 94, 95, 134])


class ResolveMemoryTests(unittest.TestCase):
    def test_follows_supersede_chain_and_ignores_rejected(self):
        rows = [
            {"id": 160, "supersedes": 98, "review": "pending", "meta": {}},
            {"id": 170, "supersedes": 160, "review": "pending", "meta": {}},
            {"id": 180, "supersedes": 110, "review": "rejected", "meta": {}},
            {"id": 190, "supersedes": None, "review": "pending",
             "meta": {"supersedes_all": ["106"]}},
        ]
        self.assertEqual(wiki_core.resolve_memory_ids([98, 106, 110, 97], rows),
                         {98: 170, 106: 190})


class LookupTests(unittest.TestCase):
    def setUp(self):
        rows = [_row(CARD),
                _row(OLD_FORMAT, wiki_id=2, git_path="wiki/出勤管理/刷卡比對規則.md",
                     topic_code="", title="刷卡比對規則", keywords="刷卡,比對"),
                _row("# 玉山銀行轉帳匯出\n\n## 摘要\n\n薪資轉帳匯出。", wiki_id=3,
                     git_path="wiki/客製/12666487_ATPINC_華騰國際/玉山銀行轉帳匯出.md",
                     topic_code="", customer_code="12666487", title="玉山銀行轉帳匯出",
                     keywords="玉山銀行,轉帳匯出")]
        patches = [
            mock.patch.object(wiki_core.db, "db_enabled", return_value=True),
            mock.patch.object(wiki_core.db, "load_all", return_value=rows),
            mock.patch.object(wiki_core.mdb, "load_all", return_value=[
                {"id": 160, "supersedes": 98, "review": "pending", "meta": {}}]),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_search_hits_rules(self):
        out = wiki_core.lookup(query="請假後刷卡異常沒有消失")
        self.assertIn("打卡（刷卡）〔CARD〕", out)
        self.assertIn("R7", out)
        self.assertIn("#98 → #160", out)
        self.assertIn("選單：出勤管理›出勤刷卡", out)

    def test_read_rule_section(self):
        out = wiki_core.lookup(page="card", section="r8")
        self.assertIn("### R8：重新比對時，人工處理過的異常會保留", out)
        self.assertNotIn("### R9", out)

    def test_read_named_section_and_toc(self):
        self.assertIn("## 程式地圖", wiki_core.lookup(page="打卡（刷卡）", section="程式地圖"))
        toc = wiki_core.lookup(page="wiki/出勤管理/打卡.md")
        self.assertIn("- R10：員工補填超時出勤原因可能有期限", toc)

    def test_old_format_by_title(self):
        out = wiki_core.lookup(page="刷卡比對規則", section="出勤異常類型")
        self.assertIn("應刷未刷", out)

    def test_custom_pages_need_customer_code(self):
        self.assertIn("找不到頁面", wiki_core.lookup(page="玉山銀行轉帳匯出"))
        out = wiki_core.lookup(page="玉山銀行轉帳匯出", customer_code="12666487")
        self.assertIn("（客製 12666487）", out)

    def test_unknown_rule(self):
        self.assertIn("沒有 R99", wiki_core.lookup(page="CARD", section="R99"))


class SopLookupTests(unittest.TestCase):
    def setUp(self):
        rows = [
            {"id": 1, "title": "計算假勤／加班時數有誤", "description": "處理時數問題",
             "content": "# 計算假勤／加班時數有誤\n\n## 需要客戶補的資料\n1. 行事曆設定",
             "category": "M1", "keywords": "假勤,加班,時數"},
            {"id": 2, "title": "客戶要求解鎖假勤", "description": "解鎖前先確認是否已計薪",
             "content": "## 前置確認\n1. 是否已計薪\n\n## 處理方式\n- 加班：Task #221",
             "category": "M1", "keywords": "解鎖,已計薪,假勤"},
        ]
        patches = [mock.patch.object(refdata_core.db, "db_enabled", return_value=True),
                   mock.patch.object(refdata_core.db, "load_all", return_value=rows)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_returns_full_content(self):
        out = refdata_core.lookup("sop", "客戶要解鎖已計薪的假勤")
        self.assertIn("客戶要求解鎖假勤", out)
        self.assertIn("Task #221", out)

    def test_category_filter(self):
        out = refdata_core.lookup("sop", "加班時數算錯", category="M2")
        self.assertIn("無相符SOP", out)


if __name__ == "__main__":
    unittest.main()
