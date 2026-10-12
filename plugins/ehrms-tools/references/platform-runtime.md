# 平台工具對照

同一份業務流程供 Claude Code 與 Codex 使用，不假設存在特定工具名稱。

| 文件用語 | Claude Code | Codex |
|---|---|---|
| Read | 檔案讀取工具 | shell／檔案工具讀取文字，依編碼解碼 |
| Grep | 文字搜尋工具 | rg；檔名清單使用 rg -l |
| Glob | 檔名搜尋工具 | rg --files 或 Get-ChildItem |
| Bash／PowerShell | shell 工具 | exec_command 或目前提供的 shell 工具 |
| PDF／圖片讀取 | 對應視覺讀取工具 | PDF 渲染與圖片檢視工具；不能只讀文字而忽略截圖 |
| MCP 工具 | 從實際工具清單選取 | 從實際工具清單選取，前綴可能不同 |

文件中的工具參數只是原平台範例，轉成目前工具支援的參數。
Big5／CP950 程式檔需明確解碼或轉成 UTF-8 暫存內容後讀取，不修改原始編碼。
文中要求用 Read 比對程式時，重點是正確解碼並閱讀語意；Codex 可用 shell 解碼，
不能以 diff 的二進位提示當作完成比對。
Word／Excel 分析優先使用目前環境的文件工具；需要保留截圖版面時，
可使用技能內 scripts/doc_to_pdf.py（需 Windows Office／pywin32）轉 PDF 再檢視。
未提供需要的工具或外部 repo 時，說明缺少的依賴，不假設已讀取或驗證。

專案規範以平台已載入的 AGENTS.md／CLAUDE.md 為準。
只有使用者要求安裝或設定時才讀取
[安裝設定](../skills/mcp-config/references/platforms.md)，一般查案不需重跑安裝。
