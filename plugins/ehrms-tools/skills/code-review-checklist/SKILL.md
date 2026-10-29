---
name: code-review-checklist
description: EHRMS 版本更新前的程式碼/SQL 重點審查清單，涵蓋 MSSQL 2008 相容性、SQL 冪等性(可重複執行)、孤立誤打字元、語法區塊完整性(If/End If 等)、衝突標記、編碼/換行噪音。Use when reviewing changes merged into a release branch (e.g. 202607_000), checking SQL scripts before deployment, verifying re-runnable SQL, or auditing a date range of merges for syntax problems.
---

執行環境：Claude Code 與 Codex 共用；工具名稱與檔案讀取方式依 [平台工具對照](../../references/platform-runtime.md) 選擇。
Git 操作需在 EHRMS_GIT repo 目錄內執行。

# EHRMS 版更程式碼/SQL 重點審查清單

對「即將釋出的 release 分支變更」或「某日期區間 merge 進去的 commit」做高訊號啟發式審查。
環境無 ASP/VB6/SQL 編譯器，**這是啟發式檢查、非編譯保證**：能抓明顯破壞語法/部署的模式，抓不到邏輯型錯誤(未宣告變數、型別不符)。

## 編碼鐵則（先讀，否則統計會錯）
- `.cls` / `.bas` = **Big5**：解碼用 `iconv -f big5 -t utf-8`。ASCII 關鍵字(BEGIN/Sub/If…)直接對原始位元組 grep 即可，不需解碼。
- `.asp` / `.js` = **UTF-8(常含 BOM)**；`.sql` 多為 **UTF-8**。
- ⚠️ **不要對 UTF-8 檔跑 `iconv -f big5`**：會在第一個非法位元組中斷，導致後段(如 END)漏算、產生假警報。SQL 的 BEGIN/END、CREATE 等關鍵字一律用 `LC_ALL=C grep` 對**原始位元組**統計。
- 相關記憶：Big5 .cls 修改法、ASP CRLF↔LF diff 噪音。

## 鎖定審查範圍

```bash
# 某日期區間 merge 進 release 的變更檔（依需要改日期/分支）
git log --since="2026-06-01 00:00" --until="2026-06-18 23:59" origin/202607_000 \
  --name-only --pretty=format: | grep -viE '^$' | sort -u > /tmp/changed_win.txt
grep -iE '\.(asp|cls|bas|js|sql)$' /tmp/changed_win.txt > /tmp/changed_code.txt
# 視窗起點(視窗前狀態)用於「前後比對」，取區間第一個 commit 的 parent 或 merge-base
```

---

## 檢查 1：SQL 是否相容 MSSQL 2008
2012+ 才有的函式在 2008 會直接報錯。掃視窗 `.sql`（原始位元組）：

```bash
pat='\bIIF[[:space:]]*\(|\bTRY_(CONVERT|CAST|PARSE)[[:space:]]*\(|\bCONCAT(_WS)?[[:space:]]*\(|\bFORMAT[[:space:]]*\(|\bSTRING_(SPLIT|AGG)[[:space:]]*\(|\bTHROW\b|\b(LAG|LEAD|FIRST_VALUE|LAST_VALUE)[[:space:]]*\(|\bEOMONTH[[:space:]]*\(|\bDATE(TIME)?FROMPARTS[[:space:]]*\(|\bCHOOSE[[:space:]]*\(|\bOFFSET\b.*\bFETCH\b|\bWITHIN[[:space:]]+GROUP\b|NEXT[[:space:]]+VALUE[[:space:]]+FOR|CREATE[[:space:]]+SEQUENCE|\bTRIM[[:space:]]*\(|DROP[[:space:]]+(TABLE|PROCEDURE|INDEX|VIEW|FUNCTION)[[:space:]]+IF[[:space:]]+EXISTS'
grep -iE '\.sql$' /tmp/changed_code.txt | while read f; do
  h=$(git show "origin/202607_000:$f" 2>/dev/null | LC_ALL=C grep -inE "$pat")
  [ -n "$h" ] && { echo "⚠ $(basename $f)"; echo "$h" | sed 's/^/   L/'; }
done
```
注意：`\bTRIM(` 不會誤命中 `LTRIM/RTRIM`（無詞界）。2008 用 `LTRIM(RTRIM())`、`RAISERROR`(非 THROW)。

## 檢查 2：SQL 冪等性（版更後可重複執行）
EHRMS 部署 SQL **必須可重跑**。逐檔確認下列守衛：

| 物件類型 | 可重跑必備樣式 |
|---|---|
| Stored Procedure / Function | `IF EXISTS(... OBJECT_ID ...) DROP PROCEDURE` → `CREATE`；或 `IF NOT EXISTS 建空殼 → ALTER PROCEDURE` |
| 永久 TABLE | `IF NOT EXISTS(... IsUserTable) CREATE TABLE` |
| ALTER TABLE ADD 欄位 | 前置 `if NOT exists(... syscolumns/sys.columns ... B.name='欄名')` |
| 資料 INSERT | `IF NOT EXISTS(...)` / `WHERE NOT EXISTS` / `MERGE` 包住 |
| 暫存表 #temp | proc 內 `IF OBJECT_ID('tempdb..#x') IS NOT NULL DROP TABLE #x`（執行期，部署不影響）|

