---
name: bug-fix
description: EHRMS 程式碼修改生命週期管理。涵蓋建立分支、commit、合併 lab、建立 Notion 測試報告（整合 Jira MCP + git log）、版本釋出。支援從任意步驟開始，可自動偵測當前狀態。Use when managing code change lifecycle for EHRMS, creating branches, merging to lab, creating test reports, or preparing releases.
---

執行環境：Claude Code 與 Codex 共用；工具名稱與檔案讀取方式依 [平台工具對照](../../references/platform-runtime.md) 選擇。
Git 操作需在 EHRMS_GIT repo 目錄內執行。

# EHRMS Bug Fix 生命週期

程式碼修改確認後的完整 Git 生命週期管理，從建立分支到版本釋出。

- Git 安全規則：See [git-safety.md](git-safety.md)
- 詳細流程參考：See [code-change-workflow.md](code-change-workflow.md)

## 觸發方式

提供 Jira 單號後，AI 自動偵測當前狀態並提示下一步：

- 「EHRMSONE-12345 幫我建立分支」→ 直接執行 Step 1
- 「EHRMSONE-12345 已 merge 到 lab，要建立測試報告」→ 直接執行 Step 3
- 「EHRMSONE-12345 執行 Step 3」→ 直接執行對應步驟
- 「EHRMSONE-12345」（只提供單號）→ 自動偵測狀態後詢問

## 自動偵測步驟

只提供 Jira 單號時，依序執行偵測：

```bash
# 1. 分支是否存在（本地 + 遠端）
git branch -a | grep EHRMSONE-XXXXX

# 2. 是否已 merge 到 lab
git log origin/lab --oneline | grep EHRMSONE-XXXXX

# 3. 列出版號分支，確認是否已進入釋出流程
git branch -r | grep -E "origin/[0-9]{6}_[0-9]{3}"
# 再查各版號分支是否含此單號 commit
git log origin/<版號分支> --oneline | grep EHRMSONE-XXXXX
```

偵測完成後向使用者回報並確認：

```
【EHRMSONE-12345 狀態偵測】
✅ 分支已建立（EHRMSONE-12345）
✅ 已 merge 到 lab（2026-02-20）
❌ 尚未進入版號分支

推測目前在 Step 3（建立測試報告）。是否繼續？
```

⚠️ 狀態需使用者確認，不自行假設。

---

## Step 1｜建立開發分支

**AI 執行：**

```bash
git checkout master
git pull origin master
git checkout -b EHRMSONE-XXXXX
```

⚠️ 若分支已存在 → 詢問是否沿用現有分支或進行重整（見 Step 7）

---

## Step 2｜Commit + Merge to Lab

> 程式碼修改由 `/debug` 完成。此步驟假設修改已完成，進行提交與合併。

### 2.1 Commit

```bash
git status && git diff
git add <修改的檔案路徑>    # 逐一加入，不使用 git add .
git commit -m "$(cat <<'EOF'
EHRMSONE-XXXXX: 修正內容簡述
- 詳細說明
EOF
)"
```

提醒使用者完成自我 review 後再繼續。

### 2.2 Merge to Lab

```bash
# ⚠️ 前置檢查：確認工作區乾淨，避免切換失敗或帶入不相關變更
git branch --show-current          # 確認在預期分支
git status --porcelain             # 應只剩本次相關檔案
#   → 若有其他未提交的追蹤檔變更（如 .claude/settings.local.json），
#     先 stash，否則 git checkout 可能被中止、導致分支從錯誤基底切出

# 更新 lab 至最新
git checkout lab
git pull origin lab

# 合併開發分支
git merge EHRMSONE-XXXXX
```

若遇 conflict → 見【衝突處理】

合併後，提供以下指令供使用者察看 log 確認：

```bash
git log --oneline -10    # 確認 merge 結果符合預期
```

**⚠️ Push 由使用者操作。** 確認後自行執行：

```bash
# Push 前再確認一次：ahead 數字符合預期、無殘留未提交追蹤檔
git status -sb           # 例：## lab...origin/lab [ahead 2]
git push origin lab
```

---

## SQL 類型修正補充規範（Stored Procedure / SQL 檔案）

修正對象為 `script/StoredProcedure/*.SQL`（或 `/script` 下其他 SQL 類型）時，Step 1～2 的分支/commit 流程照走，但額外多兩件事：

1. **來源檔編碼**：`script/StoredProcedure/*.SQL` 多為 Big5 編碼（Chinese 註解），**不可用 Edit tool 直接改**，會把整檔編碼轉壞。須用 PowerShell 以 codepage 950 讀寫（讀出轉 UTF-8 → 用 Edit 改內容 → 轉回 Big5 寫回），與 `.cls` 檔案的處理方式相同。
2. **額外在 `/SQL` 資料夾新增一份可直接執行的部署腳本**，與 `script/StoredProcedure` 的來源檔一併 commit：
   - 命名慣例：`SQL/YYYYMMDD_使用者_TICKETID_描述.SQL`（如 `SQL/20260819_ziping_EHRMSONE-32960_getEmpCardMatch系列_NTEXT改NVARCHAR修正.SQL`）
   - 編碼：**UTF-8**（與 `script/StoredProcedure` 的 Big5 來源檔不同，這支是給人直接執行用的）
   - 內容：檔頭以註解說明問題/根因/修法，本體為完整 `CREATE PROCEDURE` 內容，保留原始 `GO` 批次分隔，讓 CS/客戶端/部署人員可直接在 SSMS 或 sqlcmd 執行，不需理解 Git repo 結構
