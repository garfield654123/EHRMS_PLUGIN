---
name: ehrms-custom-mcp
description: 透過 ehrms-custom-mcp 直接查詢客戶端 EHRMS 資料庫（客戶開啟 EHRMS_Connect.exe、提供 6 碼配對碼後，AI 產生 SELECT、工程師逐筆核准執行）。Use when the user gives a 6-digit pairing code (配對碼), asks to 連客戶資料庫／直接查客戶 DB／用 custom mcp 查, or when a 維運單 needs customer data and the user has the customer connected. 不處理資料異動（只能 SELECT）。
---

使用規則內建在 `ehrms-custom-mcp`（server instructions、工具說明與錯誤訊息），照 MCP 提供的說明操作即可。

工具不存在時，告知使用者尚未註冊，改回「提供 SQL 請客戶端執行」。規格：`servers/REMOTE_DB/SPEC.md`。
