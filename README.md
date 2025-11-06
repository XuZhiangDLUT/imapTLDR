# MailBot（QQ 邮箱自动机器总结）

从 QQ 邮箱通过 IMAP 读取未读邮件，将多封未读邮件合并输出「[机器总结]」汇总邮件：
- 新生成的汇总邮件强制标记为未读；
- 原邮件在汇总后强制标记为已读；
- 通过主题前缀与最小化搜索策略，兼容 QQ IMAP 的限制。

本项目已精简为“仅机器总结”功能，所有与机器翻译相关的代码与文件已移除。为稳定美观的卡片式输出，系统优先要求模型返回严格 JSON（见 `Prompt.txt`），随后在本地渲染为统一样式 HTML；当模型未能按 JSON 返回时，自动退化为文本要点或模型自带 HTML。

**目录结构（核心）**
- `mailbot/` 程序主包
  - `jobs.py`：`summarize_job`（作业逻辑，含日志、美化、数据保存）
  - `imap_client.py`：IMAP 基础（连接/读取/写回/置已读/未读强制）
  - `scheduler.py`：阻塞式调度（可选）
  - `llm.py` / `mock_llm.py` / `utils.py`
- `run.py` 命令行入口
- `config.example.json` 配置示例（复制为 `config.json` 并填入密钥）
- `data/` 总结作业输入快照（JSON，自动创建）

## 环境准备
1) 进入项目目录：`D:\User_Files\Program Files\imapTLDR3`
2) 创建虚拟环境并安装依赖：
   - `python -m venv venv`
   - `./venv/Scripts/pip install -r requirements.txt`
   - 调度可选：`./venv/Scripts/pip install apscheduler pytz`

## 配置（config.json）
- 将 `config.example.json` 复制为 `config.json`，并填入真实邮箱授权码/API Key。
- 示例（节选）：
```json
{
  "imap": { "server": "imap.qq.com", "port": 993, "ssl": true, "email": "you@qq.com", "password": "YOUR_IMAP_AUTH_CODE", "folder": "INBOX" },
  "llm": {
    "mock": true,
    "siliconflow": { "api_base": "https://api.siliconflow.cn", "api_key": "YOUR_SILICONFLOW_API_KEY", "model": "deepseek-ai/DeepSeek-V3.2-Exp" },
    "summarizer_model": "deepseek-ai/DeepSeek-V3.2-Exp",
    "enable_thinking": true,
    "thinking_budget": 4096,
    "prompt_file": "Prompt.txt",
    "request_timeout_seconds": 15,
    "summarize_timeout_seconds": 15
  },
  "summarize": {
    "cron": ["0 7 * * *", "0 12 * * *", "0 19 * * *"],
    "folders": ["其他文件夹/Nature","其他文件夹/APS Extended","其他文件夹/PNAS"],
    "batch_size": 10,
    "chunk_tokens": 16000
  },
  "prefix": { "summarize": "[机器总结]" },
  "timezone": "Asia/Shanghai"
}
```

## 运行方式
- 一次性（按未读与前缀排除，按批汇总）：
  - `./venv/Scripts/python run.py summarize [folder] [batch]`
- 作业（与调度同款逻辑，含美化与数据保存）：
  - `./venv/Scripts/python run.py summarize_job`
- 调度（可选）：
  - `./venv/Scripts/python -m mailbot.scheduler`

提示：若发现 UNSEEN 数量少于预期，系统已默认启用“robust 枚举”（通过 ALL+FLAGS 分块读，绕过服务器 SEARCH 限制），相关参数：
- `summarize.unseen_fetch_chunk`（默认 500）
- `summarize.max_unseen_per_run_per_folder`（默认 0 不限）
- `summarize.scan_order`（`newest`/`oldest`）

## 日志与数据
- 日志含时间戳（INFO）：扫描文件夹、检测/处理主题、追加结果、Unread 强制等。
- 作业会将“提交给总结模型的全部片段”写入 `data/summarize-YYYYMMDD-HHMMSS.json`，便于复现与排查。

## 说明
- cssutils 噪声已静音（仅用于内联 CSS）。
- 搜索避免非 ASCII（仅用 UNSEEN），其它过滤在客户端侧完成。

如需我调整默认样式/配色/字号，或将 summarize_once 也保存数据快照，请告诉我。
