# MailBot（QQ 邮箱自动翻译/汇总）

从 QQ 邮箱通过 IMAP 读取未读邮件：
- 为原邮件生成「[机器翻译]」双语版本（沉浸式克隆注入，保留结构与样式），并写回原文件夹；
- 将多封未读邮件合并输出「[机器总结]」汇总邮件；
- 新邮件标记为未读，同时将原文置为已读；并通过关联头防止重复处理。

— 本 README 已全面更新并替换旧内容 —

**功能亮点**
- 沉浸式翻译：克隆原块仅替换文本节点，保留标签/样式（跳过 blockquote 历史引用），先内联 CSS 保障客户端兼容。
- 幂等与关联：新邮件带 `In-Reply-To / References / X-Linked-Message-Id`，扫描前先查关联避免重复。
- 快速自测：Mock LLM（本地秒级）、一键种子与断言脚本，真实最小闭环可快速回归。
- 调度运行：间隔（翻译）+ Cron（汇总）组合调度，时区可配。

**目录结构（核心）**
- `mailbot/` 程序主包
  - `jobs.py`：`translate_job` / `summarize_job` 调度作业
  - `immersion.py`：沉浸式注入（克隆替换文本节点，注入 `<br>`，内联 CSS）
  - `imap_client.py`：IMAP 基础（连接/读取/写回/置已读/去重/回退文件夹）
  - `mock_llm.py`：本地 Mock 翻译/总结
  - `scheduler.py`：阻塞式调度（BlockingScheduler）
  - `utils.py`：主题解码、分片等
- `scripts/` 常用脚本（端到端测试、断言、列文件夹等）
- `run.py` 命令行入口（一次性与作业模式皆可）
- `config.json` 连接与作业配置


## 环境准备
1) 进入项目目录：`D:\User_Files\Program Files\imapTLDR2`
2) 创建虚拟环境并安装依赖：
   - `python -m venv venv`
   - `./venv/Scripts/pip install -r requirements.txt`
   - `./venv/Scripts/pip install apscheduler pytz`  （调度所需）


## 配置（config.json）
最小示例（请用你的实际信息替换）：
```json
{
  "imap": {
    "server": "imap.qq.com",
    "port": 993,
    "ssl": true,
    "email": "you@qq.com",
    "password": "QQ邮箱IMAP授权码",
    "folder": "INBOX"
  },
  "llm": {
    "mock": true,
    "siliconflow": { "api_base": "https://api.siliconflow.cn", "api_key": "sk-xxx" },
    "translator_model": "Qwen/Qwen2.5-7B-Instruct",
    "summarizer_model": "deepseek-ai/DeepSeek-V3.2-Exp",
    "enable_thinking": true,
    "thinking_budget": 4096,
    "prompt_file": "Prompt.txt",
    "request_timeout_seconds": 15,
    "translate_timeout_seconds": 15,
    "summarize_timeout_seconds": 15
  },
  "translate": {
    "interval_minutes": 10,
    "folders": ["其他文件夹/IJSS", "其他文件夹/CMAME"],
    "inbox_keywords": ["相关研究汇总", "快讯汇总"],
    "inbox_from": ["scholaralerts-noreply@google.com"],
    "max_per_run_per_folder": 3
  },
  "summarize": {
    "cron": ["0 7 * * *", "0 12 * * *", "0 19 * * *"],
    "folders": ["其他文件夹/Nature", "其他文件夹/APS Extended"],
    "batch_size": 10,
    "chunk_tokens": 16000
  },
  "prefix": { "translate": "[机器翻译]", "summarize": "[机器总结]" },
  "timezone": "Asia/Shanghai"
}
```
提示：
- 生产环境请将 `llm.mock` 设为 `false`（或移除）并正确配置 API；Mock 开启时不会访问外网。
- 目标文件夹不存在会自动回退 `INBOX`；中文/带空格或点的文件夹名可直接使用（如 `其他文件夹/Def. Technol.`）。
- 兼容旧配置：如果你仍使用顶层 `siliconflow` / `siliconflow2` 字段，代码也能识别（但推荐迁移到 `llm.siliconflow`）。


## 快速开始（本地 Mock，不走外网）
- 端到端快测（种子 → 翻译 → 汇总）：
  - `./venv/Scripts/python ./scripts/test_fast.py`
- 断言关键行为（In-Reply-To / X-Linked-Message-Id / 注入结构）：
  - `./venv/Scripts/python ./scripts/assert_e2e.py`
- 查看最近带前缀的邮件：
  - `./venv/Scripts/python ./scripts/check_appended.py`
- 注入更多种子用例（纯文本/富文本/引用/关键词/发件人命中）：
  - `./venv/Scripts/python ./scripts/seed_cases.py`


## 真实最小闭环（快速回归）
- 限定 `INBOX`，每批 1 封，立刻执行（不开 Mock 将真实请求 LLM，已设置超时与降级）：
  - `./venv/Scripts/python ./scripts/virtual_run.py`


## 命令行用法（一次性与作业）
- 一次性：
  - `./venv/Scripts/python run.py translate [max]`
  - `./venv/Scripts/python run.py summarize [folder] [batch]`
  - `./venv/Scripts/python run.py smoke`（小型 e2e：翻译 1 + 汇总 1）
- 作业（与调度同款逻辑）：
  - `./venv/Scripts/python run.py translate_job`
  - `./venv/Scripts/python run.py summarize_job`
- 列出当前邮箱所有文件夹：
  - `./venv/Scripts/python ./scripts/list_folders.py`


## 调度运行（BlockingScheduler）
- 启动：`./venv/Scripts/python -m mailbot.scheduler`
- 关键配置：
  - `translate.interval_minutes`（默认 10）
  - `summarize.cron`（默认 `0 7/12/19 * * *`）
  - `timezone`（默认 `Asia/Shanghai`）


## 关键行为说明
- 沉浸式注入：
  - 克隆原块（`p/li/div/td/h1…`）并仅替换文本节点，插入 `<br>` 分隔；跳过 `blockquote`；先内联 CSS 再注入（见 `mailbot/immersion.py`）。
- 幂等防重：
  - 新邮件带 `X-Linked-Message-Id = 原文 Message-ID`；扫描前在最近邮件中查找已有关联的译文/汇总，有则跳过（见 `mailbot/imap_client.py` + `mailbot/jobs.py`）。
- 写回策略：
  - 新邮件设置为未读，原文置已读；主题前缀使用 `[机器翻译]` 或 `[机器总结]`；自动加上 `Auto-Submitted / X-Auto-Response-Suppress / In-Reply-To / References / X-Linked-Message-Id`。
- QQ IMAP 兼容性：
  - 为避免非 ASCII 搜索问题，SEARCH 仅用 `UNSEEN`，其余过滤在客户端侧完成。


## 故障与提示
- 控制台打印的中文主题可能乱码（显示编码所致），不影响邮箱内容与断言脚本。
- 真实 LLM 请求默认 15s（可在 `llm.translate_timeout_seconds` / `llm.summarize_timeout_seconds` 或通用 `llm.request_timeout_seconds` 配置）：翻译超时降级为空串、总结降级为 `(summary timeout or error)`，程序不中断。
- 目标文件夹不存在会自动回退 `INBOX`。


## 安全与最佳实践
- 切勿将真实邮箱授权码/API Key 提交到仓库；本地可使用 `.env` 或环境变量。
- 建议准备「mock / prod」两套 profile（通过 `llm.mock` 与不同的 folders/batch 快速切换）。

如果需要我为你定制默认文件夹、关键词、调度规则或对接其他 LLM 服务，告诉我即可。