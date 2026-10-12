---
name: package-files
description: 把修改過的 EHRMS 程式檔案打包成客戶端可以直接覆蓋更新的 zip（依照正確的相對路徑放置）。Use when the user asks to "包檔案", "打包程式", "包版本給客戶", "產生更新檔", "package files for customer update", or wants to bundle changed EHRMS files (asp/sql/dll/等) into a deployable zip after a fix is done.
---

執行環境：Claude Code 與 Codex 共用；工具名稱與檔案讀取方式依 [平台工具對照](../../references/platform-runtime.md) 選擇。

# EHRMS 包檔案

把修改過的檔案打包成一個 zip，解壓縮到客戶站台後，每個檔案會自動落在正確的相對路徑上（覆蓋更新用）。邏輯參考 `C:\D\file_upload_classify`（內部「標準包檔案」小工具）的規則重建，**不依賴、也不啟動那個工具**，純粹用它維護的對照表資料。

## 前置概念

EHRMS 是地端部署系統，客戶站台的目錄結構跟 `EHRMS_GIT` 這個 repo 的原始碼結構**大致一致**，但有例外：

| 檔案類型 | 客戶站台路徑 vs repo 原始碼路徑 |
|---|---|
| `.asp` / `.htm` / `.js` / `.css` / 圖片等純文字或靜態檔 | **完全一致**，就是 repo 裡的相對路徑（`EHRMS\...`、`EHRMS\ehrPortal\...`、`EHRMS\ehrPortalAdmin\...`） |
| `.sql` | 不管原本在哪，**一律攤平放到 `SQL\` 資料夾**（客戶端 SQL 是集中執行的，不會放進站台目錄） |
| `.dll` | **不是**照 `VB\EHRMS\...` 這種原始碼專案路徑，而是編譯後攤平放到 `DLL\EHRMS\<檔名>.dll`（DLL 需要使用者自己先在 VB6/​.NET 專案編譯出來，這個 skill 不負責編譯，只負責告訴你編譯完的 DLL 該放哪） |

對照表資料放在 `C:\D\file_upload_classify\server\reference_file\`：
- **`file_list.csv`**（欄位：`path,filename`）— 全產品「檔名 → 相對資料夾」對照表，12,000+ 筆
- **`repeated_files.csv`**（欄位同上）— 同檔名出現在多個資料夾的清單（例如 `Search_List.asp` 出現在 40 個模組資料夾、`topWindowS.asp` 出現在 5 個站台）

> ⚠️ 這兩份 CSV 很大，**不要用 Read 整份讀進來**。用 Grep 針對檔名查詢即可（見下方步驟）。
> 這份參考資料**不會**被複製進 `EHRMS_PLUGIN` 這個 plugin repo（該 repo 準備公開，且 `file_upload_classify` 底下另有 `customer.csv`/`branch.csv` 等含真實客戶名稱/統編的檔案，絕對不能進公開 repo）。若 `C:\D\file_upload_classify` 不存在或已搬移，先跟使用者確認新位置，不要憑空捏造路徑判斷結果。

## 執行流程

### 步驟 1：確認要打包的檔案清單

依情境判斷來源，不確定就直接問使用者：
- **剛在 `EHRMS_GIT` 改完程式**（例如接續一次 bug fix 對話）：用 `git diff --name-only <base>...<branch>` 或 `git status --porcelain` 取得異動檔案的 **repo 相對路徑**（這是最可靠的來源，不需要再去查 CSV，見步驟 2 的「已知路徑」情境）
- **使用者直接列出檔名／給一個資料夾**：用 Glob/Bash 列出實際檔案

### 步驟 2：逐一解析目標路徑

對清單中每個檔案，依優先順序判斷：

1. **`.sql` 檔** → 目標路徑固定為 `SQL\<檔名>`，不用查表。
2. **已知完整 repo 相對路徑的一般檔案**（`.asp`/`.htm`/`.js`/`.css`/圖片等，且你已經知道它在 `EHRMS_GIT` 裡的實際路徑，例如是這次對話剛改過的檔案）→ **直接拿 repo 相對路徑當目標路徑**（`/` 換成 `\`），**不要**再用檔名去查 `file_list.csv` 猜路徑。
   - 理由：`file_list.csv` 是純檔名對照表，遇到同檔名分散在多資料夾的情況（見下方 topWindowS.asp 範例）會有歧義，但我們已經確切知道是哪一份，直接用即可，比查表更準。
3. **只有裸檔名、不知道原始路徑**（例如使用者直接丟一個編譯好的 `.dll`，或是給一個沒有上下文的檔名）→ 查表：
   - 用 `Grep pattern:"<檔名>" path:"C:\D\file_upload_classify\server\reference_file\file_list.csv"` 找出所有符合的列
   - **只有 1 筆結果** → 該筆的 `path` 欄位就是目標資料夾
   - **超過 1 筆結果（同檔名多個路徑）** → 停下來，把所有候選路徑列給使用者，請使用者指定是哪一個，**不要自己猜**（這正是 `repeated_files.csv` 存在的原因）
   - **0 筆結果** → 這個檔案在對照表裡找不到（可能是全新檔案、或對照表沒更新到），停下來請使用者確認正確的目標路徑，不要憑空捏造

**範例（實際驗證過）**：`topWindowS.asp` 在 `file_list.csv` 裡有 5 筆對應到 5 個不同資料夾（`EHRMS\function`、`EHRMS\ehrPortal\function`、`EHRMS\ehrPortal\function_CN`、`EHRMS\ehrPortal\function_EN`、`EHRMS\ehrPortalAdmin\function`）。如果是從這次修 XSS 的對話延續下來，這 5 份的路徑我們早就知道了（步驟 2 規則 2），直接套用；如果是使用者事後才丟一個裸檔名 `topWindowS.asp` 進來，就會落入規則 3 的「多筆結果」情境，必須反問使用者是哪一份。

### 步驟 3：整理成 manifest 給使用者確認

打包前，先列出「檔案 → 目標路徑」對照表讓使用者過目，特別標記任何用規則 3（查表）解出來的項目：

```
| 來源檔案 | 目標路徑（zip 內） | 判斷方式 |
|---|---|---|
| EHRMS/password_change.asp | EHRMS\password_change.asp | 已知路徑 |
| EHRMS/function/topWindowS.asp | EHRMS\function\topWindowS.asp | 已知路徑 |
| Payroll.dll | DLL\EHRMS\Payroll.dll | 查表（file_list.csv 單一結果）|
```

### 步驟 4：建立暫存資料夾、複製檔案、壓縮

用 Bash/PowerShell：
1. 建立一個暫存資料夾（放在 scratchpad，例如 `<scratchpad>\package_<單號或日期>\`）
2. 依 manifest 把每個檔案複製到暫存資料夾內對應的相對路徑（資料夾不存在就建立）
3. 壓縮成 zip，檔名慣例：**有 Jira 單號就用單號**（例如 `EHRMSONE-32560_20260722.zip`），沒有就用日期（`20260722_upload.zip`）
4. 輸出位置**先問使用者**要放哪裡（預設可放 scratchpad 或使用者指定的交付資料夾），不要自己假設

### 步驟 5：交付前提醒

- 再次列出 manifest 給使用者做最後確認（尤其是查表解出來、且原本是「多筆結果」被使用者手動指定的項目）
- 若打包內容包含 `.dll`，提醒使用者：**這個 skill 不負責編譯**，`.dll` 必須是使用者已經編譯好、且是最新版本的檔案
- 若使用者提到「這個客戶有自己的客製化分支」（branch 名稱通常是 `統編_英文簡稱_中文名` 格式），目前這個 skill **只支援標準版邏輯**（用產品標準結構查路徑），客製分支的檔案路徑可能跟標準結構不同，需要人工核對，不要直接套用標準版查表結果

## 注意事項

- `file_list.csv`／`repeated_files.csv` 是 `file_upload_classify` 工具維護的快照，可能有新檔案還沒收錄進去（規則 3 的「0 筆結果」就是這種情況），這不代表檔案不該存在，只代表對照表要更新，請使用者確認正確路徑後繼續，不要因為查不到就假設檔案有問題
- 絕對不要把 `C:\D\file_upload_classify` 底下的 `customer.csv`、`branch.csv`、`customer_branch_list.csv`、`customer_sno_ename_cname.csv`、`Total_customer_file_list.csv` 等含客戶名稱/統編的檔案複製、貼到任何輸出、或寫進任何會進 `EHRMS_PLUGIN` repo 的地方