```bash
grep -iE '\.sql$' /tmp/changed_code.txt | while read f; do
  c=$(git show "origin/202607_000:$f" 2>/dev/null)   # 原始位元組，勿 iconv
  bn=$(basename "$f")
  crP=$(printf '%s\n' "$c" | LC_ALL=C grep -icE 'CREATE[[:space:]]+(PROCEDURE|PROC|FUNCTION|VIEW|TRIGGER)')
  guard=$(printf '%s\n' "$c" | LC_ALL=C grep -icE 'OBJECT_ID|IF[[:space:]]+(NOT[[:space:]]+)?EXISTS|DROP[[:space:]]+(PROCEDURE|PROC|FUNCTION|VIEW|TABLE)')
  ins=$(printf '%s\n' "$c" | LC_ALL=C grep -icE 'INSERT[[:space:]]+INTO')
  insG=$(printf '%s\n' "$c" | LC_ALL=C grep -icE 'NOT[[:space:]]+EXISTS|MERGE[[:space:]]')
  echo "$bn | 建proc:$crP guard:$guard | INSERT:$ins 防重:$insG"
done
```
判讀：建proc>0 必須 guard>0；**純資料 INSERT 腳本**(建proc=0)的每個部署期 INSERT 必須有防重。
proc **內部** 的 INSERT 是執行期、不需防重。`欄判` 注意系統可能用 `syscolumns`(非 `sys.columns`)，別誤報。

## 檢查 3：孤立誤打字元
新增行中整行只剩 1 個字母或可疑單一符號（真實案例：VBScript 區一行孤立 `d` 造成執行錯誤）。

```bash
git log --since="2026-06-01" --until="2026-06-18 23:59" origin/202607_000 -p --no-merges \
  -- '*.asp' '*.js' '*.sql' '*.cls' '*.bas' 2>/dev/null \
 | grep -aE '^\+' | grep -avE '^\+\+\+' | sed 's/\r$//' \
 | awk '{l=$0;sub(/^\+/,"",l);t=l;gsub(/^[ \t]+|[ \t]+$/,"",t);
    if(t ~ /^[A-Za-z]$/ && t!~/^(a|A|I)$/) print "孤立: ["t"]"}' | sort | uniq -c
# 命中後用 git log -S 或 git blame 定位 commit。註：單獨 ' 是空白 VBScript 註解，正常。
```

## 檢查 4：語法區塊完整性（缺 End If / Next / End Sub 等）
VB 系(.cls/.bas/.asp 內 VBScript)區塊配對。**用「視窗前 vs 現在」比對**，只揪新引入失衡（避免單行 If、`Then '註解` 造成的既有假陽性）。

```bash
pre=<視窗前commit>     # 例如區間 merge-base
grep -iE '\.(asp|cls|bas)$' /tmp/changed_code.txt | while read f; do
  for tag in now pre; do
    ref=$([ $tag = now ] && echo origin/202607_000 || echo $pre)
    raw=$(git show "$ref:$f" 2>/dev/null); [ -z "$raw" ] && continue
    case "$f" in *.cls|*.bas) c=$(printf '%s' "$raw" | iconv -f big5 -t utf-8 2>/dev/null);; *) c=$raw;; esac
    subO=$(printf '%s\n' "$c" | grep -icE '^[[:space:]]*(Private|Public|Friend|Static)?[[:space:]]*Sub[[:space:]]+'); subE=$(printf '%s\n' "$c" | grep -icE '^[[:space:]]*End Sub')
    fnO=$(printf '%s\n' "$c"  | grep -icE '^[[:space:]]*(Private|Public|Friend|Static)?[[:space:]]*Function[[:space:]]+'); fnE=$(printf '%s\n' "$c" | grep -icE '^[[:space:]]*End Function')
    doO=$(printf '%s\n' "$c"  | grep -icE '^[[:space:]]*Do([[:space:]]|$)'); doE=$(printf '%s\n' "$c" | grep -icE '^[[:space:]]*Loop'); fo=$(printf '%s\n' "$c" | grep -icE '^[[:space:]]*For\b'); fnx=$(printf '%s\n' "$c" | grep -icE '^[[:space:]]*Next\b')
    eval "${tag}=\"Sub:\$((subO-subE)) Fn:\$((fnO-fnE)) Do:\$((doO-doE)) For:\$((fo-fnx))\""
  done
  [ "$now" != "$pre" ] && echo "⚠ $(basename $f)  前[$pre] → 現[$now]  （需開 diff 人工確認）"
done
```
ASP 另查 `<%`/`%>` 標籤數須相等（不等＝解析錯誤）。
**重要**：`Declare Function`(API 宣告) 無 End Function、單行 `If…Then x`、`Then '註解` 都會讓計數偏移 → 失衡一律**開 diff 人工確認**是否真缺，勿直接判定。

## 檢查 5：殘留衝突標記
```bash
git grep -nE '^(<<<<<<<|>>>>>>>|=======)$' origin/202607_000 -- '*.asp' '*.cls' '*.bas' '*.js' '*.sql'
```

## 檢查 6：編碼/換行噪音（非語法錯誤但會淹沒 review）
- `.asp` 整檔 diff(每行都變) 多半是 CRLF↔LF：`git diff <base> <ref> -w --stat` 行數遠小於原始即是。修法見 ASP CRLF 記憶(`hash-object --no-filters` 注入 CRLF blob 對齊基準)。
- `.cls`/`.bas` 全檔變更多半是被 UTF-8 工具重存破壞 Big5：用 Big5(codepage 950) 或 byte-safe 方式處理。
- 程式碼行誤用**全形標點/全形空格/智慧引號**：只在字串字面值內者無害，在程式語法處才是錯誤。

---

## 輸出格式
依「檢查 1~6」逐項回報 ✅/⚠，命中處附 `檔名:行號` 與一句說明，並對 ⚠ 用 `git blame`/`git log -S` 列出**可能造成問題的 commit**。發現問題且需對照其他 release 分支時，向使用者確認正確分支名(repo 可能只有單一 2026 release 分支)。
