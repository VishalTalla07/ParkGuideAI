"""Download only the pinned reranker files; never fetch executable model code."""
import hashlib
import json
import os
from pathlib import Path
import ssl
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
FILES = ['config.json', 'special_tokens_map.json', 'tokenizer.json',
         'tokenizer_config.json', 'vocab.txt', 'model.safetensors']


def main():
    config = json.loads((ROOT / 'config/answering.json').read_text())
    directory = ROOT / config['reranker_directory']
    directory.mkdir(parents=True, exist_ok=True)
    previous_path = directory / 'download_manifest.json'
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
    hashes = {}
    for name in FILES:
        path = directory / name
        if (previous.get('revision') == config['reranker_revision'] and path.exists()
                and hashlib.sha256(path.read_bytes()).hexdigest() == previous.get('sha256', {}).get(name)):
            hashes[name] = previous['sha256'][name]
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://huggingface.co/{config['reranker_model']}/resolve/{config['reranker_revision']}/{name}"
        temporary = path.with_suffix(path.suffix + '.download')
        try:
            with urlopen(url, timeout=120, context=ssl.create_default_context()) as response, temporary.open('wb') as out:
                while data := response.read(1024 * 1024):
                    out.write(data)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        print(f'DOWNLOAD {name}', flush=True)
    manifest = {'model': config['reranker_model'], 'revision': config['reranker_revision'], 'sha256': hashes}
    previous_path.write_text(json.dumps(manifest, indent=2) + '\n')
    print('Pinned reranker model downloaded and checksummed.')


if __name__ == '__main__':
    main()
