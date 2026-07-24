# -*- coding: utf-8 -*-
"""doc_to_pdf.py -- 用 Office COM 自動化把 Word(.doc/.docx)／Excel(.xls/.xlsx) 轉成 PDF，
供 Read 工具讀取。

背景：Read 工具完全不支援 Word/Excel 格式（新舊版本皆然，.doc/.docx/.xls/.xlsx 都讀不了），
只支援純文字／PDF／圖片。轉純文字會遺失內嵌圖片/版面，PDF 才能保留完整內容給 Read 工具讀。

唯讀轉檔：以 ReadOnly 開啟原始檔案，另存新檔到系統暫存目錄，不寫回、不修改原始檔案
（呼應 custom-sa-analyze skill「唯讀分析，不修改任何檔案」的原則）。

用法：
    py doc_to_pdf.py "C:/D/CUSTOM_GIT/23019591_Skhb_新壽/SA/新壽-客制規格書V1.0(特休假).doc"

成功時印出轉檔後的 .pdf 路徑（單行，方便呼叫端擷取）；失敗時印出 ERROR: 訊息並以非 0 結束碼結束。

⚠️ 若在 Git Bash / MSYS 環境下呼叫且路徑含中文，argv 可能被錯誤解碼（mojibake）導致
「找不到檔案」——請改用 PowerShell 呼叫，或確認呼叫端已正確傳遞 UTF-8 引數。
"""
import sys
import tempfile
from pathlib import Path

WORD_EXTS = {".doc", ".docx"}
EXCEL_EXTS = {".xls", ".xlsx"}


def _convert_word(src: Path, out_path: Path):
    import win32com.client
    word = win32com.client.DispatchEx("Word.Application")
    word.Visible = False
    word.DisplayAlerts = 0  # wdAlertsNone
    try:
        doc = word.Documents.Open(str(src), ReadOnly=True)
        try:
            doc.ExportAsFixedFormat(str(out_path), ExportFormat=17)  # 17 = wdExportFormatPDF
        finally:
            doc.Close(False)  # SaveChanges=False，原始檔案保證不被修改
    finally:
        word.Quit()


def _convert_excel(src: Path, out_path: Path):
    import win32com.client
    excel = win32com.client.DispatchEx("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    try:
        wb = excel.Workbooks.Open(str(src), ReadOnly=True)
        try:
            wb.ExportAsFixedFormat(0, str(out_path))  # 0 = xlTypePDF
        finally:
            wb.Close(False)  # SaveChanges=False
    finally:
        excel.Quit()


def convert(doc_path: str) -> str:
    src = Path(doc_path).resolve()
    if not src.is_file():
        raise FileNotFoundError(f"找不到檔案：{src}")

    ext = src.suffix.lower()
    if ext not in WORD_EXTS and ext not in EXCEL_EXTS:
        raise ValueError(f"不支援的副檔名：{ext}（僅支援 .doc/.docx/.xls/.xlsx）")

    out_dir = Path(tempfile.gettempdir()) / "custom_sa_doc_convert"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / (src.stem + ".pdf")

    if ext in WORD_EXTS:
        _convert_word(src, out_path)
    else:
        _convert_excel(src, out_path)

    return str(out_path)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("ERROR: 用法：py doc_to_pdf.py <.doc/.docx/.xls/.xlsx 檔案路徑>", file=sys.stderr)
        sys.exit(1)
    try:
        result_path = convert(sys.argv[1])
        print(result_path)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
