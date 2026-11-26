"""Jira API client for making authenticated requests."""

import json as _json
import os
import re
from datetime import date, datetime
from typing import Any, Optional
import httpx
from .config import config, JiraCredentials


class JiraClient:
    """Jira API 客戶端"""

    def __init__(self, credentials: Optional[JiraCredentials] = None):
        if credentials:
            self.base_url = credentials.api_base_url
            self.auth = credentials.auth
        else:
            self.base_url = config.api_base_url
            self.auth = config.auth
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict] = None,
        json_data: Optional[dict] = None
    ) -> dict[str, Any]:
        """執行 HTTP 請求"""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"

        # connect 逾時設短一點，離線時快速失敗；read 保留較長時間給正常的慢回應
        timeout = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)

        try:
            async with httpx.AsyncClient() as client:
                response = await client.request(
                    method=method,
                    url=url,
                    auth=self.auth,
                    headers=self.headers,
                    params=params,
                    json=json_data,
                    timeout=timeout
                )
        except httpx.TransportError as e:
            raise ValueError(
                "無法連線到 Jira（可能目前不在公司網路環境，或 JIRA_BASE_URL 設定錯誤），"
                f"請確認網路連線後再試。（原始錯誤：{e}）") from e

        # 根據 Jira REST API v3 標準處理錯誤
        if response.status_code == 400:
            raise ValueError(f"請求無效: {response.text}")
        elif response.status_code == 401:
            raise ValueError("未授權: 請檢查 API Token 是否有效")
        elif response.status_code == 403:
            raise ValueError("權限不足: 請檢查用戶權限")
        elif response.status_code == 404:
            raise ValueError("資源未找到")
        elif response.status_code == 410:
            raise ValueError("資源已過期或不可用")
        elif response.status_code >= 400:
            response.raise_for_status()

        # PUT issue、POST transitions 成功時回 204 無內容
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    async def get(self, endpoint: str, params: Optional[dict] = None) -> dict[str, Any]:
        """執行 GET 請求"""
        return await self._request("GET", endpoint, params=params)

    async def post(self, endpoint: str, json_data: dict) -> dict[str, Any]:
        """執行 POST 請求"""
        return await self._request("POST", endpoint, json_data=json_data)

    async def put(self, endpoint: str, json_data: dict) -> dict[str, Any]:
        """執行 PUT 請求"""
        return await self._request("PUT", endpoint, json_data=json_data)

    # search_issues detail="full" 抓取的欄位。
    # 不可用 *all：EHRMSONE 掛了數百個共用 customfield（多為流程範本文字），
    # 單張 Issue 的完整 JSON 可達 30 萬字元，會超過 MCP 輸出上限。
    DEFAULT_SEARCH_FIELDS = [
        "summary", "status", "assignee", "reporter", "priority",
        "issuetype", "created", "updated", "duedate", "labels",
        "components", "description", "attachment", "parent",
    ]
    # detail="list"（預設）的最小清單欄位：掃清單找目標用，細節靠 get_issue 查單筆
    LIST_FIELDS = ["summary", "status", "assignee", "updated"]
    # get_issue 預設加抓的 EHRMSONE 高頻自訂欄位（option 物件只取文字，回傳用友善鍵名）
    ISSUE_CUSTOM_FIELDS = {
        "customfield_12722": "customer",      # 客戶名稱（統編＋公司名）
        "customfield_11949": "severity",      # 嚴重度（Fatal…）
        "customfield_12362": "source",        # 問題來源（客戶…）
        "customfield_11820": "category",      # 單據類別（維運…）
        "customfield_11901": "issue_nature",  # 問題類型（一般問題…）
        "customfield_12364": "problem_type",  # 問題性質（線上問題…）
    }
    MAX_SEARCH_RESULTS = 50   # 單次搜尋上限（防上下文爆量）
    MAX_DESC_CHARS = 600      # 清單場景的描述截斷長度；全文請查單筆
    MAX_EXTRA_CHARS = 800     # 額外指定欄位的截斷長度
    MAX_FULL_DESC_CHARS = 20000  # get_issue 單筆全文的保險上限

    async def search_issues(
        self,
        jql: str,
        fields: Optional[list[str]] = None,
        max_results: int = 50,
        next_page_token: Optional[str] = None,
        expand: Optional[list[str]] = None,
        detail: str = "list"
    ) -> dict[str, Any]:
        """
        使用 JQL 搜尋 Issues（v3 search/jql 端點）

        分頁：search/jql 使用 nextPageToken（不支援 startAt）——
        回傳含 next_page_token 時，帶回原參數即可取得下一頁。

        預設精簡、按需加深：
        - detail="list"（預設）：每筆只回 key/summary/status/assignee/updated，
          掃清單找目標用；細節用 get_issue 查單筆
        - detail="full"：回 14 個精簡欄位＋描述截斷（MAX_DESC_CHARS）
        fields 只能「加欄位」，追加欄位一律 ADF→純文字並截斷，絕不回傳原始 JSON。

        Args:
            jql: JQL 查詢字串
            fields: 額外欄位（如 customfield_12722），會附加在精簡結構上
            max_results: 單頁筆數（上限 MAX_SEARCH_RESULTS）
            next_page_token: 上一頁回傳的翻頁 token
            expand: 要展開的資源
            detail: "list"（預設）/ "full"
        """
        max_results = min(int(max_results or 50), self.MAX_SEARCH_RESULTS)
        full = (detail == "full")
        extra = [x for x in (fields or []) if x and not x.startswith("*")]
        base = self.DEFAULT_SEARCH_FIELDS if full else self.LIST_FIELDS
        params = {
            "jql": jql,
            "maxResults": max_results,
            "fields": ",".join(base + extra),
        }
        if next_page_token:
            params["nextPageToken"] = next_page_token
        if expand:
            params["expand"] = ",".join(expand)

        result = await self.get("search/jql", params=params)

        if full:
            issues = [self._simplify_issue(i, extra) for i in result.get("issues", [])]
        else:
            issues = [self._list_row(i, extra) for i in result.get("issues", [])]
        out: dict[str, Any] = {"count": len(issues), "issues": issues}
        if not full:
            out["note"] = "精簡清單模式；單筆細節用 get_issue，整批要更多欄位用 detail=\"full\""
        if not result.get("isLast", True) and result.get("nextPageToken"):
            out["next_page_token"] = result["nextPageToken"]
            out["more"] = "尚有更多結果，帶 next_page_token 再查一次可取得下一頁"
        return out

    def _list_row(self, issue: dict[str, Any],
                  extra_fields: Optional[list[str]] = None) -> dict[str, Any]:
        """detail=\"list\" 的最小清單列。"""
        f = issue.get("fields", {})
        row = {
            "key": issue.get("key"),
            "summary": f.get("summary", ""),
            "status": (f.get("status") or {}).get("name", ""),
            "assignee": (f.get("assignee") or {}).get("displayName", ""),
            "updated": (f.get("updated") or "")[:10],
        }
        for name in extra_fields or []:
            val = self._field_to_text(f.get(name))
            if isinstance(val, str) and len(val) > self.MAX_EXTRA_CHARS:
                val = val[:self.MAX_EXTRA_CHARS] + "…（已截斷）"
            row[name] = val
        return row

    @classmethod
    def _field_to_text(cls, v: Any) -> Any:
        """任意 Jira 欄位值 → 精簡文字（ADF 轉純文字、物件取名稱）"""
        if v is None:
            return None
        if isinstance(v, dict):
            if v.get("type") == "doc":
                return cls._adf_to_text(v)
            for k in ("displayName", "name", "value"):
                if k in v:
                    return v[k]
            return _json.dumps(v, ensure_ascii=False)[:300]
        if isinstance(v, list):
            return [cls._field_to_text(x) for x in v]
        return v

    def _simplify_issue(self, issue: dict[str, Any],
                        extra_fields: Optional[list[str]] = None,
                        desc_limit: Optional[int] = None) -> dict[str, Any]:
        """將 Issue 壓縮為精簡結構，ADF 描述轉純文字並截斷。
        desc_limit：描述截斷長度；不填用清單預設 MAX_DESC_CHARS（附改查單筆提示）。"""
        f = issue.get("fields", {})
        desc = self._adf_to_text(f.get("description")) if f.get("description") else ""
        limit = self.MAX_DESC_CHARS if desc_limit is None else desc_limit
        if limit and len(desc) > limit:
            hint = "，需要全文請用 get_issue 查單筆" if desc_limit is None else ""
            desc = desc[:limit] + f"…（已截斷，全文 {len(desc)} 字{hint}）"
        simplified = {
            "key": issue.get("key"),
            "summary": f.get("summary", ""),
            "status": (f.get("status") or {}).get("name", ""),
            "assignee": (f.get("assignee") or {}).get("displayName", ""),
            "reporter": (f.get("reporter") or {}).get("displayName", ""),
            "priority": (f.get("priority") or {}).get("name", ""),
            "issue_type": (f.get("issuetype") or {}).get("name", ""),
            "created": (f.get("created") or "")[:10],
            "updated": (f.get("updated") or "")[:10],
            "duedate": f.get("duedate"),
            "labels": f.get("labels") or [],
            "components": [c.get("name", "") for c in f.get("components") or []],
            "description": desc,
            "attachments": [a.get("filename", "") for a in f.get("attachment") or []],
        }
        parent = f.get("parent")
        if parent:
            simplified["parent"] = parent.get("key")
        for name in extra_fields or []:
            val = self._field_to_text(f.get(name))
            if isinstance(val, str) and len(val) > self.MAX_EXTRA_CHARS:
                val = val[:self.MAX_EXTRA_CHARS] + "…（已截斷）"
            simplified[name] = val
        return simplified

    @staticmethod
    def _adf_to_text(adf: Any) -> str:
        """將 Atlassian Document Format (ADF) 轉為純文字。

        涵蓋維運單常見節點：段落/標題/清單（含巢狀、起始編號）/程式碼/引用/
        panel/表格/圖片與附件（輸出佔位符，AI 才知道有圖可抓）/mention/
        hardBreak/分隔線/expand。未知節點遞迴取其子內容，確保不吞字。"""
        if not isinstance(adf, dict):
            return str(adf) if adf is not None else ""
        if "content" not in adf:
            return ""

        def media_tag(n):
            a = n.get("attrs", {})
            name = a.get("alt") or (a.get("id") or "")[:8]
            return f"[圖片/附件: {name}]"

        def inline(nodes):
            parts = []
            for n in nodes or []:
                t = n.get("type")
                if t == "text":
                    parts.append(n.get("text", ""))
                elif t == "hardBreak":
                    parts.append("\n")
                elif t == "mention":
                    parts.append(n.get("attrs", {}).get("text") or "@?")
                elif t == "emoji":
                    parts.append(n.get("attrs", {}).get("text", ""))
                elif t == "inlineCard":
                    parts.append(n.get("attrs", {}).get("url", ""))
                elif t == "media":
                    parts.append(media_tag(n))
                else:
                    parts.append(inline(n.get("content")))
            return "".join(parts)

        def list_item(li, indent, prefix):
            out = []
            for b in blocks(li.get("content"), ""):
                if not out:
                    out.append(indent + prefix + b)
                else:
                    out.append(indent + " " * len(prefix) + b)
            return out or [indent + prefix.rstrip()]

        def blocks(nodes, indent=""):
            lines = []
            for n in nodes or []:
                t = n.get("type")
                c = n.get("content")
                if t == "paragraph":
                    lines.append(indent + inline(c))
                elif t == "heading":
                    lv = n.get("attrs", {}).get("level", 1)
                    lines.append(indent + "#" * lv + " " + inline(c))
                elif t == "bulletList":
                    for li in c or []:
                        lines.extend(list_item(li, indent, "- "))
                elif t == "orderedList":
                    start = n.get("attrs", {}).get("order", 1)
                    for i, li in enumerate(c or []):
                        lines.extend(list_item(li, indent, f"{start + i}. "))
                elif t == "codeBlock":
                    lines.append(indent + "```")
                    lines.extend(indent + ln for ln in inline(c).split("\n"))
                    lines.append(indent + "```")
                elif t == "blockquote":
                    lines.extend(indent + "> " + ln for ln in blocks(c))
                elif t == "panel":
                    ptype = n.get("attrs", {}).get("panelType", "info")
                    lines.append(indent + f"【{ptype}】")
                    lines.extend(blocks(c, indent))
                elif t == "table":
                    for row in c or []:
                        cells = [" ".join(blocks(cell.get("content"))).strip()
                                 for cell in row.get("content") or []]
                        lines.append(indent + "| " + " | ".join(cells) + " |")
                elif t in ("mediaSingle", "mediaGroup"):
                    lines.append(indent + inline(c))
                elif t == "rule":
                    lines.append(indent + "---")
                elif t in ("expand", "nestedExpand"):
                    lines.append(indent + "▸ " + n.get("attrs", {}).get("title", ""))
                    lines.extend(blocks(c, indent))
                else:
                    txt = inline(c)
                    if txt:
                        lines.append(indent + txt)
            return lines

        return "\n".join(blocks(adf["content"]))

    async def get_issue(
        self,
        issue_key: str,
        fields: Optional[list[str]] = None,
        plain_text: bool = True,
        expand: Optional[str] = None
    ) -> dict[str, Any]:
        """
        取得單一 Issue 的詳細資訊，支援欄位過濾與 ADF 轉純文字

        Args:
            issue_key: Issue 的 Key (例如: PROJ-123)
            fields: 指定要回傳的欄位，不填則回傳全部
            plain_text: 是否將 ADF 轉為純文字（預設 True）
            expand: 要展開的額外資訊 (例如: changelog,renderedFields)
        """
        params = {}
        if fields:
            params["fields"] = ",".join(fields)
        else:
            params["fields"] = "*all"
        if expand:
            params["expand"] = expand

        issue = await self.get(f"issue/{issue_key}", params=params)

        if plain_text:
            adf_to_text = self._adf_to_text

            for k, v in issue.get("fields", {}).items():
                if isinstance(v, dict) and v.get("type") == "doc" and v.get("version") == 1:
                    issue["fields"][k] = adf_to_text(v)

        return issue

    async def get_issue_summary(self, issue_key: str) -> dict[str, Any]:
        """
        取得 Issue 的基本摘要資訊（輕量查詢）

        Args:
            issue_key: Issue 的 Key (例如: PROJ-123)

        Returns:
            dict: 包含 key, summary, status, assignee, priority, issue_type, created, updated
        """
        summary_fields = ["summary", "status", "assignee", "priority", "issuetype", "created", "updated"]
        issue = await self.get_issue(issue_key, fields=summary_fields, plain_text=False)
        fields = issue.get("fields", {})

        created = (fields.get("created") or "")[:10]
        updated = (fields.get("updated") or "")[:10]

        return {
            "key": issue.get("key"),
            "summary": fields.get("summary", ""),
            "status": (fields.get("status") or {}).get("name", ""),
            "assignee": (fields.get("assignee") or {}).get("displayName", ""),
            "priority": (fields.get("priority") or {}).get("name", ""),
            "issue_type": (fields.get("issuetype") or {}).get("name", ""),
            "created": created,
            "updated": updated,
        }

    MAX_COMMENTS = 500  # limit 上限（「全部」的保險絲）

    async def get_issue_comments(self, issue_key: str, limit: int = 20) -> dict[str, Any]:
        """取得 Issue 最新的 N 筆評論（新→舊，自動翻頁＋精簡輸出）。

        查案時最新評論才反映現況，故用 orderBy=-created 由新到舊取，
        預設只拿 limit 筆——不再「全拿」灌爆上下文；要更早的討論調大 limit。
        body 由 ADF 轉純文字，avatar/accountId 等噪音一律不回傳。"""
        limit = max(1, min(int(limit or 20), self.MAX_COMMENTS))
        start, total, comments = 0, 0, []
        while len(comments) < limit:
            page = await self.get(
                f"issue/{issue_key}/comment",
                params={"startAt": start, "orderBy": "-created",
                        "maxResults": min(100, limit - len(comments))})
            total = page.get("total", 0)
            batch = page.get("comments", [])
            for c in batch:
                item = {
                    "author": (c.get("author") or {}).get("displayName", ""),
                    "created": (c.get("created") or "")[:16].replace("T", " "),
                    "body": self._adf_to_text(c.get("body")),
                }
                updated = (c.get("updated") or "")[:16].replace("T", " ")
                if updated and updated != item["created"]:
                    item["updated"] = updated
                comments.append(item)
            start += len(batch)
            if not batch or start >= total:
                break
        out: dict[str, Any] = {"issue": issue_key, "total": total,
                               "order": "新→舊", "comments": comments}
        if len(comments) < total:
            out["note"] = f"評論共 {total} 筆，回傳最新 {len(comments)} 筆；要更早的討論請調大 limit"
        return out

    async def get_issue_basic(self, issue_key: str,
                              comments_limit: int = 10,
                              fields: Optional[list[str]] = None) -> dict[str, Any]:
        """
        取得單張 Issue 的完整內容（單筆全文視角）

        回傳精簡欄位結構＋完整描述（ADF→純文字，保險上限 MAX_FULL_DESC_CHARS）
        ＋EHRMSONE 高頻自訂欄位（客戶/嚴重度等，友善鍵名）
        ＋最新 comments_limit 筆評論（新→舊）。
        search_issues 截斷描述後指引到本方法，這裡必須給得出全文。

        Args:
            issue_key: Issue 的 Key (例如: PROJ-123)
            comments_limit: 評論筆數（預設 10；0=不帶評論）
            fields: 額外追加的欄位（如其他 customfield_*），轉純文字並截斷

        Returns:
            dict: 精簡欄位 + description 全文 + 自訂欄位 + comment_total + comments[]（新→舊）
        """
        extra = [x for x in (fields or []) if x and not x.startswith("*")]
        fetch = (list(self.DEFAULT_SEARCH_FIELDS)
                 + list(self.ISSUE_CUSTOM_FIELDS) + extra)
        issue = await self.get_issue(issue_key, fields=fetch, plain_text=False)
        data = self._simplify_issue(issue, extra_fields=extra,
                                    desc_limit=self.MAX_FULL_DESC_CHARS)

        f = issue.get("fields", {})
        for cid, alias in self.ISSUE_CUSTOM_FIELDS.items():
            val = self._field_to_text(f.get(cid))
            if isinstance(val, list):
                val = "、".join(str(x) for x in val if x)
            if val:
                data[alias] = val

        if comments_limit and int(comments_limit) > 0:
            result = await self.get_issue_comments(issue_key, limit=comments_limit)
            data["comment_total"] = result.get("total", 0)
            data["comments"] = result.get("comments", [])
            data["comments_order"] = "新→舊"
            if result.get("note"):
                data["comments_note"] = result["note"]
        return data

    async def download_attachment_content(self, content_url: str) -> bytes:
        """
        下載附件內容

        Args:
            content_url: 附件的下載 URL

        Returns:
            附件的二進位內容
        """
        timeout = httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0)
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                response = await client.get(
                    content_url,
                    auth=self.auth,
                    timeout=timeout
                )
                response.raise_for_status()
                return response.content
        except httpx.TransportError as e:
            raise ValueError(
                "無法連線到 Jira（可能目前不在公司網路環境，或 JIRA_BASE_URL 設定錯誤），"
                f"請確認網路連線後再試。（原始錯誤：{e}）") from e

    async def download_attachment_to_local(
        self,
        content_url: str,
        filename: str,
        download_folder: str = "downloads"
    ) -> dict[str, Any]:
        """
        下載附件到本地資料夾

        Args:
            content_url: 附件的下載 URL
            filename: 檔案名稱
            download_folder: 下載資料夾路徑
        """
        try:
            os.makedirs(download_folder, exist_ok=True)
            content = await self.download_attachment_content(content_url)
            file_path = os.path.join(download_folder, filename)
            with open(file_path, 'wb') as f:
                f.write(content)
            return {
                "success": True,
                "file_path": file_path,
                "file_size": len(content),
                "filename": filename
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_custom_fields(self) -> list[dict[str, Any]]:
        """取得所有自訂欄位的定義"""
        return await self.get("field")

    @staticmethod
    def _text_to_adf(text: str) -> dict[str, Any]:
        """純文字 → ADF。逐行拆成段落：單一 text node 內的 \\n 不會被 Jira 渲染成換行"""
        content = []
        for line in text.split("\n"):
            para: dict[str, Any] = {"type": "paragraph", "content": []}
            if line:
                para["content"] = [{"type": "text", "text": line}]
            content.append(para)
        return {"version": 1, "type": "doc", "content": content}

    async def add_comment(self, issue_key: str, body: str) -> dict[str, Any]:
        """
        對 Issue 新增評論

        Args:
            issue_key: Issue 的 Key (例如: PROJ-123)
            body: 評論內容（純文字，將轉為 ADF 格式）
        """
        return await self.post(f"issue/{issue_key}/comment",
                               json_data={"body": self._text_to_adf(body)})

    # ── 寫入：修改欄位（白名單） ─────────────────────────────
    _ACCOUNT_ID_RE = re.compile(r"^[0-9a-f]{24}$|^\d+:[0-9a-f-]{36}$", re.I)
    UPDATE_RESULT_FIELDS = ["summary", "status", "assignee", "duedate",
                            "labels", "components"]

    @staticmethod
    def _clean_names(values: Optional[list[str]], kind: str) -> list[str]:
        """去空白、去重；標籤不可含空白（Jira 限制）。"""
        out: list[str] = []
        for v in values or []:
            v = str(v).strip()
            if not v:
                continue
            if kind == "標籤" and re.search(r"\s", v):
                raise ValueError(f"標籤不可含空白：「{v}」，未做任何修改")
            if v not in out:
                out.append(v)
        return out

    async def _resolve_assignee(self, assignee: str) -> Optional[str]:
        """email／accountId → accountId；空字串代表取消指派（回 None）。"""
        assignee = assignee.strip()
        if not assignee:
            return None
        if self._ACCOUNT_ID_RE.match(assignee):
            return assignee
        if "@" not in assignee:
            raise ValueError(
                f"assignee 請填 email 或 accountId（收到「{assignee}」），未做任何修改；"
                "可先用 get_user_info 查詢")
        users = await self.get("user/search",
                               params={"query": assignee, "maxResults": 10})
        exact = [u for u in users
                 if (u.get("emailAddress") or "").lower() == assignee.lower()]
        candidates = exact or users
        if len(candidates) == 1:
            return candidates[0]["accountId"]
        if not candidates:
            raise ValueError(f"查無使用者「{assignee}」，未做任何修改")
        names = [f"{u.get('displayName')}（{u.get('accountId')}）" for u in candidates]
        raise ValueError(
            f"「{assignee}」對到多位使用者，未做任何修改，請改填 accountId：{names}")

    async def update_issue_fields(
        self,
        issue_key: str,
        add_labels: Optional[list[str]] = None,
        remove_labels: Optional[list[str]] = None,
        add_components: Optional[list[str]] = None,
        remove_components: Optional[list[str]] = None,
        assignee: Optional[str] = None,
        duedate: Optional[str] = None,
        summary: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        以白名單更新 Issue 欄位（PUT issue/{key}）。

        - labels／components：用 update 語法 add／remove，不覆蓋既有值
        - assignee：email 或 accountId；空字串＝取消指派
        - duedate：YYYY-MM-DD；空字串＝清空
        - summary：整段取代（不可空白）
        參數為 None 表示不動該欄位。回傳修改前後的欄位值供核對。
        """
        add_l = self._clean_names(add_labels, "標籤")
        rm_l = self._clean_names(remove_labels, "標籤")
        add_c = self._clean_names(add_components, "元件")
        rm_c = self._clean_names(remove_components, "元件")
        for kind, a, r in (("標籤", add_l, rm_l), ("元件", add_c, rm_c)):
            both = set(a) & set(r)
            if both:
                raise ValueError(f"{kind}同時出現在新增與移除：{sorted(both)}，未做任何修改")

        update: dict[str, list] = {}
        if add_l or rm_l:
            update["labels"] = ([{"add": x} for x in add_l]
                                + [{"remove": x} for x in rm_l])
        if add_c or rm_c:
            update["components"] = ([{"add": {"name": x}} for x in add_c]
                                    + [{"remove": {"name": x}} for x in rm_c])

        fields: dict[str, Any] = {}
        if summary is not None:
            summary = summary.strip()
            if not summary:
                raise ValueError("summary 不可空白，未做任何修改")
            fields["summary"] = summary
        if duedate is not None:
            duedate = duedate.strip()
            if duedate:
                try:
                    date.fromisoformat(duedate)
                except ValueError:
                    raise ValueError(
                        f"duedate 格式須為 YYYY-MM-DD（收到「{duedate}」），未做任何修改")
            fields["duedate"] = duedate or None
        if assignee is not None:
            fields["assignee"] = {"accountId": await self._resolve_assignee(assignee)}

        if not update and not fields:
            raise ValueError("沒有指定任何要修改的欄位")

        before = await self._update_snapshot(issue_key)
        body: dict[str, Any] = {}
        if update:
            body["update"] = update
        if fields:
            body["fields"] = fields
        await self.put(f"issue/{issue_key}", json_data=body)
        after = await self._update_snapshot(issue_key)
        changed = [k for k in after if after[k] != before.get(k)]
        return {"issue": issue_key, "updated": True, "changed_fields": changed,
                "before": before, "after": after}

    async def _update_snapshot(self, issue_key: str) -> dict[str, Any]:
        issue = await self.get_issue(issue_key, fields=self.UPDATE_RESULT_FIELDS,
                                     plain_text=False)
        f = issue.get("fields", {})
        return {
            "summary": f.get("summary", ""),
            "status": (f.get("status") or {}).get("name", ""),
            "assignee": (f.get("assignee") or {}).get("displayName", ""),
            "duedate": f.get("duedate"),
            "labels": sorted(f.get("labels") or []),
            "components": sorted(c.get("name", "") for c in f.get("components") or []),
        }

    # ── 寫入：狀態流轉（transition） ─────────────────────────
    async def _current_status(self, issue_key: str) -> dict[str, Any]:
        issue = await self.get_issue(issue_key, fields=["summary", "status"],
                                     plain_text=False)
        f = issue.get("fields", {})
        return {"key": issue.get("key") or issue_key,
                "summary": f.get("summary", ""),
                "status": (f.get("status") or {}).get("name", "")}

    async def _transitions(self, issue_key: str) -> list[dict[str, Any]]:
        data = await self.get(f"issue/{issue_key}/transitions",
                              params={"expand": "transitions.fields"})
        out = []
        for t in data.get("transitions", []):
            required = [
                (meta or {}).get("name") or fid
                for fid, meta in (t.get("fields") or {}).items()
                if (meta or {}).get("required")
            ]
            item: dict[str, Any] = {"id": str(t.get("id")), "name": t.get("name", ""),
                                    "to_status": (t.get("to") or {}).get("name", "")}
            if required:
                item["required_fields"] = required
            out.append(item)
        return out

    async def list_transitions(self, issue_key: str) -> dict[str, Any]:
        """列出 Issue 目前狀態與可執行的 transition（唯讀）。"""
        cur = await self._current_status(issue_key)
        transitions = await self._transitions(issue_key)
        out: dict[str, Any] = {
            "issue": cur["key"], "summary": cur["summary"],
            "current_status": cur["status"], "transitions": transitions,
            "next_step": ("要執行時，先向使用者確認「單號、目前狀態 → 目標狀態」，"
                          "取得同意後再呼叫 transition_issue（帶 transition_id 與 "
                          "expected_status=目前狀態）。"),
        }
        if any(t.get("required_fields") for t in transitions):
            out["note"] = ("有 required_fields 的 transition 需要填欄位，"
                           "本工具無法代填，執行可能失敗，請改在 Jira 畫面操作")
        return out

    async def transition_issue(self, issue_key: str, transition_id: str,
                               expected_status: str) -> dict[str, Any]:
        """
        執行 transition。執行前核對目前狀態仍是 expected_status、
        且 transition_id 仍在可用清單中，任一不符即拒絕、不送出。
        """
        transition_id = str(transition_id or "").strip()
        expected = (expected_status or "").strip()
        if not transition_id or not expected:
            raise ValueError("transition_id 與 expected_status 都必填；請先用 list_transitions 查詢")

        cur = await self._current_status(issue_key)
        if cur["status"].casefold() != expected.casefold():
            raise ValueError(
                f"{cur['key']} 目前狀態是「{cur['status']}」，與確認時的「{expected}」不同，"
                "未執行。請重新 list_transitions 並再次向使用者確認")

        transitions = await self._transitions(issue_key)
        target = next((t for t in transitions if t["id"] == transition_id), None)
        if not target:
            avail = [f"{t['id']}:{t['name']}→{t['to_status']}" for t in transitions]
            raise ValueError(
                f"{cur['key']} 目前沒有 id={transition_id} 的 transition，未執行。可用：{avail}")

        await self.post(f"issue/{issue_key}/transitions",
                        json_data={"transition": {"id": transition_id}})
        after = await self._current_status(issue_key)
        return {"issue": cur["key"], "summary": cur["summary"],
                "transition": target["name"],
                "from_status": cur["status"], "to_status": after["status"]}

    # ── 寫入：記錄工時（worklog） ────────────────────────────
    _TIME_SPENT_RE = re.compile(r"^\d+(\.\d+)?[wdhm]( \d+(\.\d+)?[wdhm])*$")

    @staticmethod
    def _jira_datetime(value: Optional[str]) -> str:
        """YYYY-MM-DD 或 YYYY-MM-DD HH:MM（本機時區）→ Jira worklog started 格式。
        不填＝現在；只給日期＝當天 09:00。"""
        value = (value or "").strip()
        if not value:
            dt = datetime.now().astimezone()
        else:
            try:
                dt = datetime.fromisoformat(value.replace("T", " "))
            except ValueError:
                raise ValueError(
                    f"started 格式須為 YYYY-MM-DD 或 YYYY-MM-DD HH:MM（收到「{value}」），未記錄工時")
            if len(value) == 10:
                dt = dt.replace(hour=9)
            if dt.tzinfo is None:
                dt = dt.astimezone()
        return dt.strftime("%Y-%m-%dT%H:%M:%S.000%z")

    async def add_worklog(self, issue_key: str, time_spent: str,
                          started: Optional[str] = None,
                          comment: Optional[str] = None) -> dict[str, Any]:
        """
        對 Issue 記錄工時（POST issue/{key}/worklog）。

        Args:
            time_spent: Jira 時間格式，例如 "1h 30m"、"2h"、"45m"、"1d"
            started: 開始時間 YYYY-MM-DD 或 YYYY-MM-DD HH:MM（本機時區），不填＝現在
            comment: 工時說明（純文字，選填）
        """
        time_spent = " ".join((time_spent or "").split())
        if not self._TIME_SPENT_RE.match(time_spent):
            raise ValueError(
                f"time_spent 格式錯誤（收到「{time_spent}」），例：1h 30m、2h、45m、1d；未記錄工時")
        body: dict[str, Any] = {"timeSpent": time_spent,
                                "started": self._jira_datetime(started)}
        if comment and comment.strip():
            body["comment"] = self._text_to_adf(comment)
        result = await self.post(f"issue/{issue_key}/worklog", json_data=body)
        return {
            "issue": issue_key,
            "worklog_id": result.get("id"),
            "time_spent": result.get("timeSpent", time_spent),
            "started": result.get("started", body["started"]),
            "author": (result.get("author") or {}).get("displayName", ""),
        }

    async def get_issue_changelog(self, issue_key: str, limit: int = 50,
                                  fields: Optional[list[str]] = None,
                                  max_pages: int = 10) -> dict[str, Any]:
        """取得 Issue 變更歷史（最新優先＋精簡輸出＋可過濾欄位）。

        changelog 端點由舊到新分頁（無法反序），策略：
        - 無 fields 過濾：先探 total，只抓尾端頁（最新的 limit 筆）
        - 有 fields 過濾：全抓（上限 max_pages 頁）後過濾再取最新 limit 筆
        輸出精簡為 (時間, 操作者, 欄位 from→to)，新→舊排序。"""
        limit = max(1, min(int(limit or 50), 500))
        want = set(x.strip().lower() for x in (fields or []) if x and x.strip())

        async def fetch(start_at: int) -> dict:
            return await self.get(f"issue/{issue_key}/changelog",
                                  params={"startAt": start_at, "maxResults": 100})

        entries = []
        if want:
            start = 0
            for _ in range(max_pages):
                page = await fetch(start)
                values = page.get("values", [])
                entries.extend(values)
                if page.get("isLast", True) or not values:
                    break
                start += len(values)
            total_all = len(entries)
        else:
            probe = await fetch(0)
            total_all = probe.get("total", 0)
            start = max(0, total_all - limit)
            values = probe.get("values", []) if start == 0 else []
            while True:
                if not values:
                    page = await fetch(start)
                    values = page.get("values", [])
                    if not values:
                        break
                entries.extend(values)
                start += len(values)
                if start >= total_all:
                    break
                values = []

        out = []
        for h in entries:
            changes = [
                {"field": i.get("field"),
                 "from": i.get("fromString"), "to": i.get("toString")}
                for i in h.get("items", [])
                if not want or (i.get("field") or "").lower() in want
            ]
            if changes:
                out.append({
                    "at": (h.get("created") or "")[:16].replace("T", " "),
                    "by": (h.get("author") or {}).get("displayName", ""),
                    "changes": changes,
                })
        out.reverse()  # 新→舊
        result: dict[str, Any] = {"issue": issue_key, "total_changes": total_all,
                                  "returned": min(len(out), limit),
                                  "order": "新→舊", "changelog": out[:limit]}
        if len(out) > limit:
            result["note"] = f"符合條件共 {len(out)} 筆，回傳最新 {limit} 筆；要更早的請調大 limit"
        return result

    async def get_user_info(self, username: Optional[str] = None) -> Any:
        """取得用戶資訊。

        Jira Cloud v3 已移除 GET /user 的 username 參數（GDPR），
        指定使用者時改走 user/search?query=（可用 email 或顯示名稱模糊查詢）。"""
        def slim(u: dict) -> dict:
            return {
                "accountId": u.get("accountId"),
                "displayName": u.get("displayName"),
                "emailAddress": u.get("emailAddress", ""),
                "active": u.get("active"),
            }
        if username:
            users = await self.get("user/search",
                                   params={"query": username, "maxResults": 10})
            return [slim(u) for u in users]
        return slim(await self.get("myself"))

# 全域客戶端實例
jira_client = JiraClient()
