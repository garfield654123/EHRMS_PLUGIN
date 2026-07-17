---
name: mail-query
description: This skill should be used when the user asks to "查某個通知信的寄發邏輯", "找寄信程式碼在哪", "這個通知信怎麼寄的", "查排程通知設定", "為什麼這封信沒寄出", "某功能的寄信流程", "找通知信的程式碼位置". Use when locating EHRMS AutoEngine mail notification code or tracing mail sending flow.
---

# EHRMS 寄信功能定位

EHRMS 的通知信由 **AutoEngine 排程** 驅動，主程式在 `ClsAutorun_A.cls`。定位某種通知的寄信邏輯時，以 DB 查到的 `NOTIFY_DESCRIPTION`（中文名稱）或 `NOTIFY_REFERENCE_CODE`（如 SNR01）直接在程式碼中搜尋。

---

## 整體架構

```
HRMS_AUTO_JOB（排程設定）
    ↓ 依時間觸發
AutoEngine（VB6 排程服務）
    ↓
ClsAutorun_A.cls（主入口）← → modNotify.bas / modNotify_CN.bas
    ↓
modSendMail.bas → mfSendMail()（實際發信）
```

---

## 定位流程

### Step 1：查通知設定

使用 MCP 查 `HRMS_NOTIFY_REFERENCE`，取得目標通知的 CODE 與說明：

```sql
SELECT NOTIFY_REFERENCE_CODE, NOTIFY_DESCRIPTION, NOTIFY_TYPE,
       NOTIFY_REFERENCE_START, NOTIFY_FREQUENCY, EXPIRE_DAYS
FROM HRMS_NOTIFY_REFERENCE
WHERE NOTIFY_DESCRIPTION LIKE '%關鍵字%'
   OR NOTIFY_REFERENCE_CODE = 'SNRxx'
ORDER BY NOTIFY_REFERENCE_CODE
```

### Step 2：定位程式碼

取得 `NOTIFY_DESCRIPTION`（中文）或 `NOTIFY_REFERENCE_CODE` 後，在 `ClsAutorun_A.cls` 中搜尋：

```
檔案：VB\EHRMS\AuotEngine\ClsAutorun_A.cls
編碼：BIG5（CP950）— 用 PowerShell 讀取，不可用 Edit tool 直接修改
搜尋：Grep tool 搜尋 NOTIFY_REFERENCE_CODE 或 NOTIFY_DESCRIPTION 中文關鍵字
```

注意：`ClsAutorun_A.cls` 為 BIG5 編碼，中文註解可直接作為搜尋關鍵字。

### Step 3：分析邏輯

找到對應程式碼段後，說明：
1. 觸發條件（哪些員工、哪些日期條件）
2. 收件對象（本人 / 一階主管 / 二階主管 / 業務）
3. 信件主旨與內文來源（`HRMS_NOTIFY_REFERENCE` 的 TOPIC_* / LETTER_* 欄位）
4. 呼叫鏈路（呼叫哪個 modNotify function）

---

## 相關程式碼檔案

| 檔案 | 路徑 | 說明 |
|------|------|------|
| `ClsAutorun_A.cls` | `VB\EHRMS\AuotEngine\` | **主入口**，各通知邏輯在此 |
| `modNotify.bas` | `VB\EHRMS\AuotEngine\` | 通知共用函式（繁中版） |
| `modNotify_CN.bas` | `VB\EHRMS\AuotEngine\` | 通知共用函式（簡中版） |
| `modSendMail.bas` | `VB\EHRMS\共用Bas\` | 底層寄信函式 `mfSendMail()` |

---

## 相關資料表

| 表格 | 說明 |
|------|------|
| `HRMS_NOTIFY_REFERENCE` | **通知主設定**：CODE、名稱、啟用狀態、寄發條件、主旨/內文範本 |
| `HRMS_NOTIFY_MESSAGE` | 通知發送記錄（員工 × 通知類型 × 日期） |
| `HRMS_NOTIFY_MEMBER` | 收件人設定 |
| `HRMS_NOTIFY` | 通知主體（主旨、內文） |
| `HRMS_NOTIFY_MESSAGE_TYPE` | 通知種類詳細說明 |
| `HRMS_AUTO_JOB` | 排程時間設定 |
| `HRMS_AUTO_JOB_LOG` | 排程執行記錄 |
| `HRMS_CONFIG` | 寄信方式設定（SMTP/Relay/OAuth2） |

---

## 寄信方式判斷（HRMS_CONFIG）

`mfSendMail()` 依 `HRMS_CONFIG` 決定發信管道：

| KEYNAME | 說明 |
|---------|------|
| `SendMailMethod` | `1`=Relay；其他=SMTP |
| `NETSMTP` | `Y`=使用 .NET SMTP |
| `Oauth2Method` | `2`=Graph；`3`=ExchangeOnline；`4`=MailKit；`5`=Google |
| `SMTP_Server_IP` | SMTP 主機 |
| `SMTP_Server_Port` | SMTP 埠號 |
| `Oauth2WebSerivceURL` | OAuth2 Web Service 位址 |

查目前設定：
```sql
SELECT KEYNAME, KEYVALUE FROM HRMS_CONFIG
WHERE KEYNAME IN ('SendMailMethod','NETSMTP','Oauth2Method',
                  'SMTP_Server_IP','SMTP_Server_Port','Oauth2WebSerivceURL')
```

---

## 診斷寄信失敗

1. 查排程是否有跑：`HRMS_AUTO_JOB_LOG`
2. 查通知是否啟用：`HRMS_NOTIFY_REFERENCE.NOTIFY_REFERENCE_START = '1'`
3. 查是否已產生通知記錄：`HRMS_NOTIFY_MESSAGE`（有記錄代表已判斷需寄，但不代表有寄出）
4. 查寄信設定是否正確：`HRMS_CONFIG`（SMTP/OAuth2 設定）
5. 查錯誤 log：`VB\EHRMS\AuotEngine\frmAutoEngine.log`

---

## 詳細參考

- **`references/db-schema.md`** — HRMS_NOTIFY_REFERENCE 完整欄位說明與收件對象欄位對照
- **`references/send-flow.md`** — mfSendMail 各發信方式詳細流程
