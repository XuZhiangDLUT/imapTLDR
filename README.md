# MailBot（QQ 邮箱：机器总结 + 机器翻译）

基于 IMAP 读取 QQ 邮箱未读邮件，提供两类自动化处理：
- 机器总结：按批次汇总为一封主题带前缀的「[机器总结]」邮件并投递回收件箱；
- 机器翻译：对指定文件夹/关键字的来信进行沉浸式双语内嵌翻译，主题带前缀「[机器翻译]」。

特性：
- 新生成邮件强制为未读（UNSEEN）；原始被处理邮件标记为已读；
- 服务器端仅做 ASCII 安全搜索，其余在客户端过滤，可选“鲁棒枚举未读”规避 SEARCH 截断；
- 总结优先产出 JSON 卡片（Prompt.txt 可自定义），失败时回退为简明文本/模型原生 HTML；
- 翻译采用逐行 + 保守两阶段沉浸式注入，尽量保持排版样式；
- 每次总结作业会把提交片段与模型输出写入 data/ 快照文件，便于观测复现。

## 目录
- mailbot/
  - imap_client.py：IMAP 连接、未读枚举、追加邮件 UNSEEN 强制、已读/未读控制
  - immersion.py：沉浸式双语注入（从 imapTLDR2 抽取）
  - jobs.py：翻译与总结两个作业（核心业务流）
  - summarize.py：一次性汇总（便捷体验）
  - llm.py / mock_llm.py：OpenAI 兼容客户端与本地 mock
  - scheduler.py：基于 APScheduler 的阻塞式调度器（可选）
  - config.py / utils.py：配置加载与工具函数
- run.py：命令行入口
- config.example.json：示例配置（复制为 config.json 后填写）
- Prompt.txt：用于 JSON 卡片输出的系统提示词
- data/：运行时数据快照（自动创建）

## 安装
在项目目录（例如 `D:\User_Files\Program Files\imapTLDR3`）执行：
- `python -m venv venv`
- `./venv/Scripts/pip install -r requirements.txt`
- （可选）调度依赖：`./venv/Scripts/pip install apscheduler pytz`

## 配置（config.json）
复制 `config.example.json` 为 `config.json` 并填写实际信息。关键字段（节选）：
```json
{
  "imap": { "server": "imap.qq.com", "port": 993, "ssl": true, "email": "you@example.com", "password": "YOUR_IMAP_AUTH_CODE", "folder": "INBOX" },
  "prefix": { "summarize": "[机器总结]", "translate": "[机器翻译]" },
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
  "translate": {
    "interval_minutes": 10,
    "folders": ["IJSS", "TWS", "JMPS", "EML", "PRL", "IJMS", "IJNME", "CMAME", "ComputerStruct", "SMO", "ES", "NLDyna", "JSV", "IJIE", "OceanEng", "Def. Technol.", "Eur.J.Mech.", "CompositeStruct"],
    "inbox_keywords": ["相关研究汇总", "快讯汇总"],
    "inbox_from": ["scholaralerts-noreply@google.com"],
    "max_per_run_per_folder": 3,
    "strict_line": true
  },
  "llm": {
    "mock": true,
    "siliconflow": { "api_base": "https://api.siliconflow.cn", "api_key": "YOUR_SILICONFLOW_API_KEY", "model": "deepseek-ai/DeepSeek-V3.2-Exp" },
    "summarizer_model": "deepseek-ai/DeepSeek-V3.2-Exp",
    "translator_model": "Qwen/Qwen2.5-7B-Instruct",
    "enable_thinking": true,
    "thinking_budget": 4096,
    "prompt_file": "Prompt.txt",
    "request_timeout_seconds": 15,
    "translate_timeout_seconds": 15,
    "summarize_timeout_seconds": 15
  }
}
```
说明：`imap.password` 为 QQ 邮箱 IMAP 授权码。仅测试流程时可将 `llm.mock` 设为 `true`；使用真实 LLM 时，设置 `llm.siliconflow.api_key` 与相关模型字段。

## 使用
- 一次性汇总（按未读与前缀排除，按批次处理）：
  - `./venv/Scripts/python run.py summarize [folder] [batch]`
  - 示例：`./venv/Scripts/python run.py summarize INBOX 5`
- 作业式汇总：`./venv/Scripts/python run.py summarize_job`
- 启动调度（翻译 + 总结）：`./venv/Scripts/python -m mailbot.scheduler`

输出行为：
- 汇总邮件以 `prefix.summarize` 开头，例如：`[机器总结] INBOX（10封）`；
- 翻译邮件以 `prefix.translate` 开头，正文为双语沉浸式；
- 新邮件追加到同一文件夹并保持未读；原邮件标记为已读。

仅供学习与研究使用，请勿用于发送垃圾邮件或违反服务条款的用途。