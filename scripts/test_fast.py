# Seed → run translate (mock) → run summarize (mock)
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mailbot.config import load_config
from mailbot.jobs import translate_job, summarize_job

# 1) seed a test email
import scripts.seed_test_mail as seed

# 2) load cfg & enable mock + small scope
cfg = load_config()
cfg.setdefault('llm', {})['mock'] = True
cfg.setdefault('translate', {})['folders'] = ['INBOX']
cfg['translate']['max_per_run_per_folder'] = 1
cfg.setdefault('summarize', {})['folders'] = ['INBOX']
cfg['summarize']['batch_size'] = 1
cfg['summarize']['chunk_tokens'] = 2000

print('--- run translate_job (mock) ---')
translate_job(cfg)
print('--- run summarize_job (mock) ---')
summarize_job(cfg)
print('done fast test.')
