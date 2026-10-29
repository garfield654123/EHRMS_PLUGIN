# Git 安全防護規則

## 絕對禁止的危險操作

| 危險操作 | 說明 | 風險 |
|---------|------|------|
| `git push --force` / `-f` | 強制推送，覆蓋遠端歷史 | 極高 |
| `git push --force-with-lease` | 條件式強制推送 | 極高 |
| `git reset --hard HEAD~n` | 硬重置並丟棄 commit | 極高 |
| `git clean -fd` | 強制刪除未追蹤檔案 | 極高 |
| `git rebase -i` | 互動式變基（不支援） | 高 |
| `git rebase` (對已推送分支) | 變基已推送的分支 | 極高 |
| `git commit --amend` (對已推送) | 修改已推送的 commit | 極高 |
| `git branch -D` | 強制刪除分支 | 高 |
| `git reflog expire` | 清除 reflog | 極高 |
| `git gc --prune=now` | 立即清理物件 | 極高 |

## 禁止推送的分支

- `master` / `main`
- `develop` / `dev`
- `release/*`
- `hotfix/*`

## 允許的 Git 操作

| 操作 | 備註 |
|-----|------|
| `git status` / `git log` / `git diff` / `git branch` | 安全，唯讀 |
| `git checkout -b` / `git checkout` | 安全（需確認無未提交變更） |
| `git add` / `git commit` / `git pull` / `git fetch` | 安全 |
| `git stash` / `git stash pop` | 安全 |
| `git merge` | 需確認目標分支 |

## Git 操作前置檢查

執行任何 Git 操作前必須依序檢查：

1. `git branch --show-current`
2. `git status`
3. 若有未提交變更，先 stash 或 commit
4. `git fetch && git status`



## Git 基本規則

1.使用者若是需要上到測試機，不指定的狀況下通常是指 lab 分支，需要跟使用者詢問確認
2. 當使用者拉分支的時候通常都是由master 拉下來再合併到lab，所以若有檔案差異過多通常是master版本過舊，導致檔案並不進去，此時可以詢問使用者是否需要將master更新，方法是從最新的master拉下來開一個 EHRNSONE-xxxxx_master分支 ，再自動找尋此次更動的檔案進行更新後再合併到lab

## --amend 使用規則

僅在以下條件**全部滿足**時允許：

1. Commit 尚未推送到遠端
2. 是本次對話中剛建立的 commit
3. 用於修正 commit message 或補充遺漏的檔案

**禁止**：Commit 已推送、非本次對話建立、commit 失敗或被 hook 拒絕時。

## 錯誤處理

### Commit 失敗
- 禁止使用 `--amend`，修正後建立新 commit

### 合併衝突
- 執行 `git status` 確認衝突檔案
- 向使用者報告，等待指示，禁止自動選擇版本

### 分支切換失敗
- 先 `git stash` 暫存，或 `git add <檔案> && git commit -m "WIP"`
- 避免使用 `git add .` 以免加入不相干檔案

## 安全檢查清單

- [ ] 確認當前分支名稱
- [ ] 確認工作區無未預期的變更
- [ ] 不對保護分支直接操作
- [ ] 不執行 force 操作
- [ ] 操作計畫已獲使用者同意
- [ ] commit message 格式正確
- [ ] 不執行 push（除非使用者明確要求且非保護分支）
