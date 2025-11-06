# Simulate the scheduled jobs with virtual time (run immediately)
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mailbot.config import load_config
from mailbot.jobs import translate_job, summarize_job

cfg = load_config()

# shrink scope for quick real-call testing
cfg.setdefault('translate', {})
cfg['translate']['max_per_run_per_folder'] = 1
cfg['translate']['folders'] = ['INBOX']  # only inbox for speed

cfg.setdefault('summarize', {})
cfg['summarize']['folders'] = ['INBOX']
cfg['summarize']['batch_size'] = 1
cfg['summarize']['chunk_tokens'] = 4000

print('--- virtual translate job ---')
translate_job(cfg)
print('--- virtual summarize job ---')
summarize_job(cfg)
print('done.')
