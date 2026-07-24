---
name: custom-file-analyze
description: 兩階段深度掃描單一客製分支的所有客製檔案（不依賴 SA 規格書），判定標準客製/純客製/版本落後/標準附屬，詳細記錄客製了什麼邏輯，並嘗試比對 HRMS_CUSTOM_SA 找對應規格書。寫入 HRMS_CUSTOM。由 headless 批次呼叫，非互動使用；一次只處理一個指定分支。備份版本——正本在 CUSTOM_GIT/.claude/skills/custom-file-analyze（該 repo 無穩定分支可版控，這裡是唯一有 git 歷史的副本）。
---

# 客製檔案深度分析（兩階段掃描）

輸入：一個 CUSTOM_GIT 分支名稱（由呼叫端 prompt 指定，不主動詢問使用者——這是 headless 批次流程，無人在場確認）。

## 核心原則

1. **唯讀分析，不修改任何檔案**（比照 custom-locate）
2. **完成或失敗都要切回原分支**（CUSTOM_GIT 是共用工作目錄，任何人或排程都可能同時在用）
3. **不依賴 SA 規格書**：SA 是增補，不是判斷依據——即使沒有規格書、或規格書遺失，也要靠程式碼本身（含跟標準版比對）正確判斷客製與否、客製了什麼
4. **候選檔案要用規則篩，不是無差別讀全部檔案**：分支可能有上百個檔案，全部深讀不可行，先用便宜規則篩出候選再深讀
5. **每個檔案處理完就立刻寫入 DB**（不要全部分析完才一次寫）

## ⚠️ STD_BASELINE（標準版比對基準，每半年更新）

**目前使用：`202607_000`**（EHRMS_GIT 分支）。此基準約每半年會換版，執行前先確認這個值是否還是最新——過期的話跟使用者確認新基準再繼續，不要憑印象沿用舊值。

## 步驟 1：安全切換分支

同 custom-locate 步驟2（`git status` 確認乾淨、記錄原始分支/commit、checkout 目標分支）。

## 步驟 2：階段一—候選客製檔案篩選（便宜、全面）

規則沿用 `custom-scan/SKILL.md` 的「客製標記識別規則」，本 skill 不重複維護，只讀取引用：

1. **`ZZ_` 前綴**（函式級客製）：Grep 分支下所有 `.asp`/`.cls`/`.bas` 檔案，找 `ZZ_` 出現的位置與所在函式
2. **非標準 ProgID**（元件級客製）：`.asp` 中 `Server.CreateObject` 參數非標準命名（不是 `Payroll.*`／`Personnel.*`／`Attendance.*` 等標準 DLL）
3. **客製前綴 DLL/資料夾**：整支 `.vbp` 專案或資料夾名稱本身是 `{客戶代號}_{模組}` 格式（例如 `SKHB_Personnel_SpecialLeave`），整個元件視為候選

三者命中的檔案去重合併成候選清單。**這階段不深讀，純規則掃描。**

## 步驟 3：階段二—逐檔深讀分析

對候選清單每個檔案：

1. **判斷 STANDARD_PATH**：依 `custom-compare/SKILL.md` 的路徑對應規則（`eHRMS/VB/` ↔ `/VB/EHRMS/`、`eHRMS/Webpage/` ↔ `/EHRMS/`，排除 `eHRMS/Webpage/plugin/`）推算標準版對應路徑；推不出來或標準版無此檔（純客製常見於 Plugin 目錄）→ `STANDARD_PATH` 留空
2. **Word/Excel 格式無關**（本步驟只讀程式碼，不會遇到 Read 工具讀不了的問題）
3. **有 STANDARD_PATH**：讀客製版 + 讀 `EHRMS_GIT` 於 `STD_BASELINE` 分支的對應檔案，語意比對差異。**⚠️ 一律用 Read 工具讀取比對內容，不要用 shell `diff` 直接比對檔案**——`.cls`/`.bas`/`.asp` 通常是 Big5 編碼，shell `diff` 有時會把高位元組誤判成二進位檔而中途放棄比對（實測過，`modCommon.bas` 就踩到這個坑，比對可能不完整卻不會報錯，容易誤判）。不是純文字 diff，客製版常見格式/欄位順序跑掉，要看語意：
   - 差異確實是刻意的客戶邏輯調整（新增條件判斷、改變計算方式、新增欄位等） → `CUSTOM_TYPE=standard`
   - 檔案幾乎整份跟標準版一致，只是缺了標準版後續才加的功能/修正（看得出「曾經一致、後來沒跟上」的痕跡，例如標準版有明確的 `modify by XXX for [單號]` 註解而客製版沒有），且**找不到任何客製版獨有的邏輯** → `CUSTOM_TYPE=version_lag`
   - 判斷不出是「曾經客製過但沒跟上」還是「單純陪著模組資料夾一起被複製、從未被改過」時，優先看是否有 `>`（客製版獨有）內容：完全沒有客製版獨有內容 → 傾向 `std_attached`；不確定就用 `version_lag`（較保守的標籤，不代表「保證沒客製過」）
