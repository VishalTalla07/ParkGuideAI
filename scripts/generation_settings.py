"""Provider settings and local credentials; never log or expose key values."""
import json
import os
from pathlib import Path
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
QUOTA_MESSAGE = 'Free quota reached—please try again later.'


def generation_config():
    return json.loads((ROOT / 'config/answering.json').read_text())


def provider_name(config):
    return 'Google Gemini' if config.get('generation_provider', 'openai') == 'gemini' else 'OpenAI'


def api_key(config, root=ROOT):
    variable = 'GEMINI_API_KEY' if config.get('generation_provider', 'openai') == 'gemini' else 'OPENAI_API_KEY'
    value = os.environ.get(variable) or dotenv_values(root / '.env').get(variable) or ''
    value = value.strip()
    if value.lower() in {'your_api_key_here', 'paste_your_key_here', 'your_gemini_api_key_here'}:
        return ''
    return value
