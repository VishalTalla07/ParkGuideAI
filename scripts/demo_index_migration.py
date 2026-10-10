"""Re-chunk and replace vector/sparse indexes with socket access blocked."""
import copy
import json
from unittest.mock import patch

from chunk_sources import build_snapshot
from retrieval import ROOT, Embedder, IndexStore


def main():
    chunks_config = json.loads((ROOT / 'config/chunking.json').read_text())
    retrieval_config = json.loads((ROOT / 'config/retrieval.json').read_text())
    documents = ROOT / 'data/normalized/documents.jsonl'
    chunks_output = ROOT / 'data/chunks'
    embedder = Embedder(retrieval_config)
    store = IndexStore(retrieval_config, development_preview=True)
    alternate_config = copy.deepcopy(chunks_config)
    alternate_config['target_body_tokens'] = 160
    with patch('socket.socket', side_effect=RuntimeError('Network access forbidden during index migration')):
        baseline_chunks = build_snapshot(documents, chunks_config, chunks_output)
        baseline = store.build(chunks_output / 'versions' / baseline_chunks['version'], embedder)
        alternate_chunks = build_snapshot(documents, alternate_config, chunks_output)
        alternate = store.build(chunks_output / 'versions' / alternate_chunks['version'], embedder)
        restored_chunks = build_snapshot(documents, chunks_config, chunks_output)
        restored = store.build(chunks_output / 'versions' / restored_chunks['version'], embedder)
    if baseline['version'] != restored['version']:
        raise RuntimeError('Index identity did not reproduce after restoring the default chunk configuration')
    manifest = store.validate(store.directory / 'versions' / restored['version'])
    expected_names = {entry['collection'] for entry in manifest['strategies'].values()}
    if {c.name for c in store.client.list_collections()} != expected_names:
        raise RuntimeError('Superseded vector collection remains')
    receipt = {'mode': store.mode, 'network_access': 'blocked during re-chunking, embedding, and index migration',
               'baseline_index_version': baseline['version'], 'alternate_index_version': alternate['version'],
               'restored_index_version': restored['version'], 'default_index_reproduced': True,
               'removed_old_only_chunk_ids': restored['migration']['old_only_ids_removed'],
               'removed_vector_collections': restored['migration']['removed_collections'],
               'active_vector_sparse_manifest_ids_match': True, 'orphan_collections': 0, 'orphan_sparse_versions': 0,
               'note': 'Engineering migration demonstration; same normalized corpus, no crawl, no gold-set retrieval evaluation.'}
    (ROOT / 'data/audit/index_migration_demo.json').write_text(json.dumps(receipt, indent=2) + '\n')
    history = ROOT / 'data/audit/index_runs'
    history.mkdir(exist_ok=True)
    for report in (alternate, restored):
        (history / (report['version'] + '.json')).write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
