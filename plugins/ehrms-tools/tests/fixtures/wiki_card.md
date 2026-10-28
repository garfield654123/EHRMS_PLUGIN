---
topic_code: CARD
title: 打卡（刷卡）
category: 業務主題
module: M1
modules: [M1, AutoEngine, Portal]
related_topics: [LEAVE_APPLY_CHECK, OVERTIME_APPLY, FLEXIBLE, LEAVE_OF_ABSENCE]
owner: ziping.zhou
audience: eng
customer_code: ""
keywords: [打卡, 刷卡, 刷卡異常, 刷卡比對, 忘刷卡, 遲到, 早退, 曠職, 超時出勤, 手機打卡, 刷卡排程]
description: 員工打卡資料從哪裡來、怎麼變成上下班紀錄、系統如何判定遲到早退曠職等異常、異常怎麼被處理，以及重新比對時哪些結果會保留。
menu_paths:
  - 出勤管理›出勤刷卡
  - 出勤管理›手機打卡
  - 出勤管理›員工卡號管理
entry_list:
  - EHRMS/Personnel/PELAttend/PELAttend_CardMachine/
  - EHRMS/Personnel/PELAttend/PELAttend_MobilePunch/
  - EHRMS/Personnel/PELAttend/PELAttend_CardNo/
  - EHRMS/Plugin/CardData/
  - EHRMS/ehrPortal/DEPT/（Personal_Atten_Error*.asp）
  - VB/EHRMS/Personnel/Cls/clsCardMachine.cls
  - VB/EHRMS/AuotEngine/clsAutoRun_CardData.cls
  - VB/PORTAL/Portal_Frontend/Cls/clsErrorCard.cls
  - script/StoredProcedure/dbo.runCardMatch.SQL
related_tables: [HRMS_CARD_RAWDATA, HRMS_CARD_DATA, HRMS_CARD_DATA_MATCH, HRMS_WORKTIMESET, HRMS_OT_REASON_SET]
related_configs:
  - HRMS_CONFIG: [CardOffTime, CardMatchTwoMonth, showOverTime, SNR30_Flag]
  - PORTAL_CONFIG: [showOverTime, showOverTimeEdit]
sources: [memory:92-112, memory:134, faq:302, faq:708, raw/system-feature-map.md]
---

# 打卡（刷卡）

<!-- ================= 上半：給所有人 ================= -->

## 這是什麼

員工每天打卡，系統會把打卡時間和當天的班別、請假、加班資料對照，找出「應刷未刷、遲到、早退、曠職、超時出勤」等異常，交給人事或員工處理。整個過程分四步：

```
收集打卡 → 判定上下班 → 比對找出異常 → 處理異常
（四種來源）  （識別碼）    （手動或排程）   （人工／請假自動沖銷）
```

## 什麼時候會發生

- **每天自動**：刷卡排程定時把卡鐘檔案轉入，接著自動比對。
- **人事手動**：在「出勤刷卡」頁面對指定日期或員工重新比對，例如補了忘刷卡單之後。
- **員工自己**：在員工入口看到自己的刷卡異常，補填超時出勤原因。

## 規則

### R1：打卡資料有四種來源
門禁卡鐘匯入、網路刷卡、簽核補單（忘刷卡單）、手機打卡。不同來源後續的處理方式略有不同，例如刪除原始打卡資料時，網路刷卡不受影響。
（依據：記憶 #110）

### R2：卡鐘和手機的打卡要先「判定上下班」才算數
原始打卡要先依班別判定哪一筆是上班、哪一筆是下班，才會變成出勤紀錄並拿去比對。被選為上下班的那兩筆，**不一定**是當天第一筆和最後一筆。
- 例：員工當天刷了 5 次卡，系統依班別的檢查時段挑出上下班各一筆，其餘標為「非首尾筆」。
（依據：記憶 #106、#110）

### R3：只有「需要打卡」的在職員工會被比對
員工資料設定為需刷卡、且不是錄取待報到的員工才會被比對。離職或留職停薪從**生效日隔天**起不再比對；復職從**復職日當天**起恢復比對。
- 例：員工 3/15 離職生效，3/15 當天仍會比對，3/16 起不比對。
（依據：記憶 #108）

