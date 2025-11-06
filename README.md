# MailBot（QQ 邮箱「机器总结」）

用 IMAP 读取 QQ 邮箱的未读邮件，按批次汇总为一封主题带前缀的「[机器总结]」邮件并投递回收件箱：
- 新生成的汇总邮件强制为未读（UNSEEN）；
- 原始参与汇总的邮件在成功汇总后标记为已读；
- 通过“尽量少的服务器端搜索 + 客户端过滤 + 兜底遍历”的策略，规避 QQ IMAP 的 SEARCH 结果截断与中文搜索兼容性问题；
- 优先引导大模型严格输出 JSON（见 `Prompt.txt`），本地渲染为统一样式的卡片 HTML；当模型未能输出合规 JSON 时，自动退化为简明文本要点或模型原生 HTML。

本仓库已精简为“仅机器总结”功能，所有翻译相关代码已移除。

推荐运行环境：Python 3.10+（代码使用 `str | None` 等 3.10 语法）。

## 功能特点
- QQ 兼容：只在服务器端做 ASCII 安全的最小化搜索，UNSEEN 枚举支持“鲁棒模式”（按 UID 范围分块拉取 FLAGS 并在本地过滤）。
- 汇总投递：新邮件以 `Auto-Submitted: auto-generated` 标识，附带 `X-Linked-Message-Id` 链接原始会话，追加后强制移除 `\\Seen` 确保未读。
- 渲染优先级：优先渲染 JSON 卡片；否则回退为要点列表；再否则直接渲染 HTML 片段。
- 观测与复现：每次运行把“提交给模型的所有片段 + 模型回答 + 元信息”写入 `data/summarize-YYYYMMDD-HHMMSS.json`。
- 可选调度：提供基于 APScheduler 的阻塞式调度器，可按 crontab 表达式定时汇总。

## 目录结构
- `mailbot/` 核心逻辑
  - `imap_client.py`：IMAP 连接、未读枚举（含鲁棒模式）、读/写、已读/未读控制、追加邮件 UNSEEN 强制
  - `jobs.py`：调度式/批处理式汇总主流程、卡片 HTML 渲染、数据落盘、LLM 调用适配
  - `summarize.py`：一次性汇总（对某个文件夹按批次执行，快速体验）
  - `llm.py` / `mock_llm.py`：OpenAI 兼容客户端与本地 mock 策略
  - `scheduler.py`：基于 APScheduler 的阻塞式调度器（可选）
  - `config.py` / `utils.py`：配置与常用工具
- `run.py`：命令行入口
- `config.example.json`：配置示例（复制为 `config.json` 后填写）
- `Prompt.txt`：用于“JSON 卡片”输出的系统提示词（可按需修改）
- `data/`：运行时数据快照（自动创建）

## 安装
在项目目录（例如 `D:\\User_Files\\Program Files\\imapTLDR3`）执行：
- 创建虚拟环境并安装必需依赖：
  - `python -m venv venv`
  - `./venv/Scripts/pip install -r requirements.txt`
- 调度可选依赖（如需启用定时调度）：
  - `./venv/Scripts/pip install apscheduler pytz`

## 配置（config.json）
复制 `config.example.json` 为 `config.json` 并填写实际信息。关键字段如下（节选）：

```json
{
  "imap": {
    "server": "imap.qq.com",
    "port": 993,
    "ssl": true,
    "email": "you@example.com",
    "password": "YOUR_IMAP_AUTH_CODE",
    "folder": "INBOX"
  },
  "prefix": { "summarize": "[机器总结]" },
  "timezone": "Asia/Shanghai",
  "summarize": {
    "cron": ["0 7 * * *", "0 12 * * *", "0 19 * * *"],
    "folders": ["其他文件夹/Nature", "其他文件夹/APS Extended", "其他文件夹/PNAS"],
    "batch_size": 10,
    "chunk_tokens": 16000,
    "unseen_fetch_chunk": 500,
    "max_unseen_per_run_per_folder": 0,
    "scan_order": "newest"
  },
  "llm": {
    "mock": true,
    "siliconflow": {
      "api_base": "https://api.siliconflow.cn",
      "api_key": "YOUR_SILICONFLOW_API_KEY",
      "model": "deepseek-ai/DeepSeek-V3.2-Exp"
    },
    "summarizer_model": "deepseek-ai/DeepSeek-V3.2-Exp",
    "enable_thinking": true,
    "thinking_budget": 4096,
    "prompt_file": "Prompt.txt",
    "request_timeout_seconds": 15,
    "summarize_timeout_seconds": 15
  }
}
```

