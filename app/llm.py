"""LLM 管线：提取（对话→facts）+ 裁决（ADD/UPDATE/NOOP + scope 判定）。

LLM 后端：OpenAI 兼容（aliyun-elp 路由，sensenova/auto）。
"""
import json
import re

import httpx

from .config import settings

EXTRACT_SYSTEM = """你是一个长期记忆提取器。从对话内容中提取值得长期记住的事实、偏好、流程、观察。
输出严格的 JSON 数组，每项：{"content": "一句话记忆（中文）", "kind": "fact|preference|procedure|observation", "confidence": 0.0-1.0, "tags": ["标签"], "target_scope": "user|project|device|agent", "reason": "简短理由"}
scope 判定规则：
- user（跨设备共享）：用户通用偏好、身份事实、跨设备都适用的规范。要求：不包含任何本机路径、IP、密钥、生产细节。
- project：明确属于某个项目的通用知识，且多设备共建。
- agent：某类子代理任务的通用经验。
- device（本机私有，默认）：包含本机路径、服务器细节、生产环境信息、或拿不准的内容。
密钥/密码/API Key 一律不提取（跳过）。
只输出 JSON 数组，不要其他文字。没有值得记的输出 []。"""

ADJUDICATE_SYSTEM = """你是记忆裁决器。给定一条新记忆候选和已有的相关记忆，决定写入方式。
输出严格 JSON：{"action": "ADD|UPDATE|NOOP", "update_id": null或已有记忆id, "merged_content": "合并后的记忆内容（UPDATE时）", "target_scope": "user|project|device|agent", "reason": "简短理由"}
判定规则（按优先级，从严掌握）：
- NOOP：候选与某条已有记忆表达完全同一事实且无任何新信息（完全重复，写它毫无意义）。
- UPDATE：候选与某条已有记忆语义重复或高度相似——**同一事实/偏好/流程的不同措辞、详略版本、部分子集都算**。给出 update_id（必须从 related 里选），merged_content 写合并后的完整表述（取更完整/更新的表述）。
  典型场景（都必须 UPDATE 不能 ADD）：
  · 「用户要求中文」与「用户偏好：所有对话和记忆使用中文」→ 同一条偏好
  · 「E团购部署走 pull-release.sh」与「E团购项目的 h5 部署走 pull-release.sh --only h5」→ 同一条流程
  · 旧记忆 + 新增小细节 → UPDATE 并把细节合进去
- ADD：与所有已有记忆语义都不同（不同事实/不同项目/不同流程）才允许新增。
- **宁可 UPDATE 不可 ADD**：拿不准是新增还是更新时，一律选 UPDATE（用最相关的那条）。禁止对同一语义重复 ADD。
scope 修正规则：若候选含本机路径/IP/生产细节但标了 user，改判 device；拿不准一律 device。
只输出 JSON，不要其他文字。"""


def _chat(system: str, user: str, max_tokens: int = 2000, timeout: int = 90) -> str:
    if not settings.llm_api_key:
        raise RuntimeError("LLM_API_KEY 未配置（/etc/memoryd/config.env）")
    resp = httpx.post(
        f"{settings.llm_base_url}/chat/completions",
        headers={"Authorization": f"Bearer {settings.llm_api_key}"},
        json={
            "model": settings.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.1,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    msg = data["choices"][0].get("message", {})
    # 混合推理模型（sensenova/auto）偶发只返回 reasoning_content 而无 content，容错取值
    return msg.get("content") or msg.get("reasoning_content") or ""


def _parse_json_array(text: str) -> list:
    """容错解析：剥掉 markdown 代码围栏等，取第一个 JSON 数组/对象。"""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    m = re.search(r"\[.*\]|\{.*\}", text, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, list) else [data]
    except json.JSONDecodeError:
        return []


def extract_facts(dialogue_text: str) -> list[dict]:
    """对话文本 → 记忆候选列表。"""
    raw = _chat(EXTRACT_SYSTEM, dialogue_text[:12000])
    facts = []
    for f in _parse_json_array(raw):
        if not isinstance(f, dict) or not f.get("content"):
            continue
        facts.append({
            "content": str(f["content"])[:2000],
            "kind": str(f.get("kind", "fact")),
            "confidence": min(max(float(f.get("confidence", 0.5)), 0.0), 1.0),
            "tags": [str(t) for t in (f.get("tags") or [])][:10],
            "target_scope": f.get("target_scope", "device")
            if f.get("target_scope") in ("user", "project", "device", "agent") else "device",
            "reason": str(f.get("reason", ""))[:200],
        })
    return facts


def adjudicate(fact: dict, related: list[dict]) -> dict:
    """单条候选 + 相关已有记忆 → 裁决结果。"""
    related_text = json.dumps(
        [{"id": r["id"], "content": r["content"], "scope": r["scope"], "kind": r["kind"]} for r in related],
        ensure_ascii=False,
    ) if related else "（无相关已有记忆）"
    user = f"新记忆候选：\n{json.dumps(fact, ensure_ascii=False)}\n\n已有相关记忆：\n{related_text}"
    raw = _chat(ADJUDICATE_SYSTEM, user, max_tokens=800)
    parsed = _parse_json_array(raw)
    if not parsed or not isinstance(parsed[0], dict):
        return {"action": "ADD", "update_id": None, "merged_content": fact["content"],
                "target_scope": fact.get("target_scope", "device"), "reason": "解析失败按保守ADD"}
    d = parsed[0]
    action = d.get("action", "ADD") if d.get("action") in ("ADD", "UPDATE", "NOOP") else "ADD"
    return {
        "action": action,
        "update_id": d.get("update_id") if action == "UPDATE" else None,
        "merged_content": str(d.get("merged_content") or fact["content"])[:2000],
        "target_scope": d.get("target_scope") if d.get("target_scope") in
                        ("user", "project", "device", "agent") else fact.get("target_scope", "device"),
        "reason": str(d.get("reason", ""))[:200],
    }