### R4：自動比對不是每次都比整個月
自動排程會依「上一次薪資作業的假勤截止日」推算從哪天開始比，預設比到昨天。所以已經結過薪的舊日期，排程通常不會再比。
- 例：上個月薪資的假勤截止日是 9/30，排程大致從 10/1 比到昨天。
（依據：記憶 #93）

### R5：異常分六種，門檻來自班別設定
應刷未刷（只缺上班或只缺下班一筆）、遲到、早退、超時出勤、曠職、資料不全。遲到與早退是否成立，看班別設定的「容許遲到／早退分鐘」；超過班別的「曠職上限」會升級成曠職。整天上下班都沒刷，兩筆都算曠職。
- 例：容許遲到 5 分鐘、曠職上限 120 分鐘的班別，晚到 10 分鐘算遲到，晚到 3 小時算曠職。
（依據：記憶 #100）

### R6：超時出勤只在「沒有其他異常」時才判定
下班晚於應下班時間、且超過加班的最小單位，才算超時出勤。公司可以設定不顯示超時出勤異常。
（依據：記憶 #102）

### R7：已經請假或申請加班，異常會被自動標為「已處理」，但不會消失
比對時，若申請的假勤時段涵蓋了異常時段（午休等扣除時段不算），系統會把該異常自動標成已處理。異常紀錄仍然看得到，只是狀態變成已處理。加班類申請則用來沖銷超時出勤。
- 例：請了上午半天假，9 點的遲到異常會顯示為「已處理」。
（依據：記憶 #98；回覆客戶可用 FAQ #302、#708）

### R8：重新比對時，人工處理過的異常會保留
重新比對指定日期時：
- **人工標為已處理**的異常：保留，不會被覆蓋。
- **系統自動判定的已處理**和**未處理**的異常：刪除後依當時的資料重新判定。
- **員工在員工入口填超時出勤原因而處理的**：預設保留；若公司開啟「超時出勤異常通知」則會重算。

所以補了忘刷卡單或請假單後重新比對，異常**通常**會消失或轉為已處理，但不保證一定消失。
（依據：記憶 #97）

### R9：人工標成「已處理」後，畫面上改不回「未處理」
後台單筆處理或批次轉處理都只能把未處理改成已處理。批次轉處理不會產生假勤；需要產生假勤請改用「批次轉入假勤」。
（依據：記憶 #112）

### R10：員工補填超時出勤原因可能有期限
公司可設定填寫期限：「每月 N 號前」或「刷卡日後 N 天」。逾期後員工入口的填寫按鈕會停用。
（依據：記憶 #104）

## 常見誤解

| 客戶的說法 | 實際情況 | 先查什麼 |
|---|---|---|
| 「請了假，為什麼還顯示異常？」 | 異常還在是正常的，應該顯示為已處理（R7） | 狀態是否為已處理；若仍未處理，查假別、請假時段是否涵蓋、是否已重新比對 |
| 「補了忘刷卡單，異常沒消失」 | 補單後要重新比對才會更新（R8） | 是否已對該日重新比對；該異常是否之前被人工處理過 |
| 「某天的異常沒有被比出來」 | 可能不在自動比對的日期範圍內，或員工不在比對名單（R3、R4） | 排程紀錄中該次實際比對的區間；員工的刷卡設定、離職留停日 |
| 「排程寄信說好幾小時沒有刷卡資料」 | 代表排程沒讀到符合規則的檔案，不一定是卡鐘真的沒資料 | 排程的讀取路徑與檔名規則、排程紀錄 |
| 「刪了打卡資料，異常怎麼沒變？」 | 刪除原始打卡不會自動重算（R1、R2） | 刪除後是否重新判定上下班並重新比對 |
| 「把已處理改回未處理」 | 畫面上做不到（R9） | 需由工程評估處理方式 |

## 在哪裡設定

