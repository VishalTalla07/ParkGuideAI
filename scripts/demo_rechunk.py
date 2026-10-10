"""Demonstrate local chunk migration while network access is explicitly blocked."""
import argparse
import copy
import json
from pathlib import Path
from unittest.mock import patch

from chunk_sources import ROOT, build_snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--documents', type=Path, default=ROOT / 'data/normalized/documents.jsonl')
    parser.add_argument('--config', type=Path, default=ROOT / 'config/chunking.json')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'data/chunks')
    parser.add_argument('--audit-dir', type=Path, default=ROOT / 'data/audit')
    parser.add_argument('--alternate-target', type=int, default=160)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    alternate_config = copy.deepcopy(config)
    alternate_config['target_body_tokens'] = args.alternate_target
    if config == alternate_config:
        parser.error('Alternate target must differ from the default')
    with patch('socket.socket', side_effect=RuntimeError('Network access forbidden during rechunking')):
        baseline = build_snapshot(args.documents, config, args.output_dir)
        alternate = build_snapshot(args.documents, alternate_config, args.output_dir)
        restored = build_snapshot(args.documents, config, args.output_dir)
    versions = list((args.output_dir / 'versions').iterdir())
    if baseline['version'] != restored['version'] or len(versions) != 1 or versions[0].name != restored['version']:
        raise RuntimeError('Migration did not restore a single reproducible snapshot')
    receipt = {'network_access': 'blocked via socket.socket during all builds',
               'initial_version': baseline['version'], 'alternate_version': alternate['version'],
               'restored_version': restored['version'], 'default_version_reproduced': True,
               'previous_chunk_ids_removed': restored['migration']['old_only_ids_removed'],
               'orphan_versions': 0, 'active_manifest_verified': True,
               'scope': 'Local chunk snapshots only; vector and BM25 index migration is not implemented yet.'}
    args.audit_dir.mkdir(parents=True, exist_ok=True)
    (args.audit_dir / 'chunking_summary.json').write_text(json.dumps(restored, indent=2) + '\n')
    (args.audit_dir / 'rechunk_demo.json').write_text(json.dumps(receipt, indent=2) + '\n')
    history = args.audit_dir / 'chunk_runs'
    history.mkdir(exist_ok=True)
    for result in (alternate, restored):
        (history / (result['version'] + '.json')).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