说明：
- 将 `imap.password` 设置为 QQ 邮箱 IMAP 授权码。
- 若仅测试渲染流程，可将 `llm.mock` 设为 `true`（不会调用外部服务）。
- 使用真实 LLM 时，设置 `llm.siliconflow.api_key` 与 `api_base`，并在 `summarizer_model` 指定模型。
- `Prompt.txt` 控制 JSON 卡片字段（标题、作者、要点、相关性、打分、标签等）；可按领域自行修改。

## 使用方法
- 一次性汇总（按未读与前缀排除，按批次处理）：
  - `./venv/Scripts/python run.py summarize [folder] [batch]`
  - 示例：`./venv/Scripts/python run.py summarize INBOX 5`
- 作业式汇总（与调度相同逻辑，包含美化与数据保存）：
  - `./venv/Scripts/python run.py summarize_job`
- 启动调度（可选）：
  - `./venv/Scripts/python -m mailbot.scheduler`

输出行为：
- 新生成的汇总邮件主题以 `prefix.summarize` 开头，例如：`[机器总结] INBOX（10封）`。
- 汇总邮件会被追加到同一文件夹并强制保持未读；被汇总的原邮件将被标记为已读。

## 日志与数据
- 统一日志记录到标准输出（INFO 级别，带时间戳）。
- 每次运行都会在 `data/` 目录生成 `summarize-YYYYMMDD-HHMMSS.json`，包含：
  - 提交给模型的每个片段（文本与估算 token）；
  - 模型回答（含 reasoning/thinking 字段的最佳努力提取）；
  - 运行元信息（模型、是否启用思维链、批次大小、时间戳等）。

## 兼容性细节（QQ IMAP）
- 搜索策略：仅用 ASCII 安全的 `UNSEEN` 和少量 HEADER 搜索，其余前缀/自动投递判断在客户端过滤，避免中文导致的 SEARCH 失败。
- 鲁棒枚举：当 `robust=true` 时按 UID 范围分块拉取 FLAGS 并在本地筛选未读，规避服务端截断（参见 `mailbot/imap_client.py:1` 的 `list_unseen_robust`）。
- 未读强制：新邮件投递后，会尽力移除 `\\Seen` 标志以保证“汇总邮件为未读”（参见 `mailbot/imap_client.py:1` 的 `append_unseen`）。

## 常见问题（FAQ）
- 未读数量比预期少？
  - 已默认启用鲁棒枚举；可调 `summarize.unseen_fetch_chunk`，必要时增大以覆盖更大的 UID 段。
- 模型超时或返回非 JSON？
  - 会自动降级为文本要点或原生 HTML；你可调整 `llm.*timeout_seconds`、`Prompt.txt` 或改小 `chunk_tokens`。
- 调度不生效？
  - 确认已安装 `apscheduler`/`pytz`，并检查 `summarize.cron` 与 `timezone` 配置。

## 开发者提示
- 入口脚本：`run.py:1`（命令解析与调用）。
- 一次性汇总：`mailbot/summarize.py:1`。
- 调度/批处理：`mailbot/jobs.py:1`（核心业务流）。
- IMAP 封装与兼容：`mailbot/imap_client.py:1`。
- LLM 调用：`mailbot/llm.py:1`，本地模拟：`mailbot/mock_llm.py:1`。
- 调度器：`mailbot/scheduler.py:1`。
- 配置加载：`mailbot/config.py:1`。

如需调整卡片样式（配色/字号等），可修改 `mailbot/jobs.py` 中的 HTML 片段；如需让 `summarize_once` 也始终保存数据快照，可参考 `summarize_job` 的落盘逻辑。

—
仅供学习与研究使用，请勿用于发送垃圾邮件或违反服务条款的用途。

