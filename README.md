# MailBot（QQ 邮箱自动总结 + 翻译）

MailBot 是面向 QQ 邮箱的 IMAP 自动化机器人。它按文件夹扫描未读邮件，批量生成带`[机器总结]`前缀的摘要邮件，并将重点期刊/关键词邮件“原位、双语”翻译为`[机器翻译]`邮件。项目内置稳健的 APScheduler 调度、LLM 限流/重试、可观测性与丰富的调试脚本，适合自用的学术情报收集与日报场景。

## 核心特性

- **只处理 UNSEEN**：所有操作都基于未读邮件，处理完成自动 mark seen，并把输出写回源文件夹，流程可重复执行。
- **总结 + 翻译双引擎**：总结支持 DeepSeek (SiliconFlow) 与 Gemini 管道，翻译默认使用 Qwen（可 fallback DeepSeek）。支持 mock LLM 便于离线调试。
- **稳健的 HTML 注入**：内置 immersion/inplace 三套注入策略，保证 a/code/pre 等节点不被破坏，可自由切换“原位替换”“行内分段”“全文沉浸”。
- **速率与并发控制**：翻译任务使用线程池 + 令牌桶控制 RPM/TPM，逐段重试并缓存同一邮件内的重复片段，提升稳定性。
- **智能调度**：APScheduler 负责任务编排。翻译按“固定延迟”循环，摘要按 Cron 或紧跟翻译执行并具备错过补跑；Ctrl+C 可即时退出。
- **可观测性**：日志统一格式/色彩，支持非 UTF 终端；`data/`目录保存每次摘要的 JSON（可通过 `summarize.save_summary_json` 关闭）。
- **脚本工具箱**：`scripts/` 目录提供邮箱调试、摘要链路验证、IMAP 统计等辅助脚本，便于排障。

## 目录速览

```
mailbot/
  ├─ config.py           # UTF-8-SIG 读取配置
  ├─ imap_client.py      # IMAP 连接、搜索、取信、写信
  ├─ immersion.py        # 翻译注入策略 + DOM 修复
  ├─ jobs.py             # summarize/translate 任务与 LLM 调用
  ├─ scheduler.py        # APScheduler 封装（错过补跑、Ctrl+C 快退）
  ├─ summarize.py        # 单次 summarize 入口（调试、手动触发）
  ├─ llm.py / mock_llm.py# LLM 客户端与本地 mock
  └─ utils.py            # prefix 过滤、分片、token 估算等
scripts/                # 调试脚本（test_gemini_x666、count_folder_messages…）
data/                   # 摘要 JSON 日志，便于回溯
Prompt.txt               # 摘要提示模版，可自定义
config(.example).json    # 主配置，需填写 QQ IMAP 授权码等
run.py                   # CLI 入口，支持 summarize / summarize_job
```

## 环境要求

- Windows / Linux / macOS，Python 3.10+（建议 3.11，因为项目默认如此测试）
- 已开启 QQ 邮箱 IMAP，并使用“专用授权码”登录
- 具备可用的 LLM API Key（SiliconFlow、x666 Gemini 等）

## 快速开始

1. **获取代码**
   ```powershell
   git clone <repo-url>
   cd imapTLDR3
   ```
2. **创建虚拟环境并安装依赖**
   ```powershell
   python -m venv venv
   ./venv/Scripts/Activate.ps1
   pip install -r requirements.txt
   ```
3. **准备配置**
   ```powershell
   copy config.example.json config.json
   ```
   - 填写 `imap.email`、`imap.password`（QQ IMAP 授权码）、`llm` 中的 API Key。
   - 根据自己邮箱结构设置 `summarize.folders`、`translate.folders` 等。
4. **首跑验证**（单次 summarize）：
   ```powershell
   python run.py summarize INBOX 3
   ```
   观察控制台日志 + `data/summarize-*.json` 是否产出。
5. **进入常驻模式**：
   ```powershell
   python -m mailbot.scheduler
   ```
   - 启动后立刻执行一次 summarize（预热）、1 秒后启动 translate 队列。
   - 翻译按 `translate.interval_minutes` 循环；摘要按 Cron 或“跟随翻译”执行。

## 配置说明

> 所有配置均位于 `config.json`，读取时使用 UTF-8-SIG，兼容含 BOM 文件。

### `imap`
- `server`/`port`/`ssl`：QQ 默认为 `imap.qq.com:993` + SSL。
- `email` / `password`：邮箱地址与 IMAP 授权码。
- `folder`：`run.py summarize` 的默认扫描文件夹。

### `prefix`
- `summarize` / `translate`：生成邮件使用的前缀。默认 `[机器总结]`、`[机器翻译]`。

### `timezone`
- 影响调度与日志的显示时区，默认为 `Asia/Shanghai`。

