---
name: user-config
description: 管理 EHRMS 使用者身份設定，記錄使用者的 Notion User ID 等資訊，供 test-report 等 skills 讀取使用。Use to view, switch, or add user identity configuration.
---

執行環境：Claude Code 與 Codex 共用；工具名稱與檔案讀取方式依 [平台工具對照](../../references/platform-runtime.md) 選擇。

# EHRMS 使用者設定

此 skill 管理使用者身份設定，供 `test-report` 等其他 skill 讀取使用者資訊（如 Notion Owner）。

**設定檔位置：** `~/.ehrms-tools/users.md`（使用者家目錄，與 `.env` 同一個資料夾；直接編輯此檔案即可更新設定）。
設定檔放在 plugin 目錄外，更新 plugin 時才不會被覆蓋。檔案不存在時，以本目錄的
[users.example.md](users.example.md) 為範本建立，並請使用者填入自己的資料。

## 使用方式

| 指令 | 說明 |
|------|------|
| `/user-config` | 顯示當前 active 使用者與完整清單 |
| `/user-config switch 子平` | 切換 active 使用者 |
| `/user-config add` | 引導新增使用者 |

## 工作流程

### 查看設定
1. 讀取 `~/.ehrms-tools/users.md`（不存在時依上方說明從範本建立）
2. 顯示 active 使用者名稱、Jira Email、Notion User ID
3. 列出完整使用者清單

### 切換 active 使用者
1. 讀取 `~/.ehrms-tools/users.md`
2. 確認使用者名稱存在於清單中（不存在則提示）
3. 更新 `active:` 欄位
4. 回報切換結果

### 新增使用者
1. 詢問：名稱、Jira Email
2. 使用 `notion-get-users` 以 Email 搜尋 Notion User ID
3. 確認後新增一行到 `~/.ehrms-tools/users.md` 的使用者清單
4. 詢問是否同時切換為 active

## 供其他 skills 參考的讀取規則

其他 skill 需要取得 Owner 時，步驟如下：
1. 讀取 `~/.ehrms-tools/users.md`，取得 `active:` 欄位的使用者名稱
2. 在「使用者清單」中找到對應的 `Notion User ID`
3. 若檔案不存在、`active` 未設定、或找不到對應使用者 → 詢問使用者
