"""Session-independent chat orchestration; source passages remain the only evidence."""
import json
import os
import re
from pathlib import Path
import threading
import time
from urllib.error import HTTPError, URLError
from generation_settings import api_key, generation_config, QUOTA_MESSAGE

ROOT = Path(__file__).resolve().parents[1]
PARKS = {'All three parks': None, 'Yosemite': 'yose', 'Yellowstone': 'yell', 'Grand Canyon': 'grca'}
REASONS = {
    'empty_context': 'No sources match this question and the selected filters. Try another park or remove the date filter.',
    'corpus_review_pending': 'Cited answers are unavailable until the source corpus has been reviewed.',
    'insufficient_context': 'The retrieved sources do not provide enough information to answer fully.',
    'invalid_evidence': 'The generated answer failed source citation checks. Please try again or explore the retrieved sources.',
    'quote_not_in_source': 'The generated answer included a quote that could not be verified. Please try again or explore the retrieved sources.',
    'high_stakes': 'ParkGuide AI cannot assess emergencies or provide personal medical advice. Contact emergency services for urgent danger, or a park ranger for on-site guidance.',
    'api_key_missing': 'Cited answers need a locally configured API key. You can explore sources in preview mode.',
    'ambiguous_followup': 'Select the earlier question under “Follow-up to”, or name the park and topic in your question.',
}


def index_version(preview, root=ROOT):
    path = root / 'data/index' / ('development_preview' if preview else 'approved') / 'active.json'
    return json.loads(path.read_text())['version'] if path.exists() else None


def safe_source_url(url):
    from urllib.parse import urlsplit
    parsed = urlsplit(url)
    return (parsed.scheme == 'https' and parsed.netloc == 'www.nps.gov'
            and not any(c in url for c in ['(', ')', '\n', '\r', ' ']))


def request_query(question, previous=None):
    question = question.strip()
    if not question or len(question) > 2000:
        raise ValueError('Enter a question of 1–2,000 characters.')
    return f'Earlier question: {previous}\nFollow-up question: {question}' if previous else question


def referral_needed(question):
    # Deliberately narrow pattern checks; this is not a medical classifier.
    return bool(re.search(r'\b(emergency|injured|injury|chest pain|can[’\x27]?t breathe|cannot breathe|'
                          r'bleeding|unconscious|medical advice|medication|lost right now)\b', question, re.I))


def compact_trace(result):
    trace = {k: v for k, v in result.items() if k not in ['candidates', 'contexts', 'reranked_candidates']}
    trace['rankings'] = [{
        'chunk_id': c['chunk_id'], 'title': c['chunk']['title'], 'url': c['chunk']['url'],
        'pre_rank': c['pre_rank'], 'post_rank': c['post_rank'],
        **c['scores'], 'reranker_score': c['reranker_score'],
        'selected': c['post_rank'] <= len(result['contexts'])
    } for c in result['reranked_candidates']]
    return trace


class ChatEngine:
    def __init__(self, preview=True):
        from retrieval import Embedder, IndexStore
        from answering import Reranker
        config = json.loads((ROOT / 'config/retrieval.json').read_text())
        self.answering = json.loads((ROOT / 'config/answering.json').read_text())
        self.embedder = Embedder(config)
        self.store = IndexStore(config, development_preview=preview)
        self.ranker = Reranker(self.answering)
        self.retriever = self.store.retriever('heading_aware', self.embedder)
        self.lock = threading.Lock()

    def respond(self, question, park=None, page_type=None, as_of=None, previous=None, cited=False):
        from answering import generate, QuotaReached
        query = request_query(question, previous)
        if referral_needed(query):
            return {'status': 'refused', 'reason': 'high_stakes', 'message': REASONS['high_stakes'], 'sources': [], 'trace': None}
        if not previous and re.search(r'\b(it|there|those|that one|what about)\b', question, re.I):
            return {'status': 'refused', 'reason': 'ambiguous_followup', 'message': REASONS['ambiguous_followup'], 'sources': [], 'trace': None}
        start = time.perf_counter()
        with self.lock:
            result = self.ranker.rerank(self.retriever.search(query, park=park, page_type=page_type, as_of=as_of))
            if cited:
                # Cached engines must use the current provider and credential variable.
                self.answering = generation_config()
                if result['index_mode'] == 'approved' and not api_key(self.answering):
                    answer = {'status': 'refused', 'reason': 'api_key_missing', 'answer': REASONS['api_key_missing']}
                else:
                    try:
                        answer = generate(result, self.answering)
                    except QuotaReached:
                        answer = {'status': 'error', 'reason': 'quota_reached', 'answer': QUOTA_MESSAGE}
            else:
                answer = {'status': 'preview' if result['contexts'] else 'refused',
                          'reason': None if result['contexts'] else 'empty_context',
                          'answer': 'Here are the closest source passages. These are an evidence preview; a generated answer is not available in this mode.'}
        sources = [{'chunk_id': c['chunk_id'], 'title': c['chunk']['title'], 'url': c['chunk']['url'],
                    'excerpt': ' '.join(c['chunk']['text'].split()[:25]),
                    'pages': c['chunk'].get('page_numbers', []), 'coverage_period': c['chunk'].get('coverage_period'),
                    'source_last_updated': c['chunk'].get('source_last_updated')} for c in result['contexts']]
        trace = compact_trace(result)
        trace['generation'] = {k: v for k, v in answer.items() if k not in ['answer', 'claims']}
        trace['processing_seconds'] = time.perf_counter() - start
        trace['original_question'] = question
        trace['followup_context'] = previous
        reason = answer['reason']
        return {'status': answer['status'], 'reason': reason,
                'message': REASONS.get(reason, answer['answer']), 'sources': sources, 'trace': trace,
                'filters': result['filters']}


def friendly_error(error):
    """Do not display raw network errors, filesystem paths, or request headers."""
    from answering import QuotaReached
    if isinstance(error, QuotaReached) or isinstance(error, HTTPError) and error.code == 429:
        return QUOTA_MESSAGE
    if isinstance(error, HTTPError) and error.code in (401, 403):
        return 'The API key or project is not authorized. Check your Gemini key and model access in Google AI Studio.'
    if isinstance(error, HTTPError):
        return 'The answer service could not complete the request. Check API access and try again.'
    if isinstance(error, (URLError, TimeoutError)):
        return 'The answer service could not be reached. Your question is still in this conversation; try again.'
    if isinstance(error, ValueError) and ('token' in str(error).lower() or 'query' in str(error).lower()):
        return 'This question is too long for the local search model. Shorten it or start a new question.'
    return 'The local search could not finish. Check the model downloads and active index, then try again.'
