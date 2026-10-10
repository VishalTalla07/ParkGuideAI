"""ParkGuide AI — run with: .venv/bin/python -m streamlit run app.py"""
import json
import os
from pathlib import Path
import sys

import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'scripts'))
from chat_service import ChatEngine, PARKS, friendly_error, index_version, safe_source_url
from generation_settings import generation_config, api_key, provider_name

st.set_page_config(page_title='ParkGuide AI', page_icon='🌲', layout='wide')


@st.cache_resource(show_spinner=False)
def load_engine(preview, version, generation_signature):
    # Version participates in the cache key so a migrated index is loaded anew.
    return ChatEngine(preview=preview)


def clear_chat():
    st.session_state.messages = []
    st.session_state.followup = 'New question'


def render_response(response, number):
    status = response['status']
    if status == 'refused':
        st.warning('Unable to answer · ' + response['message'])
    elif status == 'error':
        st.error(response['message'])
    elif status == 'answered':
        st.markdown(response['message'])
    else:
        st.info(response['message'])
    if response.get('filters'):
        filters = response['filters']
        labels = [f"Park: {filters['park'] or 'all three'}", f"Sources: {filters['page_type'] or 'HTML + PDF'}"]
        if filters.get('as_of'):
            labels.append('Known coverage: ' + filters['as_of'])
        st.caption(' · '.join(labels))
    sources = response.get('sources', [])
    if sources:
        with st.expander(f'Sources · {len(sources)} selected passages', expanded=status == 'preview'):
            for i, source in enumerate(sources, 1):
                st.write(f"{i}. {source['title']}")
                st.text(source['excerpt'] + ' …')
                if safe_source_url(source['url']):
                    st.link_button('Open NPS source', source['url'], key=f'source_{number}_{i}')
                if source.get('pages'):
                    st.caption('PDF pages: ' + ', '.join(str(p) for p in source['pages']))
                if source.get('source_last_updated'):
                    st.caption('Source last updated: ' + str(source['source_last_updated']) + ' · This does not guarantee current validity.')
    trace = response.get('trace')
    if trace:
        with st.expander('How these sources were selected'):
            st.caption('Search → 20 candidates at most → reranking → 5 passages at most. Scores indicate ranking, not confidence.')
            st.write(f"Retrieved: {trace['candidate_count']} · Eligible: {trace['eligible_chunks']} · Pool shortfall: {'yes' if trace['candidate_pool_shortfall'] else 'no'}")
            timings = st.columns(3)
            timings[0].metric('Retrieval', f"{trace['latency_seconds'] * 1000:.0f} ms")
            timings[1].metric('Reranking', f"{trace['reranking_latency_seconds'] * 1000:.0f} ms")
            gen = trace.get('generation', {})
            timings[2].metric('Generation', f"{gen['generation_latency_seconds'] * 1000:.0f} ms" if 'generation_latency_seconds' in gen else 'Not run')
            columns = ['title', 'pre_rank', 'post_rank', 'reranker_score',
                       'dense_score', 'bm25_score', 'rrf_score', 'selected']
            st.dataframe([{k: row.get(k) for k in columns} for row in trace['rankings']],
                         hide_index=True, width='stretch')
            st.caption(trace['temporal_note'])
            if trace.get('warning'):
                st.caption(trace['warning'])
            with st.expander('Full trace and model usage'):
                st.json(trace)
            st.download_button('Download this trace', json.dumps(trace, indent=2),
                               file_name=f'parkguide_trace_{number}.json', mime='application/json', key=f'trace_{number}')


st.markdown('''<style>
.block-container {max-width:1100px;padding-top:2.8rem;}
h1 {letter-spacing:-1.5px;font-weight:750;}
[data-testid="stChatMessage"] {border:1px solid #DFE5D9;border-radius:16px;padding:1.1rem;}
[data-testid="stSidebar"] {border-right:1px solid #DFE5D9;}
.eyebrow {color:#61735D;letter-spacing:.15em;font-size:.75rem;font-weight:700;}
.hero {background:#EAF0E3;border-radius:20px;padding:24px 28px;margin-bottom:20px;}
.hero p {color:#48604C;margin-bottom:0;line-height:1.7;}
</style>''', unsafe_allow_html=True)

if 'messages' not in st.session_state:
    st.session_state.messages = []

