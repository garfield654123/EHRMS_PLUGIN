# 本機平台設定

範圍：Windows 上的 Claude Code、Codex CLI／具本機執行能力的 Codex 桌面環境。
純網頁 ChatGPT 不會執行本機 Python，也不會直接取得公司內網資料庫。

## 共用執行環境

以目前 SKILL.md 向上兩層定位插件根目錄，不假設快取安裝位置固定。
所有 MCP 使用同一個入口：

```powershell
py -3 -B "<PLUGIN_ROOT>/scripts/run_mcp.py" jira
py -3 -B "<PLUGIN_ROOT>/scripts/run_mcp.py" database
py -3 -B "<PLUGIN_ROOT>/scripts/run_mcp.py" memory
```

這些是持續運行的 stdio 服務；連線驗證使用 MCP client，不能把等待 stdin 當成當機。
入口直接呼叫 main_stdio，避免一般入口的啟動文字進入 JSON-RPC stdout。

## 連線資訊

共用啟動器依序讀取，已存在的有效值優先：

1. 服務程序環境變數。
2. EHRMS_ENV_FILE 指定的 dotenv 檔，未指定時用使用者家目錄的 .ehrms-tools/.env。
3. 舊版相容：使用者家目錄 .claude/settings.json 的 env。

只取所選服務需要的變數，不匯入其他 Claude 設定。缺值時僅回報變數名稱。
Jira 需要 JIRA_BASE_URL、JIRA_EMAIL、JIRA_API_TOKEN。
DB 與 Memory 需要 MSSQL_SERVER、MSSQL_DATABASE、MSSQL_USERNAME、MSSQL_PASSWORD。
Memory 可另外使用 JIRA_EMAIL 或 MEMORY_USER 標示記錄者。
請使用者在本機編輯設定檔，不要求把 Token／密碼貼進對話，不輸出設定值。
可複製 config.env.example 作為範本，勿覆蓋已有設定，勿放進插件快取或提交 Git。

## Claude Code

- 保留 .claude-plugin/plugin.json 與 repo 的 .claude-plugin/marketplace.json。
- manifest 指向 mcp.claude.json；Claude 先讀預設 .mcp.json，再用此檔同名服務覆蓋。
- mcp.claude.json 使用 CLAUDE_PLUGIN_ROOT 絕對定位共用入口。
- 透過 Claude 的 plugin marketplace 安裝 ehrms-tools@ehrms-local；更新後重新載入插件。
- 列表：claude mcp list；設定存在不等於連線成功。
- 手動安裝單一 MCP 時，先讀 claude mcp add --help，明確選擇 scope；例如：

```powershell
claude mcp add --transport stdio --scope user EHRMS-jira-mcp -- py -3 -B "<PLUGIN_ROOT>/scripts/run_mcp.py" jira
```

插件與手動 MCP 安裝擇一，避免重複連線。

## Codex

- 使用 .codex-plugin/plugin.json、.mcp.json 與同一份 skills/。
- .mcp.json 的 cwd 為插件根目錄相對路徑 "."，args 從該目錄定位入口。
- env_vars 明確轉交服務所需的環境變數；也可直接使用共用 dotenv 設定檔。
- repo marketplace 位於 .agents/plugins/marketplace.json。註冊來源後，仍需安裝及啟用插件：

```powershell
codex plugin marketplace add "<EHRMS_PLUGIN repo 根目錄>"
```

在支援插件的 Codex 桌面介面選擇 EHRMS Local 並安裝 ehrms-tools，重新開啟對話。
若 CLI 版本僅提供獨立 MCP，使用以下替代方式；它只接入工具，不會安裝 skills：

```powershell
codex mcp add EHRMS-jira-mcp -- py -3 -B "<PLUGIN_ROOT>/scripts/run_mcp.py" jira
codex mcp add ehrms-database -- py -3 -B "<PLUGIN_ROOT>/scripts/run_mcp.py" database
codex mcp add ehrms-memory -- py -3 -B "<PLUGIN_ROOT>/scripts/run_mcp.py" memory
codex mcp list
```

CLI 單獨使用 skills 時，可將需要的技能完整目錄（含 references/scripts）複製至專案的
.agents/skills/；先比對既有檔案，不覆蓋同名但用途不同的技能。
插件安裝者不需要再複製技能。工具前綴依 runtime 而異，從實際工具清單解析名稱。
專案規範使用 AGENTS.md；Claude 使用 CLAUDE.md。保留兩邊既有業務約束。
