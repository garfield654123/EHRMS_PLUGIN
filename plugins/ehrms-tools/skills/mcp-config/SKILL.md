---
name: mcp-config
description: 管理 Claude Code 與 Codex 的 EHRMS MCP 設定，支援 Jira、DB 與 Memory 的安裝、設定檢查及連線診斷。Use when configuring EHRMS MCP servers or checking their connection status.
---

# MCP 設定管理

先依目前執行環境辨識 Claude Code 或 Codex，再讀取 [平台設定](references/platforms.md)。
使用者已指定平台、服務或安裝路徑時沿用，不重複詢問。

## 查看設定

1. 從平台實際工具清單確認 EHRMS-jira-mcp、ehrms-database、ehrms-memory 是否提供。
2. 以對應 CLI 的 mcp list 核對設定；只報告服務名稱、啟動路徑與缺少的變數名稱，不輸出憑證。
3. 分別標示「已設定」「已載入工具」「已通過服務連線」；MCP resources 空白不代表服務離線。
4. 未安裝時，依平台設定文件提供插件安裝或獨立 MCP 設定。

## 新增或調整

- 使用插件內的 scripts/run_mcp.py，不另行 clone 舊的獨立伺服器 repo。
- 使用者未指定服務時，列出 Jira、Database、Memory 三者的用途後確認範圍。
- 保留既有 MCP 與環境設定，只修改目標項目；檢查是否已有插件版或獨立 MCP，避免重複。
- 優先沿用有效環境變數或既有共用設定。缺少憑證時，請使用者在本機設定，不貼入對話。
- 完成設定後提示重新載入對應客戶端／開新對話，不能把設定檔寫入成功當作服務連線成功。

## 驗證

- Database：echo；需要驗證資料庫時，執行 SELECT 1 AS ok。
- Jira：不帶 username 呼叫 get_user_info；查單時只使用使用者指定且有權限的單號。
- Memory：列舉工具確認握手；需驗證 DB 時可用 faq_lookup 查已知主題。
- 不以 remember、jira_log、custom_log、custom_sa_log 或 add_comment 做連線測試。
- recall 會更新引用統計，不能把它當作完全唯讀的 ping。

只提供數字的 Jira 單號補 EHRMSONE- 前綴；完整 key 保留使用者指定的專案。
