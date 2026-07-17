# mfSendMail 發信流程詳細說明

檔案：`VB\EHRMS\共用Bas\modSendMail.bas`

## 流程判斷邏輯

```
mfSendMail()
├── 查 HRMS_CONFIG.SendMailMethod
│   ├── = '1' → mfSendMailByRelay()        [CDONTS.NewMail, 使用 local SMTP relay]
│   └── 其他 → 查 HRMS_CONFIG.NETSMTP
│       ├── = 'Y' → 查 HRMS_CONFIG.Oauth2Method
│       │   ├── = '2' → mfSendMailByNetSMTPGraphOauth2()    [Microsoft Graph API]
│       │   ├── = '3' → mfSendMailByNetSMTPExChangeOnlineOauth2()  [Exchange Online]
│       │   ├── = '4' → mfSendMailByNetSMTPMailKit()        [MailKit 程式庫]
│       │   ├── = '5' → mfSendMailByNetSMTPGoogle()         [Google Gmail OAuth2]
│       │   └── 其他 → mfSendMailByNetSMTP()                [.NET SMTP (EHR_NET)]
│       └── 其他 → mfSendMailBySMTP()                       [CDO.Message]
└── 寄信後呼叫 SleepSeconds()（查 HRMS_CONFIG.SendMail_delay 延遲）
```

## 各方式說明

### Relay（SendMailMethod = '1'）
- 使用 `CDONTS.NewMail` COM 物件
- 透過 IIS local SMTP relay 轉發
- 不需 SMTP 帳密

### CDO.Message（NETSMTP = 'N'）
- 使用 `CDO.Message` COM 物件
- 讀取：`SMTP_Server_IP`、`SMTP_Server_Port`、`SMTP_User_Authentic`、`SMTP_Username`、`SMTP_Password`、`SMTP_SSL_Encode`

### .NET SMTP（NETSMTP = 'Y', Oauth2Method 非 2/3/4/5）
- 使用 `EHR_NET.clsEmailSender` COM 物件（.NET 組件）
- 原先為 `EmailSender.clsEmailSender`，2024/04/16 改為 `EHR_NET` (EHRMSONE-16274)
- 讀取同 CDO 的 SMTP 設定

### Graph OAuth2（Oauth2Method = '2'）
- 使用 Microsoft Graph API
- 讀取：`Oauth2ClientId`、`Oauth2ClientSecret`、`Oauth2TenantId`
- Token 快取於：`Oauth2AccessToken`、`Oauth2TokenExpiresOn`
- Web Service：`Oauth2WebSerivceURL`（預設 `http://localhost/MailService/MailService.asmx`）

### Exchange Online OAuth2（Oauth2Method = '3'）
- 使用 Exchange Online API
- 讀取設定與 Graph 相同

### MailKit（Oauth2Method = '4'）
- 使用 MailKit 程式庫（via Web Service）
- 讀取 SMTP 設定 + `Oauth2WebSerivceURL`

### Google Gmail OAuth2（Oauth2Method = '5'）
- 使用 Google Gmail API
- 讀取：`Oauth2ClientId`、`Oauth2ClientSecret`、`Oauth2RefreshToken`
- Token 快取於：`Oauth2AccessToken`、`Oauth2TokenExpiresOn`

## 函式簽章

```vb
Public Function mfSendMail(
    strFromEmail As String,   ' 寄件者
    strToEmail As String,     ' 收件者（多個以 ; 分隔）
    strSubject As String,     ' 主旨
    strTextBody As String,    ' 純文字內文（有值時優先）
    strHtmlBody,              ' HTML 內文
    strCC As String,          ' 副本
    strBCC As String,         ' 密件副本
    strAttachFile As String,  ' 附件（多個以 | 分隔）
    conn As ADODB.Connection  ' DB 連線
) As Boolean
```

## 錯誤記錄

寄信失敗時呼叫 `WriteError(ERR.Description)` 寫入錯誤 log：
- log 位置：`VB\EHRMS\AuotEngine\frmAutoEngine.log`
