"""
Jira AI Bridge - Python SDK
============================
专为 AI Agent 与大模型调度优化的 Jira 客户端。
关键特性：
1. 投影裁剪（Projection Optimization）：强制默认裁剪无用 schema 字段，将百条检索耗时从数十秒压低至 0.5s。
2. 门卫安全头（Gate Key）：携带外网 Nginx 门卫鉴权，隔离恶意嗅探。
3. 结构化精简输出：将臃肿的 Jira JSON 转换为最适合 LLM 上下文（Context Window）的轻量字典列表。
"""

import os
import requests
from typing import List, Dict, Any, Optional

class JiraAIBridge:
    def __init__(
        self,
        gateway_url: Optional[str] = None,
        gate_key: Optional[str] = None,
        jira_pat: Optional[str] = None,
        timeout: int = 30
    ):
        """
        初始化 Jira AI 网关客户端。
        优先从环境变量读取配置：
        - JIRA_GATEWAY_URL
        - JIRA_GATE_KEY
        - JIRA_PAT
        """
        self.base_url = (gateway_url or os.getenv("JIRA_GATEWAY_URL", "")).rstrip("/")
        self.gate_key = gate_key or os.getenv("JIRA_GATE_KEY", "")
        self.jira_pat = jira_pat or os.getenv("JIRA_PAT", "")
        self.timeout = timeout

        if not self.base_url:
            raise ValueError("必须提供 gateway_url 或设置 JIRA_GATEWAY_URL 环境变量")
        if not self.gate_key:
            raise ValueError("必须提供 gate_key 或设置 JIRA_GATE_KEY 环境变量")
        if not self.jira_pat:
            raise ValueError("必须提供 jira_pat 或设置 JIRA_PAT 环境变量")

        self.headers = {
            "X-Gate-Key": self.gate_key,
            "Authorization": f"Bearer {self.jira_pat}",
            "Content-Type": "application/json",
            "User-Agent": "JiraAIBridge/1.0"
        }

    def search_issues(
        self,
        jql: str,
        fields: Optional[List[str]] = None,
        max_results: int = 50
    ) -> List[Dict[str, Any]]:
        """
        使用 JQL 检索 Jira 问题单并进行轻量化投影。
        
        :param jql: 标准 Jira 查询语句，如 'project = "PROJ" AND status = "Open"'
        :param fields: 需要拉取的字段列表。若为 None，默认拉取核心字段。
        :param max_results: 最大返回条数（默认 50）
        :return: 精简后的字典列表，方便直接喂给 LLM 消费
        """
        if fields is None:
            # 隧道通信黄金法则：仅拉取关键字段
            fields = ["key", "summary", "status", "assignee", "priority", "updated", "created"]

        url = f"{self.base_url}/rest/api/2/search"
        params = {
            "jql": jql,
            "fields": ",".join(fields),
            "maxResults": max_results
        }

        resp = requests.get(url, headers=self.headers, params=params, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()

        results = []
        for issue in data.get("issues", []):
            f = issue.get("fields", {})
            assignee_obj = f.get("assignee") or {}
            status_obj = f.get("status") or {}
            priority_obj = f.get("priority") or {}

            results.append({
                "key": issue.get("key"),
                "summary": f.get("summary", ""),
                "status": status_obj.get("name", "Unknown"),
                "assignee": assignee_obj.get("displayName", "未分配"),
                "priority": priority_obj.get("name", "Normal"),
                "updated": f.get("updated", ""),
                "created": f.get("created", "")
            })
        return results

    def get_issue(self, issue_key: str) -> Dict[str, Any]:
        """
        获取单个问题的核心详情。
        """
        url = f"{self.base_url}/rest/api/2/issue/{issue_key}"
        params = {"fields": "key,summary,status,assignee,priority,description,comment"}
        resp = requests.get(url, headers=self.headers, params=params, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        f = data.get("fields", {})
        return {
            "key": data.get("key"),
            "summary": f.get("summary"),
            "description": f.get("description"),
            "status": (f.get("status") or {}).get("name"),
            "assignee": ((f.get("assignee") or {}).get("displayName") or "未分配"),
            "priority": ((f.get("priority") or {}).get("name") or "Normal"),
            "recent_comments": [
                {
                    "author": (c.get("author") or {}).get("displayName"),
                    "created": c.get("created"),
                    "body": c.get("body")
                }
                for c in (f.get("comment", {}).get("comments", [])[-5:])  # 仅保留最近 5 条评论
            ]
        }

    def add_comment(self, issue_key: str, comment_text: str) -> bool:
        """
        为指定问题单添加评论。
        """
        url = f"{self.base_url}/rest/api/2/issue/{issue_key}/comment"
        payload = {"body": comment_text}
        resp = requests.post(url, headers=self.headers, json=payload, timeout=self.timeout)
        return resp.status_code in [200, 201]


# ----------------------------------------------------
# 模块测试 / 使用示例
# ----------------------------------------------------
if __name__ == "__main__":
    import json
    # 测试前请先导出环境变量或填入参数
    # export JIRA_GATEWAY_URL="http://YOUR_CLOUD_IP:8083"
    # export JIRA_GATE_KEY="YOUR_AI_AGENT_GATE_KEY"
    # export JIRA_PAT="YOUR_PERSONAL_ACCESS_TOKEN"

    try:
        bridge = JiraAIBridge()
        print("[*] 正在执行 JQL 搜索测试...")
        issues = bridge.search_issues('updated >= -1d ORDER BY updated DESC', max_results=5)
        print(f"[+] 检索成功，共召回 {len(issues)} 条记录:")
        print(json.dumps(issues, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"[-] 提示: 请先配置实际连接参数后再运行测试: {e}")
