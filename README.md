# Jira-AI-Bridge 🚀

> **企业内网 Jira 外网安全穿透网关与 AI Agent 智能化调度实践指南**  
> 支持零 VPN 外网流畅访问 Web、多机高可用热备负载均衡、双重门卫安全隔离、AI JQL 轻量投影调度（毫秒级拉取）。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![FRP Version](https://img.shields.io/badge/frp-v0.70%2B-blue.svg)](https://github.com/fatedier/frp)
[![Nginx](https://img.shields.io/badge/nginx-1.20%2B-green.svg)](https://nginx.org/)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)

---

## 📖 解决的核心痛点

在大型企业或主机厂研发管理中，Jira 往往部署在公司纯内网中，并受企业级硬件 WAF、跨站伪造（XSRF）防护及内外网隔离保护。这带来了两大典型阻碍：
1. **外网协作痛点**：员工在家或出差时，必须挂载不稳定且容易掉线的专用 VPN，移动端或平板更是难以随时查阅。
2. **AI 自动化调度痛点**：大模型（如 Hermes、Claude Code、OpenCode、自研智能体）需要自动化检索缺陷并生成质量报表，但普通内网穿透会遭受 **WAF 拦截、Basic Auth 冲突、全字段传输超大导致网络阻塞、401 权限紊乱** 等一系列严重问题。

本项目是一套经过高强度生产环境验证的完整工程实践，提供**云端防爆破网关 + 内网 6 补丁 Nginx 反代 + frp 隧道负载均衡组 + AI 投影 SDK**。

---

## 📐 系统架构与数据流

```text
[ 外网浏览器 / 移动端 ]                   [ AI Agent (Hermes / Claude / LLM) ]
         │ (HTTP + Basic Auth 防爆破)               │ (Header: X-Gate-Key + Bearer PAT)
         ▼                                         ▼
┌────────────────────────────────────────────────────────────────────────┐
│ 公网云服务器 (VPS)                                                      │
│  ├─ Nginx 安全网关                                                     │
│  │   ├─ 8081 端口: 网页入口（基础鉴权防爆破，限制公网直接探测）          │
│  │   └─ 8083 端口: AI 专用入口（X-Gate-Key 密钥门卫，只放行 /rest/）    │
│  └─ frps 服务端 (监听 7000，维持安全 TLS 隧道)                         │
└────────────────────────────────────────────────────────────────────────┘
                                    │ TLS 隧道传输 (反向代理)
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ 公司内网宿主节点 (多台办公机 / 晚班电脑 / 虚拟机)                        │
│  ├─ frpc 客户端 (配置 loadBalancer group，任意一台在线通道即可用)        │
│  └─ 本地 Nginx (8080 端口，承载 6 大企业级协议补丁)                     │
└────────────────────────────────────────────────────────────────────────┘
                                    │ 局域网 HTTPS / 直连
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ 企业真实 Jira 集群 (带企业 WAF、严格 XSRF 防御、SSO/域认证)             │
└────────────────────────────────────────────────────────────────────────┘
```

---

## ⚡ 为什么普通穿透必死？（6 大核心协议补丁）

很多团队尝试直接用 frp TCP 穿透 Jira 的 443 端口，结果无一例外被阻断。本项目内网 Nginx 实现了经过实战验证的 6 大补丁：

| 补丁编号 | 补丁名称 | 解决的真实阻碍与致命错误 |
| :---: | :--- | :--- |
| **Patch 1** | **SNI 扩展与 Host 改写** | 企业级 WAF 会比对 TLS 握手 SNI 与 HTTP Host。直连会因证书域名不一致直接报 `HTTP 418` 拦截。 |
| **Patch 2** | **清空 Basic 认证头** | 云端外网入口配置了 Basic Auth 防黑客扫描，但浏览器发给 Jira 时带上了该头，Jira 误以为是本地账号尝试解析，导致**全员 401 Unauthorized**。 |
| **Patch 3** | **Origin / Referer 改写** | Jira 具备严格的 XSRF 防御，外网发来的 HTTP 请求跨域时，**所有 POST 表单（包括登录和提单）直接报“系统出错了” (HTTP 403)**。补丁将其强制重写为内网合法域名。 |
| **Patch 4** | **Cookie Secure 标志剥除** | 外网为 HTTP 协议，内网是 HTTPS。若不剥除 Cookie 上的 `Secure` 标记，现代浏览器会拒绝存储 `JSESSIONID`，导致登录成功后立即退出。 |
| **Patch 5** | **Upstream Keepalive 连接复用** | 维持 32 个持久上游长连接，杜绝短连接高频 TLS 握手触发企业 WAF 的并发黑名单与封禁。 |
| **Patch 6** | **/rest/ 路由 Bearer 透传** | 外网 AI 脚本调度时必须携带 Jira Personal Access Token (PAT)，此补丁只放行 Bearer 令牌，与网页 Basic 认证完美共存。 |

---

## 📂 仓库目录规范

```text
jira-ai-bridge/
├── deploy/
│   ├── cloud/                      # 云服务器端配置
│   │   ├── frps.toml.example       # frp 服务端配置模板 (v0.70+)
│   │   └── nginx-gateway.conf.example # 云端双入口网关 (8081 Web + 8083 AI API)
│   └── client/                     # 内网电脑端配置
│       ├── frpc.toml.example       # frp 客户端负载均衡组模板
│       ├── nginx-client.conf.example # 核心 6 补丁 Nginx 反代配置
│       ├── start.bat               # Windows 一键启动脚本
│       └── stop.bat                # Windows 一键停止脚本
├── sdk/
│   └── python/                     # AI Agent / 自动化调度工具集
│       ├── jira_ai_bridge.py       # 带轻量投影优化的 Jira SDK
│       └── requirements.txt        # 依赖清单
├── LICENSE                         # MIT 开源许可证
└── README.md                       # 本说明文档
```

---

## 🚀 快速开始与部署实战

### 第一步：云端部署（VPS）

1. **安装 frp 服务端 (v0.70+) 与 Nginx**：
   ```bash
   # 下载并解压 frp
   wget https://github.com/fatedier/frp/releases/download/v0.70.1/frp_0.70.1_linux_amd64.tar.gz
   tar -zxvf frp_0.70.1_linux_amd64.tar.gz
   sudo cp frp_0.70.1_linux_amd64/frps /usr/local/bin/
   ```

2. **配置 frps (`/etc/frp/frps.toml`)**：
   参考 `deploy/cloud/frps.toml.example`，配置 `bindPort = 7000` 并设定强随机 Token。
   ```bash
   sudo systemctl enable --now frps
   ```

3. **生成 Web 入口密码并配置 Nginx**：
   ```bash
   sudo apt install apache2-utils -y
   sudo htpasswd -bc /etc/nginx/.jira_htpasswd admin YOUR_PASSWORD_HERE
   ```
   将 `deploy/cloud/nginx-gateway.conf.example` 复制至 `/etc/nginx/sites-available/jira-gateway`，替换配置中的密钥后重载 Nginx：
   ```bash
   sudo nginx -t && sudo systemctl reload nginx
   ```

4. **安全组放行**：
   在云服务商控制台安全组放行 `7000` (FRP), `8081` (Web), `8083` (AI API) 端口。

---

### 第二步：内网节点部署（办公电脑 / 虚拟机）

建议部署在常开的工控机、晚班同事电脑或本地 Linux 虚拟机（目录路径必须**纯英文**，如 `C:\jira-tunnel`）。

1. **配置内网 Nginx (`nginx.conf`)**：
   将 `deploy/client/nginx-client.conf.example` 覆盖本地 Nginx 的 `conf/nginx.conf`。
   > **注意**：将其中的 `jira.internal.company.com` 全文替换为您公司 Jira 真实的内网域名。

2. **配置内网 frpc (`frpc.toml`)**：
   参考 `deploy/client/frpc.toml.example`：
   ```toml
   serverAddr = "YOUR_CLOUD_VPS_IP"
   serverPort = 7000
   auth.method = "token"
   auth.token = "YOUR_SUPER_STRONG_FRP_TOKEN"

   [[proxies]]
   name = "jira-shift-PC01"    # 建议加上本机机器名区分
   type = "tcp"
   localIP = "127.0.0.1"
   localPort = 8080           # 对应内网 Nginx 监听端口
   remotePort = 8082          # 映射到云端 Nginx 反代的端口

   # 开启负载均衡组
   [proxies.loadBalancer]
   group = "jira-cluster"
   groupKey = "YOUR_GROUP_KEY"
   ```

3. **启动服务**：
   - **Windows**：双击运行 `deploy/client/start.bat`。
   - **检查**：当 `frpc.log` 输出 `login to server success` 与 `start proxy success` 即表明通道已打通！

---

## 🤖 AI Agent 调度与集成实战

### 1. 隧道调度的“黄金法则”：显式投影（Projection）

通过隧道调度 Jira 时，**绝不能全量拉取 Issue**！
- **全量痛点**：Jira 单个 Issue JSON 含有大量自定义字段元数据，大小常达 30KB~80KB。拉取 100 条即超 5MB，通过内网主机上行带宽转发，响应往往超过 30 秒甚至超时。
- **黄金法则**：AI 检索必须显式声明 `fields=key,summary,status,assignee,updated,priority`。
- **实测性能**：数据量压缩 98%，100 条数据响应由 30s 骤降至 **0.55s**！

### 2. 使用 Python SDK 进行 AI 调度

```python
from sdk.python.jira_ai_bridge import JiraAIBridge

# 初始化网关连接
bridge = JiraAIBridge(
    gateway_url="http://YOUR_CLOUD_IP:8083",
    gate_key="YOUR_AI_AGENT_GATE_KEY_HERE",
    jira_pat="YOUR_JIRA_PERSONAL_ACCESS_TOKEN"
)

# 1. 毫秒级 JQL 检索未解决的高优缺陷
issues = bridge.search_issues(
    jql='project = "PROJ" AND status in ("Open", "In Progress") AND priority in ("Blocker", "Critical")',
    max_results=10
)

for bug in issues:
    print(f"[{bug['key']}] ({bug['status']}) {bug['assignee']}: {bug['summary']}")

# 2. 为指定缺陷回写分析进展
bridge.add_comment("PROJ-1024", "【AI Agent 巡检】该单据停滞超过 24 小时未更新，请经办人尽快同步排查结论。")
```

### 3. Agent System Prompt 提示词模板

可以将以下指令嵌入您的智能体系统提示词（System Prompt）中：

```markdown
你现在是研发项目缺陷治理专家，能够通过 `JiraAIBridge` 工具检索项目质量状态。
【约束准则】
1. 当用户查询项目缺陷、超时风险或版本进度时，优先使用 JQL 过滤出必要的数据集。
2. 调度查询时严格限制提取的字段集（仅提取 key, summary, status, assignee, updated）。
3. 重点识别处于「分析中/修复中」且更新时间超过 24 小时的停滞单。
4. 输出质量报告时，除给出数据概览外，必须附带一条格式化的一键催办话术模板。
```

---

## 🛠️ 生产级排障矩阵（Checklist）

| 异常现象 | 核心根因 | 快速解决排障方案 |
| :--- | :--- | :--- |
| **curl 外网挂起或超时** | 云安全组未放行端口 | 进入云服务商控制台，检查 7000、8081、8083 端口是否在**实例详情防火墙**中放行（来源设为 `0.0.0.0/0`）。 |
| **HTTP 502 Bad Gateway** | 隧道断开或客户端挂死 | 1. 检查内网机器 Nginx 是否存活；<br>2. 检查 `frpc.log`，确认是否提示 `start proxy success`。 |
| **HTTP 401 Unauthorized** | Basic 认证污染 Jira 会话 | 检查内网 Nginx 是否严格配置了 `Patch 2: proxy_set_header Authorization ""`。如未清空，Jira 会将外网网关账号当成本地用户导致鉴权失败。 |
| **HTTP 403 / 登录报“出错了”** | Jira 原生 XSRF 跨域拦截 | 检查内网 Nginx 是否配置了 `Patch 3: map $http_origin / Referer`，必须将外部 HTTP 强制重写为内网真实 HTTPS 域名。 |
| **HTTP 418 / 被安全中心拦截** | WAF 阻断非法客户端请求 | 检查内网 Nginx 的 `proxy_ssl_server_name on;` 是否开启，且 `proxy_ssl_name` 与内网真实域名完全一致。 |
| **域名访问被 302 拦截** | 云服务器未备案阻断 | 国内云厂商会在网络层拦截所有未备案域名的 HTTP/HTTPS 流量（包括非常规端口）。临时请直接使用 `http://<云端IP>:<端口>` 访问。 |

---

## 🛡️ 安全合规与免责声明

1. **企业安全规范**：在企业内部部署任何远程接入和代理软件前，请确保符合所在公司的网络安全政策、信息安全规范并取得必要授权。
2. **密钥保护**：
   - 本项目的所有示例配置文件均已去除敏感信息。在实际推送到公共代码库前，请确保通过 `.gitignore` 排除含有真实 IP、密码和 Token 的文件。
   - 建议为 AI Agent 分配只具备必要读权限与有限评论权限的专用机器人账户。
3. **免责声明**：本项目仅供技术交流与学习使用，任何个人或组织因不当使用或违反组织安全规范造成的后果，本项目作者概不承担任何责任。

---

## 📄 开源许可证

本项目基于 [MIT License](LICENSE) 开源发布。
欢迎提交 Issue 与 Pull Request 共同改进！