### `summarize`
- `cron`: Cron 表达式数组，用于 APScheduler（如 `0 7 * * *`）。
- `folders`: 需要被合并摘要的 QQ 文件夹，支持 “其他文件夹/xxx” 形式。
- `batch_size`: `run.py summarize` 单次处理上限。
- `chunk_tokens`: 根据 Rough Token 估算分段，LLM 输入更稳定。
- `unseen_fetch_chunk` / `max_unseen_per_run_per_folder` / `scan_order`: 控制 IMAP 拉取与截断策略。
- `follow_translate_interval`: true 时，摘要改为紧跟翻译执行，cron 失效。
- `save_summary_json`: 控制是否生成 `data/summarize-*.json` 记录，默认 true，可在隐私场景关闭。

### `translate`
- `interval_minutes`: 每次完成后下一次的延迟（fixed-delay）。
- `folders`: 批量翻译的 QQ 文件夹；`inbox_keywords`/`inbox_from` 额外指定 INBOX 规则。
- `max_per_run_per_folder`: 单文件夹每次最多翻译几封。
- `inplace_replace`/`strict_line`: 选择“原位双语”或“行内注入”策略。
- `concurrency`: 翻译线程池大小，建议 6-10；
- `rpm_limit` / `tpm_limit`: 令牌桶参数，对应 LLM 速率限制；
- `max_retry` / `force_retranslate`: 控制重试次数以及是否对已有回复邮件再次翻译。
- `delete_translated_email`: true 时在写入翻译邮件后删除原始邮件（谨慎开启）。

### `llm`
- `mock`: true 时使用 `mailbot.mock_llm`，无需真实 API。
- `siliconflow` / `bohe`: provider 连接信息 + 模型名称。
- `summarizer_provider`: `"bohe"` 或默认 `"siliconflow"`。
- `summarizer_model` / `translator_model`: 深度定制模型。
- `enable_thinking` / `thinking_budget`: DeepSeek/Gemini Thinking 模式控制。
- `prompt_file`: 摘要提示词文件（默认 `Prompt.txt`）。
- `request_timeout_seconds`、`summarize_timeout_seconds`、`translate_timeout_seconds`: API 调用超时。

## 运行方式

| 场景 | 命令 | 说明 |
| --- | --- | --- |
| 调度常驻 | `python -m mailbot.scheduler` | 同时管理 summarize + translate。Ctrl+C 即刻退出，无需等待任务。 |
| 只跑 summarize 流水线（cron 逻辑） | `python run.py summarize_job` | 适合配合系统级调度器（Task Scheduler / systemd timer）。 |
| 临时抽样摘要 | `python run.py summarize <folder> <batch>` | 不写入 APScheduler，便于检查配置效果。 |

## 日志与观测

- **格式**：`YYYY-MM-DD HH:MM:SS | mailbot | LEVEL | message`，关键字（START/DONE/NEXT/WARN/FLAG）带中文标签与颜色。
- **降噪**：自动降低 httpx/openai/requests/apscheduler 等第三方日志级别。
- **运行记录**：
  - 摘要：`data/summarize-*.json` 保存 meta + 每段 chunk 输出，可关闭。
  - 错误：异常会打印堆栈，并继续处理后续邮件。
- **调试脚本**：
  - `scripts/test_gemini_x666.py`：验证 Bohe/Gemini 接口。
  - `scripts/debug_fetch_emails.py`：快速查看 IMAP 邮件体。
  - `scripts/count_folder_messages.py`：统计文件夹邮件数量。
  - 其余脚本可帮助排查翻译注入、LLM JSON 返回等问题。

## 常见问题

- **为什么翻译样式与网页不同？** 邮件客户端通常屏蔽外链 CSS，程序已尝试 inline CSS；若遇到 403/超时会降级为原始 HTML，保证流程继续。
- **如何避免重复翻译？** 通过主题前缀过滤、`has_linked_reply` 检查与 `mark_seen` 实现幂等，除非显式开启 `force_retranslate`。
- **速率限制怎么配？** `rpm_limit`/`tpm_limit` 直接映射至线程池内的令牌桶，建议与服务商限制一致，避免 429。
- **如何关闭摘要 JSON 落盘？** 将 `summarize.save_summary_json` 设为 `false` 即可，不影响其它功能。
- **能否只跑翻译？** 可以自定义外部调度（只触发 `translate_job`）或在 scheduler 中将 `summarize.folders` 置空。

## 注意事项

- 不要将真实 API Key/授权码提交到版本库。
- QQ 邮箱若开启两步验证，请务必使用“第三方客户端专用授权码”。
- 邮件操作涉及隐私，请遵循相关法律法规，并知会邮箱实际主人。
- 若需文件日志，可在 `_setup_logging` 中额外添加 `RotatingFileHandler`，保持同一格式即可。

## 许可证

本仓库仅供个人学习与研究，请勿用于违反邮箱服务条款的场景。如需商用或二次分发，请事先征得作者许可。
