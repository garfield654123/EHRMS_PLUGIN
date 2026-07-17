"""Jira API client for making authenticated requests."""

import base64
import json as _json
import os
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

        async with httpx.AsyncClient() as client:
            response = await client.request(
                method=method,
                url=url,
                auth=self.auth,
                headers=self.headers,
                params=params,
                json=json_data,
                timeout=30.0
            )

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

            return response.json()

    async def get(self, endpoint: str, params: Optional[dict] = None) -> dict[str, Any]:
        """執行 GET 請求"""
        return await self._request("GET", endpoint, params=params)

    async def post(self, endpoint: str, json_data: dict) -> dict[str, Any]:
        """執行 POST 請求"""
        return await self._request("POST", endpoint, json_data=json_data)

    # search_issues 預設抓取的精簡欄位。
    # 不可用 *all：EHRMSONE 掛了數百個共用 customfield（多為流程範本文字），
    # 單張 Issue 的完整 JSON 可達 30 萬字元，會超過 MCP 輸出上限。
    DEFAULT_SEARCH_FIELDS = [
        "summary", "status", "assignee", "reporter", "priority",
        "issuetype", "created", "updated", "duedate", "labels",
        "components", "description", "attachment", "parent",
    ]
    MAX_SEARCH_RESULTS = 50   # 單次搜尋上限（防上下文爆量）
    MAX_DESC_CHARS = 600      # 清單場景的描述截斷長度；全文請查單筆
    MAX_EXTRA_CHARS = 800     # 額外指定欄位的截斷長度

    async def search_issues(
        self,
        jql: str,
        fields: Optional[list[str]] = None,
        max_results: int = 50,
        next_page_token: Optional[str] = None,
        expand: Optional[list[str]] = None
    ) -> dict[str, Any]:
        """
        使用 JQL 搜尋 Issues（v3 search/jql 端點）

        分頁：search/jql 使用 nextPageToken（不支援 startAt）——
        回傳含 next_page_token 時，帶回原參數即可取得下一頁。

        輸出永遠是精簡結構（防上下文爆量）：fields 只能「加欄位」，
        追加欄位一律 ADF→純文字並截斷，絕不回傳 Jira 原始 JSON。

        Args:
            jql: JQL 查詢字串
            fields: 額外欄位（如 customfield_12722），會附加在精簡結構上
            max_results: 單頁筆數（上限 MAX_SEARCH_RESULTS）
            next_page_token: 上一頁回傳的翻頁 token
            expand: 要展開的資源
        """
        max_results = min(int(max_results or 50), self.MAX_SEARCH_RESULTS)
        extra = [x for x in (fields or []) if x and not x.startswith("*")]
        params = {
            "jql": jql,
            "maxResults": max_results,
            "fields": ",".join(self.DEFAULT_SEARCH_FIELDS + extra),
        }
        if next_page_token:
            params["nextPageToken"] = next_page_token
        if expand:
            params["expand"] = ",".join(expand)

        result = await self.get("search/jql", params=params)

        issues = [self._simplify_issue(i, extra) for i in result.get("issues", [])]
        out: dict[str, Any] = {"count": len(issues), "issues": issues}
        if not result.get("isLast", True) and result.get("nextPageToken"):
            out["next_page_token"] = result["nextPageToken"]
            out["note"] = "尚有更多結果，帶 next_page_token 再查一次可取得下一頁"
        return out

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
                        extra_fields: Optional[list[str]] = None) -> dict[str, Any]:
        """將 Issue 壓縮為精簡結構，ADF 描述轉純文字並截斷"""
        f = issue.get("fields", {})
        desc = self._adf_to_text(f.get("description")) if f.get("description") else ""
        if len(desc) > self.MAX_DESC_CHARS:
            desc = (desc[:self.MAX_DESC_CHARS]
                    + f"…（已截斷，全文 {len(desc)} 字，需要全文請用 get_issue 查單筆）")
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

    async def get_issue_comments(self, issue_key: str) -> dict[str, Any]:
        """取得 Issue 的所有評論"""
        return await self.get(f"issue/{issue_key}/comment")

    async def get_issue_basic(self, issue_key: str) -> dict[str, Any]:
        """
        取得 Issue 的基本欄位與評論（輕量查詢）

        只抓取基本欄位（透過 get_issue_summary）與評論，不抓 *all 全欄位，
        評論 body 會由 ADF 轉為純文字以節省資源。

        Args:
            issue_key: Issue 的 Key (例如: PROJ-123)

        Returns:
            dict: 基本欄位 + comment_total + comments[]
        """
        summary = await self.get_issue_summary(issue_key)
        comments_raw = await self.get_issue_comments(issue_key)

        comments = []
        for c in comments_raw.get("comments", []):
            comments.append({
                "author": (c.get("author") or {}).get("displayName", ""),
                "created": (c.get("created") or "")[:10],
                "updated": (c.get("updated") or "")[:10],
                "body": self._adf_to_text(c.get("body")),
            })

        return {
            **summary,
            "comment_total": comments_raw.get("total", len(comments)),
            "comments": comments,
        }

    async def get_issue_worklogs(self, issue_key: str) -> dict[str, Any]:
        """取得 Issue 的所有工時記錄"""
        return await self.get(f"issue/{issue_key}/worklog")

    async def download_attachment_content(self, content_url: str) -> bytes:
        """
        下載附件內容

        Args:
            content_url: 附件的下載 URL

        Returns:
            附件的二進位內容
        """
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(
                content_url,
                auth=self.auth,
                timeout=60.0
            )
            response.raise_for_status()
            return response.content

    async def download_attachment_as_base64(self, content_url: str) -> dict[str, Any]:
        """
        下載附件並轉換為 base64 格式

        Args:
            content_url: 附件的下載 URL
        """
        try:
            content = await self.download_attachment_content(content_url)
            base64_content = base64.b64encode(content).decode('utf-8')
            file_extension = content_url.split('.')[-1].lower() if '.' in content_url else 'unknown'
            is_image = file_extension in ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'svg']
            return {
                "success": True,
                "base64_content": base64_content,
                "file_size": len(content),
                "file_extension": file_extension,
                "is_image": is_image,
                "mime_type": self._get_mime_type(file_extension)
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

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

    async def download_image_as_base64(
        self,
        content_url: str,
        filename: str
    ) -> dict[str, Any]:
        """
        下載圖片並轉換為 base64 格式

        Args:
            content_url: 附件的下載 URL
            filename: 檔案名稱
        """
        try:
            content = await self.download_attachment_content(content_url)
            base64_content = base64.b64encode(content).decode('utf-8')
            file_extension = filename.split('.')[-1].lower() if '.' in filename else ''
            is_image = file_extension in ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'svg']
            return {
                "success": True,
                "filename": filename,
                "file_size": len(content),
                "is_image": is_image,
                "file_extension": file_extension,
                "mime_type": self._get_mime_type(file_extension),
                "base64_content": base64_content
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _get_mime_type(self, file_extension: str) -> str:
        """根據檔案副檔名取得 MIME 類型"""
        mime_types = {
            'png': 'image/png',
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'gif': 'image/gif',
            'bmp': 'image/bmp',
            'webp': 'image/webp',
            'svg': 'image/svg+xml',
            'pdf': 'application/pdf',
            'txt': 'text/plain',
            'doc': 'application/msword',
            'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'xls': 'application/vnd.ms-excel',
            'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        }
        return mime_types.get(file_extension.lower(), 'application/octet-stream')

    async def get_custom_fields(self) -> list[dict[str, Any]]:
        """取得所有自訂欄位的定義"""
        return await self.get("field")

    async def add_comment(self, issue_key: str, body: str) -> dict[str, Any]:
        """
        對 Issue 新增評論

        Args:
            issue_key: Issue 的 Key (例如: PROJ-123)
            body: 評論內容（純文字，將轉為 ADF 格式）
        """
        # 逐行拆成段落：單一 text node 內的 \n 不會被 Jira 渲染成換行
        content = []
        for line in body.split("\n"):
            para: dict[str, Any] = {"type": "paragraph", "content": []}
            if line:
                para["content"] = [{"type": "text", "text": line}]
            content.append(para)
        adf_body = {"version": 1, "type": "doc", "content": content}
        return await self.post(f"issue/{issue_key}/comment", json_data={"body": adf_body})

    async def get_issue_transitions(self, issue_key: str) -> dict[str, Any]:
        """取得 Issue 的可用轉換狀態 (符合 Jira REST API v3 標準)"""
        return await self.get(f"issue/{issue_key}/transitions")

    async def get_issue_changelog(self, issue_key: str, max_pages: int = 10) -> dict[str, Any]:
        """取得 Issue 完整變更歷史（自動翻頁＋精簡輸出）。

        changelog 端點每頁上限 100 筆，依 isLast 自動翻頁；
        輸出精簡為 (時間, 操作者, 欄位 from→to)，避免原始 JSON 撐爆上下文。"""
        start, entries = 0, []
        for _ in range(max_pages):
            page = await self.get(f"issue/{issue_key}/changelog",
                                  params={"startAt": start, "maxResults": 100})
            values = page.get("values", [])
            for h in values:
                entries.append({
                    "at": (h.get("created") or "")[:16].replace("T", " "),
                    "by": (h.get("author") or {}).get("displayName", ""),
                    "changes": [
                        {"field": i.get("field"),
                         "from": i.get("fromString"), "to": i.get("toString")}
                        for i in h.get("items", [])
                    ],
                })
            if page.get("isLast", True) or not values:
                break
            start += len(values)
        return {"issue": issue_key, "total": len(entries), "changelog": entries}

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

    async def get_user_issues(
        self,
        user_email: Optional[str] = None,
        status: Optional[str] = None,
        max_results: int = 50
    ) -> dict[str, Any]:
        """
        取得特定使用者的 Issues

        Args:
            user_email: 使用者 Email (預設使用 DEFAULT_USER)
            status: Issue 狀態過濾
            max_results: 最大結果數量
        """
        email = user_email or config.default_user
        jql = f'assignee = "{email}"'

        if status:
            jql += f' AND status = "{status}"'

        return await self.search_issues(jql, max_results=max_results)


# 全域客戶端實例
jira_client = JiraClient()
