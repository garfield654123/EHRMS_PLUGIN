---
name: custom-sa-analyze
description: 深度解析單一客製分支的 SA 規格文件，摘要客製邏輯並與實際程式碼交叉驗證，寫入 HRMS_CUSTOM_SA。由 headless 批次呼叫，非互動使用；一次只處理一個指定分支（分支名稱由呼叫端 prompt 指定）。備份版本——正本在 CUSTOM_GIT/.claude/skills/custom-sa-analyze（該 repo 無穩定分支可版控，這裡是唯一有 git 歷史的副本）。轉檔輔助腳本 doc_to_pdf.py 備份在同目錄 scripts/。
---

# 客製 SA 規格深度分析

輸入：一個 CUSTOM_GIT 分支名稱（由呼叫端 prompt 指定，不主動詢問使用者——這是 headless 批次流程，無人在場確認）。

## 核心原則

1. **唯讀分析，不修改任何檔案**（比照 custom-locate）
2. **完成或失敗都要切回原分支**（CUSTOM_GIT 是共用工作目錄，任何人或排程都可能同時在用）
3. **拿不準就標記 `mapping_status=partial`，不硬猜**——這是一次性建檔，寧可留白等人工，不寫錯資料
4. **每份文件處理完就立刻寫入 DB**（不要全部分析完才一次寫，中途失敗才不會全部流失）

## 步驟 1：安全切換分支

同 custom-locate 步驟2：

1. `git -C C:/D/CUSTOM_GIT status --porcelain --untracked-files=no`，若有輸出（未提交變更）→ 停下回報，不強制切換
2. `git -C C:/D/CUSTOM_GIT rev-parse --abbrev-ref HEAD` 記錄原始分支；若結果是 `HEAD`（detached），改記 `git rev-parse HEAD` 的 commit SHA
3. `git -C C:/D/CUSTOM_GIT checkout <目標分支>`

## 步驟 2：列出 SA 文件

列出 `{分支}/SA/` 下所有 `.doc/.docx/.pdf/.xls/.xlsx`（含 `SA/old/` 等子目錄）。

## 步驟 3：逐檔分類（骨架）

對每份文件：

- `DOC_TYPE`：檔名含「規格書」→ `spec`；「確認單」→ `confirm`；「驗收單」→ `accept`；「安裝單」→ `install`；都不含 → `other`
- `VERSION_LABEL`：檔名正則擷取 `V\d+(\.\d+)?` 樣式版本號，擷取不到留空
- 同 base name（去除版號/空格差異後）群組，群組內版號最高者 `IS_LATEST=1`，其餘為 `0`

## 步驟 4：深度分析（僅 `DOC_TYPE=spec` 且 `IS_LATEST=1` 的文件）

其餘類型（confirm/accept/install）與非最新版，只做步驟3的骨架分類，**不進入本步驟**（省 token，這些文件價值低或是歷史存檔）。

對每份需深度分析的規格書：

1. **Read 工具無法直接讀取 Word/Excel 格式**（`.doc`/`.docx`/`.xls`/`.xlsx` 皆然，不是只有舊版 `.doc`）。讀取前先用 PowerShell（不要用 Git Bash／MSYS，中文路徑 argv 會被錯誤解碼導致「找不到檔案」）呼叫轉檔工具，轉成 PDF 後再用 Read 工具讀：
   ```powershell
   py C:\D\CUSTOM_GIT\.claude\scripts\doc_to_pdf.py "<文件完整路徑>"
   ```
   成功會印出轉檔後的 PDF 路徑（在系統暫存目錄，不影響原始檔案），用 Read 工具讀那個路徑。
   若轉檔失敗（例如檔案損毀、COM 元件無法啟動），不要跳過不記錄——寫入 `parse_issue="轉檔失敗：<錯誤訊息>"`，`analyzed=0`
2. 摘要：這份規格在講哪個功能、客製了什麼邏輯，寫給後續查案的人看（完整敘述，不是給機器解析用的結構化片段）
3. 從摘要內容找線索（客戶代號、功能關鍵字、提到的欄位/表格名稱）
4. 到分支的程式碼裡搜尋比對：Grep 客製 ProgID／`ZZ_` 前綴／功能關鍵字，找出實際實作這段邏輯的檔案
5. 讀取候選檔案，確認邏輯是否真的對得上規格書描述（**不是只憑檔名/關鍵字命中就當作對應**——這是「驗證」，要真的讀程式碼比對）
6. 判定 `MAPPING_STATUS`：
   - `mapped`：找到檔案且邏輯內容確實對應
   - `partial`：找到疑似檔案但無法完全驗證邏輯一致
   - `unmapped`：規格書內容找不到對應的實際程式碼（可能已還原標準版、或客製已移除）

## 步驟 5：寫入資料庫

每處理完一份文件，立刻呼叫 `custom_sa_log`（ehrms-memory MCP）寫入一筆，欄位對應：

```
custom_sa_log(
  tax_id, branch_name, doc_path, doc_format, doc_type,
  version_label, is_latest, analyzed, summary, mapped_paths,
  mapping_status, parse_issue
)
```

`mapped_paths` 一行一筆，repo 相對路徑（同 `remember`/`jira_log` 慣例，不寫本機絕對路徑）。

## 步驟 6：切回原分支

無論成功或中途失敗，都要切回步驟1記錄的原分支/commit，並在最終輸出註明「已切回：{原分支}」。

## 步驟 7：回報

輸出簡短摘要，**明確區分「嘗試深度分析」跟「實際成功分析」**，不要把兩者混著講（例如不要寫「深度分析了 X 份規格書」卻沒說清楚其中有幾份其實 `parse_issue` 卡住、`analyzed` 仍是 0——回報文字要跟 DB 裡的實際狀態一致，不能報喜不報憂）：
- 處理文件總數、骨架分類數
- 嘗試深度分析數 vs 實際成功（`analyzed=1`）數
- 成功的部分：mapped/partial/unmapped 各幾份
- 失敗的部分：`parse_issue` 各是什麼原因、幾份

供 orchestrator 判斷這個分支是否算完成。

## 注意事項

- 只查詢與比對，不修改任何檔案
- CUSTOM_GIT 是共用工作目錄，`git status` 若非乾淨狀態要停下確認，不覆蓋別人的變更
- 客戶統編/分支名稱對照以 `custom-compare/SKILL.md` 的先鋒客戶名單為準，本 skill 不重複維護