with st.sidebar:
    st.title('🌲 ParkGuide AI')
    st.caption('Your national park planning companion')
    st.divider()
    park_label = st.selectbox('Choose a park', list(PARKS), key='park')
    source_label = st.selectbox('Source type', ['HTML + PDF', 'HTML pages', 'PDF guides'], key='source_type')
    dated = st.checkbox('Filter by known coverage date', key='dated')
    as_of = st.date_input('Coverage date', key='coverage_date').isoformat() if dated else None
    if dated:
        st.caption('Sources with unknown coverage dates are excluded. This is not a live conditions check.')
    approved_version = index_version(False)
    approved_exists = approved_version is not None
    if approved_exists and st.session_state.get('seen_approved_version') is None:
        st.session_state.reviewed = True
    st.session_state.seen_approved_version = approved_version
    reviewed = st.checkbox('Use reviewed corpus', disabled=not approved_exists, key='reviewed')
    preview = not (reviewed and approved_exists)
    settings = generation_config()
    key_available = bool(api_key(settings))
    answer_ready = not preview and key_available
    # Switch once when generation becomes available, including existing preview sessions.
    # Preserve an explicit preview selection on later reruns while readiness is unchanged.
    if answer_ready and not st.session_state.get('seen_answer_ready', False):
        st.session_state.response_mode = 'Cited answer'
    st.session_state.seen_answer_ready = answer_ready
    mode = st.radio('Response mode', ['Source preview', 'Cited answer'], key='response_mode')
    st.caption(provider_name(settings) + ' key: ' + ('available locally' if key_available else 'not set'))
    if mode == 'Cited answer':
        if preview:
            st.info('Cited answers need a reviewed corpus. Source preview is available now.')
        elif not api_key(generation_config()):
            st.info('Add GEMINI_API_KEY to the project .env file to enable cited answers.')
        else:
            st.caption('Your question, selected earlier question, and up to five source passages will be sent to ' + provider_name(generation_config()) + ' for this answer.')
            if generation_config().get('generation_provider') == 'gemini':
                st.caption('Google’s free tier may use submitted content to improve its products. Avoid personal or medical details.')
    st.divider()
    earlier = [m['content'] for m in st.session_state.messages if m['role'] == 'user']
    choices = ['New question'] + [f'{i + 1}. {q[:70]}' for i, q in enumerate(earlier)]
    if st.session_state.get('followup') not in choices:
        st.session_state.followup = 'New question'
    followup = st.selectbox('Follow-up to', choices, key='followup')
    st.caption('Select an earlier question for a follow-up. Earlier answers are never used as source evidence.')
    st.button('Clear conversation', on_click=clear_chat, width='stretch')
    st.caption('History stays in this browser session and is not written to project files. Avoid sharing personal or medical details.')
    with st.expander('About this project'):
        st.write('Yosemite · Yellowstone · Grand Canyon')
        st.write('Public National Park Service sources. This student project is not affiliated with the NPS.')
        st.link_button('Project repository', 'https://github.com/VishalTalla07/ParkGuideAI')

st.markdown('<div class="eyebrow">EXPLORE WITH SOURCE EVIDENCE</div>', unsafe_allow_html=True)
st.title('A little guidance. A bigger adventure.')
st.markdown('''<div class="hero"><h3>Plan your next park visit</h3><p>Explore camping, park fees, permits, accessibility, and visitor guides for Yosemite, Yellowstone, and Grand Canyon.</p></div>''', unsafe_allow_html=True)
if preview:
    st.caption('LOCAL SOURCE PREVIEW · Corpus review pending · Saved sources may be outdated')
else:
    st.caption('REVIEWED CORPUS · Confirm time-sensitive plans with the park')

pending = None
if not st.session_state.messages:
    st.write('Start with a question')
    columns = st.columns(3)
    for column, label, query in zip(columns, ['Camping reservations', 'Entrance fees', 'Backcountry permits'],
            ['How do I reserve a campground in Yosemite?', 'What are the entrance fees for Yellowstone?',
             'Do I need a backcountry permit for overnight camping at Grand Canyon?']):
        if column.button(label, width='stretch'):
            pending = query
    st.caption('Example questions use your selected filters. Choose “All three parks” to explore freely.')

for i, message in enumerate(st.session_state.messages):
    with st.chat_message(message['role']):
        if message['role'] == 'user':
            st.text(message['content'])
        else:
            render_response(message['response'], i)

prompt = st.chat_input('Ask about camping, fees, permits, or accessibility…', max_chars=2000)
question = prompt or pending
if question:
    previous = earlier[choices.index(followup) - 1] if followup != 'New question' else None
    st.session_state.messages.append({'role': 'user', 'content': question})
    with st.chat_message('user'):
        st.text(question)
    with st.chat_message('assistant'):
        try:
            version = index_version(preview)
            if version is None:
                raise FileNotFoundError('Build the selected index first')
            with st.spinner('Finding relevant NPS sources…'):
                engine = load_engine(preview, version, json.dumps(generation_config(), sort_keys=True))
                response = engine.respond(question, park=PARKS[park_label],
                                          page_type={'HTML + PDF': None, 'HTML pages': 'html', 'PDF guides': 'pdf'}[source_label],
                                          as_of=as_of, previous=previous, cited=mode == 'Cited answer')
        except Exception as error:
            response = {'status': 'error', 'message': friendly_error(error), 'sources': [], 'trace': None}
        number = len(st.session_state.messages)
        render_response(response, number)
        st.session_state.messages.append({'role': 'assistant', 'response': response})
    st.rerun()
