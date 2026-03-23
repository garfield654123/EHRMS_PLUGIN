"""Jira API client for making authenticated requests."""

import base64
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

    async def search_issues(
        self,
        jql: str,
        fields: Optional[list[str]] = None,
        max_results: int = 50,
        start_at: int = 0,
        expand: Optional[list[str]] = None
    ) -> dict[str, Any]:
        """
        使用 JQL 搜尋 Issues (符合 Jira REST API v3 標準)

        Args:
            jql: JQL 查詢字串
            fields: 要返回的欄位列表
            max_results: 最大結果數量
            start_at: 起始位置
            expand: 要展開的資源 (例如: ['changelog', 'renderedFields'])
        """
        params = {
            "jql": jql,
            "maxResults": max_results,
            "startAt": start_at,
        }

        if fields:
            params["fields"] = ",".join(fields)
        else:
            params["fields"] = "*all"

        if expand:
            params["expand"] = ",".join(expand)

        return await self.get("search/jql", params=params)

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
            def adf_to_text(adf):
                if not isinstance(adf, dict) or "content" not in adf:
                    return str(adf)
                lines = []
                for block in adf["content"]:
                    t = block.get("type")
                    if t == "paragraph":
                        lines.append("".join([c.get("text", "") for c in block.get("content", [])]))
                    elif t == "heading":
                        level = block.get("attrs", {}).get("level", 1)
                        prefix = "#" * level
                        lines.append(f"{prefix} " + "".join([c.get("text", "") for c in block.get("content", [])]))
                    elif t == "bulletList":
                        for li in block.get("content", []):
                            for p in li.get("content", []):
                                lines.append("- " + "".join([c.get("text", "") for c in p.get("content", [])]))
                    elif t == "orderedList":
                        idx = 1
                        for li in block.get("content", []):
                            for p in li.get("content", []):
                                lines.append(f"{idx}. " + "".join([c.get("text", "") for c in p.get("content", [])]))
                                idx += 1
                    elif t == "codeBlock":
                        code = "\n".join([c.get("text", "") for c in block.get("content", [])])
                        lines.append(f"```\n{code}\n```")
                return "\n".join(lines)

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
        adf_body = {
            "version": 1,
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": body
                        }
                    ]
                }
            ]
        }
        return await self.post(f"issue/{issue_key}/comment", json_data={"body": adf_body})

    async def get_issue_transitions(self, issue_key: str) -> dict[str, Any]:
        """取得 Issue 的可用轉換狀態 (符合 Jira REST API v3 標準)"""
        return await self.get(f"issue/{issue_key}/transitions")

    async def get_issue_changelog(self, issue_key: str) -> dict[str, Any]:
        """取得 Issue 的變更歷史 (符合 Jira REST API v3 標準)"""
        return await self.get(f"issue/{issue_key}/changelog")

    async def get_user_info(self, username: Optional[str] = None) -> dict[str, Any]:
        """取得用戶資訊 (符合 Jira REST API v3 標準)"""
        if username:
            return await self.get("user", params={"username": username})
        else:
            return await self.get("myself")

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