3. `/script/StoredProcedure`（版控來源）與 `/SQL`（部署執行檔）用途不同、缺一不可：前者是這個 SP 的「目前真實定義」，後者是「這次異動要怎麼部署」的操作腳本

⚠️ 透過資料庫 MCP 工具直接執行部署（`DROP PROCEDURE` / `CREATE PROCEDURE`）屬於破壞性 DDL，可能被權限分類器擋下；若使用者未明確要求由 AI 執行部署，預設只完成分支/commit/push，部署與驗證交由使用者或 CS 自行透過 `/SQL` 腳本執行。

---

## Step 3｜建立測試報告

自動蒐集三個來源的資訊，整合後建立 Notion 頁面。

### 3.1 Jira 資訊（MCP）

```
mcp__plugin_ehrms-tools_EHRMS-jira-mcp__get_issue(issue_key="EHRMSONE-XXXXX")
```

擷取：
- **標題**（Summary）→ Notion 頁面標題
- **問題描述**（Description）→ 原本問題
- **根因**（Root Cause 欄位或 Comment 中的根因說明）
- **解決方式**（Resolution / 修復 Comment）

若欄位為空，標記為「[待補充]」，不自行捏造。

### 3.2 Git Log 資訊

```bash
# 此分支相對 master 的 commit 記錄
git log EHRMSONE-XXXXX --not origin/master --oneline

# 修改的檔案清單
git diff origin/master..EHRMSONE-XXXXX --name-only
```

從 commit message 輔助判斷修改摘要。

### 3.3 測試步驟

AI 根據 Jira 描述與 commit 內容提供**草稿**，使用者補全後再建立報告。

草稿格式：
```
1. [根據問題描述推測的測試前置條件]
2. [操作步驟簡述]
3. 確認 [預期結果]
（以下請使用者補充詳細步驟）
```

### 3.4 建立 Notion 報告

整理完畢後呼叫 `/test-report`，填入以下內容：

| Notion 欄位 | 來源 |
|------------|------|
| 頁面標題 | `EHRMSONE-XXXXX: [Jira 標題]` |
| 原本問題 | Jira Description |
| 根因 | Jira Root Cause / Comment |
| 正確案例 | 修復摘要（commit message + 解決方式） |
| 測試步驟 | AI 草稿（待使用者補全） |
| 有更動過之檔案 | `git diff --name-only` 結果 |
| Git 分支 | `EHRMSONE-XXXXX` |
| Jira 連結 | `[Jira Base URL]EHRMSONE-XXXXX`（Base URL 見 `~/.ehrms-tools/users.md`（由 user-config 管理）） |

See [../test-report/SKILL.md](../test-report/SKILL.md)

---

## Step 4｜等待上板

1. 提醒使用者在 Jira 更新 ticket 狀態為「等待上板」
2. 詢問是否需要整理至 Excel 上板清單
3. 等待內部討論結果

---

## Step 5｜正式版本釋出（PR）

1. 詢問版號分支名稱（格式：`年月_序號`，如 `202506_000`）
2. 確認後引導建立 Pull Request（EHRMSONE-XXXXX → 版號分支）
3. 提醒：**需 2 位人員 review 並 Approve** 後才能 merge

⚠️ 版號分支名稱由使用者提供，AI 不假設。

---

## Step 6｜Merge 回 Master（專人處理）

正式釋出後，**由專人負責** merge 回 `master`。

AI 提醒使用者通知負責人，不執行此步驟。

---

## Step 7｜分支重整（落後 Master 時）

**觸發時機：**
- 分支建立過久，master 已更新多個版本
- PR 差異過大，難以 review

**AI 執行：**

```bash
# 確認工作區狀態
git status
git stash push -m "WIP: stash before branch reorganization"

# 找出原分支中不在 master 的 commit（由舊到新）
git log EHRMSONE-XXXXX --not origin/master --oneline --reverse

# 建立新的乾淨分支
git fetch origin master
git checkout master && git pull origin master
git checkout -b EHRMSONE-XXXXX_master

# Cherry-pick（逐一確認再執行）
git cherry-pick <oldest-hash>^..<newest-hash>
```

若衝突 → 見【衝突處理】

**驗證：**

```bash
git diff origin/master..EHRMSONE-XXXXX_master    # 差異應只含本次修改
git log EHRMSONE-XXXXX_master --not origin/master --oneline
```

**⚠️ Push 由使用者操作。** 確認 log 符合預期後自行執行：

```bash
git push origin EHRMSONE-XXXXX_master
```

**還原工作區：**

```bash
git checkout <原本的分支>
git stash pop
```

---

## 衝突處理

Merge / Cherry-pick 發生衝突時：

1. 執行 `git status` 列出衝突檔案
2. 向使用者報告衝突清單，**等待指示**，禁止自動選擇版本
3. 使用者解決後，協助繼續：
   - Merge conflict：`git add <file>` → `git commit`
   - Cherry-pick conflict：`git add <file>` → `git cherry-pick --continue`

---

## 分支命名速查

| 情境 | 格式 | 範例 |
|------|------|------|
| 一般開發 | `EHRMSONE-XXXXX` | `EHRMSONE-12345` |
| 分支重整 | `EHRMSONE-XXXXX_master` | `EHRMSONE-12345_master` |
| 版號釋出 | `年月_序號` | `202506_000` |
| 測試機 | `lab` | — |
| 主線 | `master` | — |