| 設定 | 位置 |
|---|---|
| 刷卡資料核對（判定上下班、重新比對）、異常處理、批次轉處理 | 出勤管理 › 出勤刷卡 |
| 手機打卡 | 出勤管理 › 手機打卡 |
| 員工卡號 | 出勤管理 › 員工卡號管理 |
| 員工是否需要打卡 | 員工資料的「上下班刷卡設定」（選單位置待確認） |
| 容許遲到／早退、曠職上限、加班時段 | 班別設定（選單位置待確認） |
| 刷卡自動排程（轉入與比對的頻率、檔案路徑） | 刷卡排程設定頁（選單位置待確認） |
| 超時出勤原因的門檻與填寫期限 | 超時出勤原因設定（選單位置待確認） |

## 相關系統設定（config）

只列會改變上面規則結果的設定。「說明」「選項」取自 `HRMS_CONFIG`／`PORTAL_CONFIG` 的 DESCRIPTION、VALUE_OPTION；實際值各客戶不同，請查客戶環境。

| 設定（表） | 說明 | 選項 | 影響 |
|---|---|---|---|
| `CardOffTime`（HRMS_CONFIG） | 刷卡自動檢核截止時間 | 0：昨天、1：今天 | 自動排程比到哪一天（R4） |
| `CardMatchTwoMonth`（HRMS_CONFIG） | 自動比對區間可比到薪資計算假勤截止日前 2 個月 | Y／N | 自動排程的起日往前推 2 個月（R4） |
| `showOverTime`（HRMS_CONFIG） | 刷卡檢核是否要比對超時出勤 | Y／N | N 時比對後清除超時出勤異常（R6） |
| `SNR30_Flag`（HRMS_CONFIG） | 超時出勤原因填寫 | Y／N | 員工填原因而處理的異常，重新比對時會重算（R8） |
| `showOverTime`（PORTAL_CONFIG） | 個人假勤紀錄／異常刷卡是否顯示「超時出勤」異常項目 | 0：不顯示、1：顯示 | 為 0 時員工入口不顯示超時出勤，也不開放填原因 |
| `showOverTimeEdit`（PORTAL_CONFIG） | 超時出勤原因輸入後可再修改 | ON／OFF | OFF 時員工填過原因就不能再改（R10） |

> 員工入口能否填超時出勤原因，要三個設定同時開啟：PORTAL_CONFIG 的 `showOverTime`＝1、HRMS_CONFIG 的 `SNR30_Flag`＝Y，以及「超時出勤原因設定」中的「員工入口顯示」。

其他打卡相關設定（手機打卡、綁定手機、體溫欄位、匯出格式、曠職顯示文字等）不影響上述規則，可在 `HRMS_CONFIG` 依 DESCRIPTION 關鍵字查詢。

<!-- ================= 下半：給工程 ================= -->

## 程式地圖

只列到資料夾與主類別檔；函式與行號請查記憶（`recall 刷卡比對`，記憶 #92–#112、#134）。

| 區域 | 前端 | 後端 | 資料庫 |
|---|---|---|---|
| 出勤刷卡：匯入、核對、異常處理、批次轉處理 | `EHRMS/Personnel/PELAttend/PELAttend_CardMachine/` | `VB/EHRMS/Personnel/Cls/clsCardMachine.cls` | `HRMS_CARD_RAWDATA` → `HRMS_CARD_DATA` → `HRMS_CARD_DATA_MATCH` |
| 手機打卡 | `EHRMS/Personnel/PELAttend/PELAttend_MobilePunch/` | — | `HRMS_CARD_RAWDATA` |
| 員工卡號 | `EHRMS/Personnel/PELAttend/PELAttend_CardNo/` | — | — |
| 刷卡自動排程（轉入＋比對） | `EHRMS/Plugin/CardData/` | `VB/EHRMS/AuotEngine/clsAutoRun_CardData.cls` | `HRMS_CARD_AUTOJOB`、`HRMS_CARD_AUTOJOB_LOG` |
| 比對核心（異常判定、自動沖銷、保留規則） | — | `script/StoredProcedure/dbo.runCardMatch.SQL` | `HRMS_CARD_DATA_MATCH` |
| 員工入口：刷卡異常、超時出勤原因 | `EHRMS/ehrPortal/DEPT/Personal_Atten_Error*.asp` | `VB/PORTAL/Portal_Frontend/Cls/clsErrorCard.cls` | `HRMS_OT_REASON_SET` |

