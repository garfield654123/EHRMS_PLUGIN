---
name: setup-guide
description: 引導安裝及驗證 Claude Code／Codex 共用的 EHRMS 工具包，包括 Python 依賴、Jira／資料庫／Memory 設定與技能載入。Use when setting up ehrms-tools or diagnosing its installation.
---

# EHRMS 配置與測試

先讀取 [平台設定](../mcp-config/references/platforms.md)，依目前平台選擇安裝方式。
支援完整設定、config（只設定）、test（只測試），以及 jira／db／memory 單一服務。

## 設定流程

1. 使用目前平台的 shell 工具執行 py -3 --version，確認 Python 3.10 以上。
2. 由本 SKILL.md 向上兩層定位插件根目錄，執行 setup.bat 或
   py -3 -m pip install -r "<PLUGIN_ROOT>/requirements.txt"。命令失敗時先處理原因。
   Database／Memory 另需 Microsoft SQL Server ODBC Driver 與公司網路。
3. 按使用者指定的服務檢查環境變數是否存在，勿顯示值。可沿用共用 .env 或舊版 Claude 設定。
   缺值時提供 config.env.example，請使用者在本機填寫；不要求把密碼或 API Token 貼入對話。
4. 按平台設定文件安裝插件或獨立 MCP。保留現有設定，不重複註冊。
5. 確認技能來源。插件安裝者直接共用包內 skills/，不需要跨 repo 複製。
   若專案已有 crisis-triage、mail-query、weekly-report 的舊副本，先比對差異，再依使用者需求同步
   完整技能目錄（含 references/scripts）。Claude 專案目錄是 .claude/skills/，
   Codex 專案目錄是 .agents/skills/。不要自動 stage 或 commit。
   流程類技能（bug-fix、test-report、test-steps 等）已併入本插件；EHRMS_GIT 若仍有同名舊副本，先比對再決定是否移除。
6. 重新載入客戶端／開新對話後，依下列測試確認工具可用。

## 連線測試

先列舉工具確認三個 MCP 是否載入，再按已設定的服務測試：

| 服務 | 測試 | 成功標準 |
|---|---|---|
| Database | echo("ping") | 收到回應 |
| Database | mssql_query：SELECT 1 AS ok | 回傳 ok=1 |
| Jira | get_user_info，不指定 username | 取得目前登入帳號 |
| Jira | get_issue_summary，僅用使用者指定單號 | 取得有權限案件的摘要 |
| Memory | 列舉工具 | 包含 recall、remember、jira_lookup、jira_log、faq_lookup、bulletin_lookup、version_bulletins、task_lookup、custom_sa_log、custom_log |
| Memory | 有需要才用 faq_lookup 查已知主題 | 查詢成功，空結果也算成功 |

不呼叫寫入工具測試。recall 會更新 HIT_COUNT，不能當作純讀取測試。
工具 schema 以當前清單為準；數字單號補 EHRMSONE-，完整 key 不改。

## 結果

分開報告套件安裝、設定寫入、MCP 握手、外部服務連線與技能載入的狀態。
未執行的項目寫「未驗證」，不當作通過。
連線失敗時區分缺少依賴、路徑錯誤、缺少環境變數、公司網路／VPN、ODBC 與帳號權限問題。
設定成功但當前對話未重新載入時，明確交代需要開新對話。