4. **無 STANDARD_PATH**（含 Plugin 目錄下的獨立元件、找不到標準對應）→ `CUSTOM_TYPE=pure`
5. **`std_attached`（標準附屬）判斷**：這個分類是給「整個模組資料夾被複製出去獨立編譯（例如 `RC_Personnel` 是 `Personnel` 模組整包複製），資料夾裡有些檔案真的被改過，但也有些檔案只是陪著一起複製、從未被改過、純粹是為了讓元件能編譯而存在」這種情況。判斷依據：跟標準版比對後**完全沒有任何客製版獨有的內容**（不是「缺東西」，是「一行客製邏輯都沒有」），可以合理推測是誤上/陪同複製，不是真的客製意圖 → `CUSTOM_TYPE=std_attached`。跟 `version_lag` 的差別：`version_lag` 隱含「這裡曾經走過客製流程但沒跟上」，`std_attached` 隱含「這裡從來就不打算客製，只是編譯需要」——實務上兩者有時很難精確分辨，判斷不確定時兩者皆可接受，不用強求完美區分
6. **寫 DESCRIPTION**（詳細，比照 custom-sa-analyze 步驟4.2 的摘要規範——具體寫客製了什麼邏輯、差異在哪，不要籠統帶過；`version_lag`/`std_attached` 可簡短或留空，但建議簡短註明判斷依據，如「僅缺標準版 EHRMSONE-25105 後續修正，無客製版獨有邏輯」）
7. **嘗試比對 HRMS_CUSTOM_SA**：用 DB MCP 查 `SELECT DOC_PATH, SUMMARY, MAPPED_PATHS FROM dbo.HRMS_CUSTOM_SA WHERE BRANCH_NAME='<分支>'`，看有沒有文件的 `SUMMARY`／既有 `MAPPED_PATHS` 內容跟這個檔案的客製邏輯對得上（關鍵字、功能描述比對，不是機械式字串比對）。找到 → 記 `sa_doc_path`；找不到 → 留空，不強湊

## 步驟 4：寫入資料庫

每處理完一個檔案，立刻呼叫 `custom_log`（ehrms-memory MCP）：

```
custom_log(
  company_sno, branch_name, custom_path, custom_type, std_baseline,
  standard_path, description, sa_doc_path, source, branch_commit
)
```

`sa_doc_path` 有給值時，`custom_log` 會自動回填該筆 `HRMS_CUSTOM_SA.MAPPED_PATHS`（雙向關聯，不用另外呼叫 `custom_sa_log`）。

`custom_path`/`standard_path` 用 repo 相對路徑（同 `remember`/`jira_log` 慣例，客製 repo 保留 `CUSTOM_GIT/<分支>/` 前綴）。

## 步驟 5：切回原分支

無論成功或中途失敗，都要切回步驟1記錄的原分支/commit，並在最終輸出註明「已切回：{原分支}」。

## 步驟 6：回報

**明確區分「候選數」「嘗試深讀數」「實際成功寫入數」**，不要混著講：
- 階段一候選檔案總數
- 階段二嘗試深讀數 vs 實際成功寫入（`custom_log` 回傳成功）數
- 依 `CUSTOM_TYPE` 分類統計：standard / pure / version_lag / std_attached 各幾個
- 找到 SA 對應文件並成功回填的有幾個
- 失敗/略過的檔案跟原因

供 orchestrator 判斷這個分支是否算完成。

## 注意事項

- 只查詢與比對，不修改任何檔案
- CUSTOM_GIT 是共用工作目錄，`git status` 若非乾淨狀態要停下確認，不覆蓋別人的變更
- 客戶統編/分支名稱對照、路徑對應規則、客製標記識別規則以 `custom-compare`/`custom-scan` 的內容為權威來源，本 skill 不重複維護
- 跟 `custom-sa-analyze` 是互補關係：那個 skill 從「規格書」出發找程式碼；本 skill 從「程式碼」出發找規格書，兩邊各自獨立掃描、透過 `sa_doc_path`/`MAPPED_PATHS` 互相回填串起來