## 關鍵資料表

只列與規則有關的欄位；完整欄位請用 DB MCP `get_table_columns <表名>`，其他刷卡相關表（排程設定、卡鐘、手機打卡地點等）請用 `search_tables HRMS_CARD`／`HRMS_MOBILEPUNCH` 查詢。

**資料流**

```
卡鐘匯入(0)、手機打卡(3)                  網路刷卡(1)、簽核補單(2)
        │                                         │
        ▼                                         │
HRMS_CARD_RAWDATA（S01 門禁刷卡原始資料）          │
        │ 判定上下班（runCardDataRawdata）          │
        ▼                                         ▼
HRMS_CARD_DATA（S02 刷卡資料）◀───────────────────┘
        │ 刷卡比對（runCardMatch）＋班別、行事曆、假勤
        ▼
HRMS_CARD_DATA_MATCH（S04 刷卡資料比對檔）──▶ 報表、員工入口、假勤
```

> 依資料字典，`HRMS_CARD_RAWDATA` 的來源只有 0（卡鐘）與 3（手機），簽核補單的單號 `WF_NO` 只在 `HRMS_CARD_DATA`，所以網路刷卡與簽核補單是直接寫入 `HRMS_CARD_DATA`。員工入口的 `clsCardData.cls` 也會呼叫 `runCardDataRawdata`，呼叫原因尚未確認。

### HRMS_CARD_RAWDATA：門禁刷卡原始資料（S01）

一筆＝卡鐘或手機的一次打卡，尚未區分上下班。關鍵：`CARD_RAWDATA_ACTION` 識別碼產生（0：未產生、1：已產生（首、尾筆）、2：非首尾筆，R2）。

### HRMS_CARD_DATA：刷卡資料（S02）

一筆＝已判定上下班的一次刷卡，四種來源都在這裡。關鍵：

| 欄位 | 代碼 |
|---|---|
| `CARD_TYPE` 刷卡別 | 0：上班、1：下班、2：加班開始、3：加班結束、4：上班外出、5：外出返回 |
| `CARD_SOURCE` 刷卡資料來源 | 0：刷卡匯入、1：網路刷卡、2：workflow 補單、3：手機打卡（R1） |

### HRMS_CARD_DATA_MATCH：刷卡資料比對檔（S04）

一筆＝某員工某天「應刷的一次卡」與實際刷卡的比對結果；異常就記在這裡。關鍵：

| 欄位 | 代碼 |
|---|---|
| `CARD_DATA_CODE` 刷卡異常代碼 | 0：應刷未刷、1：遲到、2：早退、3：超時出勤、4：曠職、5：應加班未加班（畫面顯示已停用）、6：資料不全（R5） |
| `CARD_DATA_ACTION` 異常處理狀況 | 0：未處理、1：已處理 |
| `CARD_DATA_ACTION_TYPE` 已處理的來源 | 空白：人工、1：刷卡檢核自動、2：員工填寫異常原因（R8 依此決定保留或重算） |
| `CARD_DATA_START`／`_END` 異常起訖時間 | 自動沖銷以此計算涵蓋分鐘（R7） |

**config 讀取位置**（說明見上半「相關系統設定」）：

| 設定 | 讀取的程式 |
|---|---|
| `CardOffTime`、`CardMatchTwoMonth` | `clsAutoRun_CardData.cls` |
| HRMS_CONFIG `showOverTime`、`SNR30_Flag` | `dbo.runCardMatch.SQL`、`clsCardMachine.cls` |
| PORTAL_CONFIG `showOverTime`＋HRMS_CONFIG `SNR30_Flag`＋`OT_REASON_PORTAL_SHOW` | `clsErrorCard.cls`（`mfGetSNR30_Flag`） |
| PORTAL_CONFIG `showOverTimeEdit` | `ehrPortal/DEPT/Personal_Atten_Error*.asp` |


## 相關主題

- [[請假加班時數計算]]：請假涵蓋分鐘的算法影響 R7
- [[彈性工時]]：彈性班別的遲到早退判定影響 R5
- [[留職停薪]]：留停與復職的生效日影響 R3
