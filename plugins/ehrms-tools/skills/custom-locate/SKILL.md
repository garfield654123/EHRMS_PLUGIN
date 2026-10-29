---
name: custom-locate
description: 查詢特定客戶在 CUSTOM_GIT 的客製化程式碼，定位客製元件並與標準版（EHRMS_GIT）比對邏輯差異。Use when a customer-reported bug can't be explained by standard EHRMS_GIT code alone, when the user names a specific customer or mentions 客製/客戶客製, or when debug/bug-locate needs to check whether a customer runs a customized component instead of the standard one.
---

執行環境：Claude Code 與 Codex 共用；工具名稱與檔案讀取方式依 [平台工具對照](../../references/platform-runtime.md) 選擇。
客製程式位於 CUSTOM_GIT（預設 C:/D/CUSTOM_GIT），標準版位於 EHRMS_GIT。

# EHRMS 客製元件定位

在 debug 客訴時，快速找到特定客戶在 CUSTOM_GIT 的客製化程式碼，並與 EHRMS_GIT 標準版比對邏輯差異，協助判斷客訴行為是否源自客製落後標準版的 patch。

這是**單一客戶＋單一元件的輕量查詢**，用於除錯過程中的即時定位。若目的是版更時整支分支的批次比對與合併規劃，改用 CUSTOM_GIT 內的 `custom-compare`／`custom-scan` skill（本 skill 不重複維護它們已有的規則，僅在需要時讀取引用）。

## 核心原則

1. **唯讀查詢**：只定位與比對，不修改 CUSTOM_GIT 任何檔案、不 commit
2. **查完必須切回原分支**：CUSTOM_GIT 是共用工作目錄，任何人或排程都可能同時在用
3. **複用既有規則，不重新發明**：分支命名、路徑對應、獨立元件偵測、Big5 編碼規範一律以 CUSTOM_GIT 的 `custom-compare`/`custom-scan` 文件為準
4. **精簡輸出**：僅列關鍵差異與檔案路徑，不主動貼完整程式碼

## Repo 路徑

| 用途 | 路徑 |
|---|---|
| 客製 Repo | `C:/D/CUSTOM_GIT` |
| 標準版 Repo | `C:/D/EHRMS_GIT` |

## 步驟 1：解析客戶

拿到的線索可能是統編、中文簡稱、英文代號，甚至是猜測或誤拼的字（例如客戶說「orise 元件」，但分支其實是 `28069900_敦泰(旭曜)`，orise 只是他們客製元件的 ProgID 前綴，不是分支名）。

```bash
git -C C:/D/CUSTOM_GIT branch -a | grep -i <關鍵字>
```

分支命名格式為 `統編_英文代號_中文簡稱`。若第一個關鍵字查不到，換其他線索再試（統編/中文名/英文代號互相替換；若懷疑關鍵字其實是客製元件的 ProgID 前綴而非客戶代號，改用步驟 3 的 `.vbp`/`CreateObject` 反查法）。

多筆命中時（常見於同客戶有 `_YYYYMM_NNN`、`_asp1`、人名等後綴的多個分支）列出來讓使用者選，預設推薦不帶版本/人名後綴的主分支。

若需要判斷先鋒版/一般版，讀取 `C:/D/CUSTOM_GIT/.claude/skills/custom-compare/SKILL.md` 的先鋒客戶名單表（該表是權威來源，本 skill 不維護副本）。

## 步驟 2：安全切換分支查詢

⚠️ 這是本 skill 最重要的紀律：CUSTOM_GIT 只有一份工作目錄，`git checkout` 會影響其他同時使用這個 repo 的人或排程。

1. **確認乾淨狀態**：
   ```bash
   git -C C:/D/CUSTOM_GIT status --porcelain --untracked-files=no
   ```
   若有輸出（表示有未提交的追蹤檔案變更），**停下告知使用者**，不強制切換 — 可能是別人或排程留下的進行中工作。

2. **記錄原始狀態**：
   ```bash
   git -C C:/D/CUSTOM_GIT rev-parse --abbrev-ref HEAD
   ```
   若結果是 `HEAD`（代表 detached），改記 `git -C C:/D/CUSTOM_GIT rev-parse HEAD` 取得的 commit SHA。

3. **切換到目標客戶分支**：
   ```bash
   git -C C:/D/CUSTOM_GIT checkout <目標客戶分支>
   ```

