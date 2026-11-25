# -*- coding: utf-8 -*-
"""skill 提供：skill_list（列出）與 skill_get（取得全文）

skill 仍然只在 plugins/ehrms-tools/skills/<名稱>/SKILL.md 撰寫一份；線上知識庫 MCP 依 online_skills.json
的清單按需提供，同事不用安裝 plugin。常駐說明只放索引（什麼時候該取哪個 skill），全文用到才取。

skill 原本是寫給本機 plugin 的，裡面的工具名稱與線上版不同，所以取得全文時會在最前面加一段對照表。
需要讀本機程式碼或執行腳本的 skill 不適合放上來，留在 plugin 給工程師用。
"""
import json
import logging
import re
from pathlib import Path

from .dispatch import ToolResult
from .tools_core import registry

logger = logging.getLogger("kb_online.skill")

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
SKILLS_DIR = PLUGIN_ROOT / "skills"
MANIFEST = Path(__file__).resolve().parents[1] / "online_skills.json"

_FRONT_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9\-]*$")

TOOL_MAP = """\
> **線上知識庫使用須知**：這份流程原本是寫給本機 plugin 的，請依下表換成線上知識庫的工具。
>
> | 流程裡寫的 | 線上知識庫改用 |
> |---|---|
> | `recall`、`jira_lookup`、`faq_lookup`、`bulletin_lookup`、`sop_lookup`、`task_lookup`、`wiki_lookup`（給 query） | `kb_search`：一次查全部來源，不用分開呼叫；要限定來源用 `sources` |
> | `version_bulletins` | `kb_search` 加 `version_bulletins=true` |
> | `wiki_lookup`（給 page／section）、讀某一筆的全文 | `kb_get` |
> | `custom_lookup` | `kb_get(type="custom", id=統編或公司名稱)` |
> | `faq_log`、`sop_log`、`task_log` | 使用者明確要新增或更新 FAQ／SOP／Task／公告時用 `kb_contribute`；其餘已確認且可通用的結論用 `remember` 寫入並選對 `purpose` |
> | `jira_log` | 線上版不提供，Jira 結案紀錄由排程另外整理 |
> | （流程沒寫到）處理中另外查出來的新結論、使用者對你的修正 | `kb_note` 寫工作筆記，不必等結論確認；結論都來自知識庫就不用寫 |
> | `remember` 的 `supersedes`、`force` | 線上版沒有。發現既有知識有錯，用 `kb_flag` 提出疑慮並附依據 |
> | Jira 工具（`get_issue` 等）、DB MCP、`ehrms-custom-mcp` | 不在這個服務裡。你另外有接上才能用；沒有的話請使用者提供需要的內容 |
> | 讀程式碼（Read／Grep 追碼）、其他 skill（`custom-locate`、`debug` 等） | 沒有程式碼可讀時略過該步驟，並在回覆中說明哪些地方需要工程師確認 |
"""


def _load_manifest():
    """讀清單並略過無效的項目（名稱不合法、找不到 SKILL.md）。回傳 {名稱: {when, note, description, path}}。"""
    try:
        entries = json.loads(MANIFEST.read_text(encoding="utf-8")).get("skills", [])
    except (OSError, ValueError) as e:
        logger.error("讀不到 skill 清單 %s：%s", MANIFEST, e)
        return {}
    out = {}
    for e in entries:
        name = str(e.get("name") or "")
        path = SKILLS_DIR / name / "SKILL.md"
        if not _NAME_RE.match(name) or not path.is_file():
            logger.warning("skill 清單裡的「%s」找不到對應的 SKILL.md，略過", name)
            continue
        out[name] = {"when": str(e.get("when") or "").strip(), "note": str(e.get("note") or "").strip(),
                     "path": path}
    return out


def _split(text):
    """回傳 (frontmatter 裡的 description, 去掉 frontmatter 的內文)。"""
    m = _FRONT_RE.match(text)
    if not m:
        return "", text
    desc = next((ln.split(":", 1)[1].strip() for ln in m.group(1).splitlines() if ln.startswith("description:")), "")
    return desc, text[m.end():].lstrip()


def index_text():
    """放進常駐說明的 skill 索引；沒有任何 skill 時回傳空字串。"""
    skills = _load_manifest()
    if not skills:
        return ""
    lines = ["", "流程（skill）：遇到下列情況，先用 skill_get 取得流程全文並照著做，不要憑印象處理。"]
    lines += [f"- {s['when'] or name} → skill_get(name=\"{name}\")" for name, s in skills.items()]
    return "\n".join(lines) + "\n"


@registry.tool(
    "skill_list",
    "列出線上知識庫提供的工作流程（skill）與各自的使用時機。要找有沒有現成流程可以照著做時呼叫。",
    {"type": "object", "properties": {}})
async def skill_list(call, args):
    skills = _load_manifest()
    if not skills:
        return ToolResult("目前沒有提供任何流程。", no_result=True)
    lines = ["# 線上知識庫提供的流程"]
    for name, s in skills.items():
        desc, _ = _split(s["path"].read_text(encoding="utf-8"))
        lines.append(f"- **{name}**：{s['when'] or desc}")
    lines.append("\n→ 用 skill_get(name) 取得流程全文。")
    return ToolResult("\n".join(lines), refs=[{"type": "skill", "id": n, "score": None} for n in skills],
                      no_result=False)


@registry.tool(
    "skill_get",
    "取得某個工作流程（skill）的全文。常駐說明或 skill_list 指出某個情況要用哪個流程時，先呼叫這個工具再開始處理。",
    {"type": "object",
     "properties": {"name": {"type": "string", "description": "流程名稱，例如 crisis-triage"}},
     "required": ["name"]})
async def skill_get(call, args):
    name = str(args.get("name") or "").strip().lower()
    skills = _load_manifest()
    if name not in skills:
        available = "、".join(skills) or "（目前沒有）"
        return ToolResult(f"線上知識庫沒有提供「{name}」這個流程。可用的流程：{available}", no_result=True)
    s = skills[name]
    _, body = _split(s["path"].read_text(encoding="utf-8"))
    parts = [f"# 流程：{name}", TOOL_MAP]
    if s["note"]:
        parts.append(f"> **這個流程在線上使用時的補充**：{s['note']}\n")
    parts += ["---", body]
    return ToolResult("\n".join(parts), refs=[{"type": "skill", "id": name, "score": None}], no_result=False)
