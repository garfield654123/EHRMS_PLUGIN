# -*- coding: utf-8 -*-
"""使用者身分：現階段以 Jira 名單確認，並用電腦位址記住

流程（討論清單第 2 項）：
1. 連線進來 → 用電腦位址找有沒有確認過的使用者
2. 沒有 → 工具不提供服務，要求先呼叫 confirm_identity 確認信箱
3. confirm_identity：信箱要對得到 Jira 名單上唯一一位啟用中的使用者，對到就記住這個位址
4. 之後同一個位址不用再確認；位址變了就再確認一次

限制：信箱是使用者自己說的，伺服器只能確認「名單上有這個人」，不能確認「就是本人」。
接上公司帳號登入後，IDENTITY_SOURCE 會改成 sso，這個模組的確認流程就不再需要。
"""
import re
from dataclasses import dataclass

import anyio

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


@dataclass(frozen=True)
class Identity:
    name: str            # Jira 帳號（信箱 @ 前段），寫進 USER_NAME
    email: str
    source: str = "jira_list"


class JiraDirectory:
    """用 Jira 的使用者搜尋比對信箱。"""

    def __init__(self, base_url, email, token):
        self.base_url, self.email, self.token = base_url, email, token

    def configured(self):
        return bool(self.base_url and self.email and self.token)

    async def search(self, email):
        """回傳 [{accountId, displayName, emailAddress, active}]。"""
        import httpx
        async with httpx.AsyncClient(timeout=15, auth=(self.email, self.token)) as client:
            resp = await client.get(f"{self.base_url}/rest/api/3/user/search",
                                    params={"query": email, "maxResults": 10})
            resp.raise_for_status()
            return [{"accountId": u.get("accountId"), "displayName": u.get("displayName") or "",
                     "emailAddress": (u.get("emailAddress") or ""), "active": bool(u.get("active")),
                     "accountType": u.get("accountType") or ""} for u in resp.json()]


class IdentityError(Exception):
    """確認失敗；訊息可以直接回給使用者。"""


class IdentityService:
    def __init__(self, store, directory, email_domains=()):
        self.store, self.directory = store, directory
        self.email_domains = tuple(email_domains)
        self._cache = {}   # client_addr → Identity（只快取確認過的）

    async def resolve(self, client_addr):
        """這個位址確認過的使用者；沒有就回傳 None。"""
        if client_addr in self._cache:
            return self._cache[client_addr]
        binding = await anyio.to_thread.run_sync(self.store.find_binding, client_addr)
        if not binding:
            return None
        ident = Identity(name=binding["name"], email=binding["email"])
        self._cache[client_addr] = ident
        return ident

    async def confirm(self, client_addr, email):
        """比對 Jira 名單，成功就記住這個位址。失敗丟 IdentityError。"""
        email = (email or "").strip().lower()
        if not _EMAIL_RE.match(email):
            raise IdentityError("信箱格式不正確，請提供完整的公司信箱。")
        if self.email_domains and email.split("@", 1)[1] not in self.email_domains:
            raise IdentityError(f"只接受這些網域的信箱：{'、'.join(self.email_domains)}。")
        if not self.directory.configured():
            raise IdentityError("伺服器尚未設定 Jira 連線，無法確認身分，請聯絡管理者。")
        try:
            users = await self.directory.search(email)
        except Exception as e:
            raise IdentityError(f"查詢 Jira 名單失敗，請稍後再試。（{type(e).__name__}）")

        user = self._match(users, email)
        ident = Identity(name=email.split("@", 1)[0], email=email)
        await anyio.to_thread.run_sync(self.store.add_binding, client_addr, email, ident.name, user["accountId"])
        self._cache[client_addr] = ident
        return ident

    @staticmethod
    def _match(users, email):
        """名單上唯一一位啟用中的真人帳號才算對到。

        Jira 可能因隱私設定不回傳信箱（emailAddress 空白）：這時只有在搜尋結果剛好一位時才接受，
        因為搜尋本身就是用信箱查的。有回傳信箱時必須完全相同。
        """
        people = [u for u in users if u["active"] and u["accountType"] in ("", "atlassian")]
        exact = [u for u in people if u["emailAddress"].lower() == email]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise IdentityError("Jira 名單上有多個帳號使用這個信箱，請聯絡管理者。")
        hidden = [u for u in people if not u["emailAddress"]]
        if len(people) == 1 and len(hidden) == 1:
            return hidden[0]
        raise IdentityError("Jira 名單上找不到這個信箱對應的啟用中帳號，請確認信箱是否正確。")