4. 執行步驟 3、4 的查詢與比對。

5. **無論查詢成功或失敗，最後都要切回**：
   ```bash
   git -C C:/D/CUSTOM_GIT checkout <原分支名稱或原 SHA>
   ```
   並在報告中列出「已切回原分支：xxx」供使用者確認。

## 步驟 3：定位客製元件

- **有類別/檔名關鍵字**：直接在客戶分支目錄下搜尋
  ```bash
  find "C:/D/CUSTOM_GIT/<客戶分支目錄>" -iname "*<關鍵字>*"
  ```
- **只有功能描述**：比照 `custom-scan` 的「客製 DLL 命名」規則，在對應的 `.asp` 搜尋 `Server.CreateObject`，找出非標準 ProgID（例如 `ORISE.clsRemind` 而非標準的 `Personnel_SysSetting.clsRemind`）
- **`.vbp` 專案檔解析 ProgID**：找到 `<ProgID前綴>.vbp`（例如 `ORISE.vbp`），比對其中 `Class=<類別名>; Cls\<檔名>.cls` 這行，才能確認 ASP 的 `CreateObject("ORISE.clsRemind")` 實際對應哪個檔案路徑。這是找到客製元件真正檔案位置的關鍵一步，ProgID 前綴常常跟資料夾名稱一致但不保證。
- **留意鏡像路徑**：同一元件常同時出現在 `eHRMS/VB/...` 與 `VB/...`（`custom-compare` 已有的路徑對應規則）。兩份通常內容一致，用 `diff` 快速確認，一致則取其一分析即可。
- **Big5 編碼**：`.cls` 中文註解若顯示亂碼（`�` 開頭）是 Big5 編碼。唯讀查看可用：
  ```bash
  iconv -f BIG5 -t UTF-8 <檔案路徑>
  ```
  ⚠️ 這只限於**讀取確認邏輯**用。若要修改任何 `.cls` 內容，一律遵照 `custom-compare/SKILL.md` 的 PowerShell + `Encoding.GetEncoding(950)` 讀寫規則，禁止用 Write/Edit 工具直接覆寫（詳見該文件「CLS 檔案編碼規則」章節）。

## 步驟 4：與標準版比對

在 `C:/D/EHRMS_GIT` 找到功能對應的同名/同用途函式，逐函式讀邏輯比對（不是純文字 diff — 客製版常是舊 fork，格式/欄位順序已經跑掉，需要語意層面比對）。重點找：

1. **標準版有、客製版沒有的區塊**：通常是後續 patch 補上的邏輯。標準版程式碼中的日期/單號註解（例如 `'2015/01/05 add by nina for【2014A019】...`）可用來判斷這段邏輯是哪次修改新增的，藉此估計客製版落後到哪個時間點
2. **兩邊都有但邏輯不同的區塊**：語意層面的差異，非格式差異
3. 綜合上述兩點，說明「為什麼客戶的實際行為跟標準版不一樣」

## 步驟 5：輸出報告

**結構化輸出 4 個區塊：**

```
【客戶與分支定位】
客戶：[名稱]（統編：[統編]）
分支：[分支名稱]（若有多筆候選，列出並說明選擇理由）

【客製元件定位】
ProgID：[如 ORISE.clsRemind]
.vbp 解析：[ProgID前綴].vbp → Class=[類別]; Cls\[檔名].cls
檔案路徑：[路徑]（若有鏡像路徑一併列出）

【與標準版差異】
標準版對應邏輯：[EHRMS_GIT 路徑] L[行數]
客製版缺少/不同之處：
  1. [說明] - 標準版於 [日期/單號] 新增，客製版無此邏輯
  2. ...

【切回原分支確認】
原分支/SHA：[值]
已切回：✅
```

## 注意事項

- 只查詢與比對，不修改 CUSTOM_GIT 任何檔案
- CUSTOM_GIT 是共用工作目錄，`git status` 若非乾淨狀態要停下確認，不覆蓋別人的變更
- 客製規則/客戶名單以 `custom-compare/SKILL.md`、`custom-scan/SKILL.md` 的內容為權威來源，本 skill 不重複維護
- 若查完的結論要拿去修復程式碼，回到 `/debug` skill 繼續根因診斷與修復流程；本 skill 只負責定位與比對
