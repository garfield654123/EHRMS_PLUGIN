# EHRMS 程式碼修改流程

從需求確認到正式釋出的完整流程，使用 Git + GitHub 版控。

- Git 安全規則：See [../bug-fix/git-safety.md](../bug-fix/git-safety.md)

## 流程步驟

### Step 1｜建立開發分支

- 從最新 `master` 拉出新分支，命名使用 Jira 單號
- 若需重整（見 Step 7），使用變體：`EHRMSONE-12345_master`

```bash
git checkout master
git pull origin master
git checkout -b EHRMSONE-12345
```

> ⚠️ 務必確認 `master` 為最新版本再切分支，避免後續 merge 差異過大。

### Step 2｜開發、自我 Review 與推送至 lab

1. 在開發分支上進行程式碼修改
2. Commit message 建議清楚描述修改內容
3. **修改者本人 push 前自行 review 一遍**
4. push 後**手動 merge 進 `lab`**（測試機分支）

```bash
git add .
git commit -m "修改描述"
git push origin EHRMSONE-12345
```

> AI 應提醒使用者確認已完成自我 review 再 push。

### Step 3｜測試階段

merge 到 `lab` 並 push 後：

1. 撰寫**測試文件**（Notion 或 Markdown），內容包含：
   - 原始問題描述
   - 修改的檔案清單
   - 測試步驟說明
2. 將測試文件發給相關人員進行測試
3. 等待測試回饋

> AI 可協助草擬測試文件的架構與內容。

### Step 4｜等待上板

測試通過後：

1. 在 Jira 上更新 ticket 狀態為「等待上板」
2. 於內部會議討論哪些程式碼可釋出
3. 確認項目後整理至 Excel 清單

### Step 5｜正式版本釋出（PR 流程）

1. 建立版號分支，命名格式 `年份月份_序號`（由使用者指定）
   - 範例：`202506_000`、`202612_100`
2. 以 **Pull Request** 將開發分支 merge 進版號分支
3. PR 須經 **2 位人員 review 並 Approve** 後才能 merge

> ⚠️ 版號分支名稱由使用者提供，AI 不應自行假設。

### Step 6｜版更後 merge 回 master（專人處理）

正式釋出後，**由專人負責** merge 回 `master`。

> AI 不需執行此步驟，但應在適當時機提醒使用者通知負責人。

### Step 7｜分支重整（當分支落後太多時）

**觸發時機**：Bug 修正拖延過久，`master` 已更新多個版本，PR 差異過大。

**處理方式**：從最新 `master` 建立乾淨分支，將原分支的 commit cherry-pick 過來。

#### 7.1 前置準備

```bash
# 確認工作區狀態，有未提交變更先 stash
git status
git stash push -m "WIP: stash before branch reorganization"
```

#### 7.2 找出原分支的 commit 範圍

```bash
# 列出原分支中不在 master 的 commit（由舊到新）
git log EHRMSONE-12345 --not origin/master --oneline --reverse
```

#### 7.3 建立新的 _master 分支

```bash
git fetch origin master
git checkout master && git pull origin master
git checkout -b EHRMSONE-12345_master
```

#### 7.4 Cherry-pick 原分支的 commit

```bash
# 單一 commit
git cherry-pick <commit-hash>

# 多個連續 commit（從舊到新）
git cherry-pick <oldest-hash>^..<newest-hash>
```

#### 7.5 衝突處理

若 cherry-pick 出現衝突：

1. 執行 `git status` 查看衝突檔案
2. **通知使用者** 並等待指示，禁止自動選擇版本
3. 使用者解決衝突後，執行 `git add <衝突檔案>` → `git cherry-pick --continue`

#### 7.6 驗證與推送

```bash
# 確認新分支與 master 的差異只包含原修改
git diff origin/master..EHRMSONE-12345_master
# 確認 commit 記錄正確
git log EHRMSONE-12345_master --not origin/master --oneline
# push 新分支
git push origin EHRMSONE-12345_master
```

#### 7.7 還原工作區

```bash
# 切回原本分支
git checkout <原本的分支>
# 恢復 stash
git stash pop
```

> ⚠️ cherry-pick 會保留原始 commit message 與修改者資訊，但產生新的 commit hash。後續 PR 應使用 `_master` 分支。

## 分支命名速查

| 情境 | 分支名稱範例 |
|------|-------------|
| 一般開發 | `EHRMSONE-12345` |
| 分支重整 | `EHRMSONE-12345_master` |
| 版號釋出 | `202506_000` |
| 測試機 | `lab` |
| 主線 | `master` |
