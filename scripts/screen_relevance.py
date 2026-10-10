"""Offline relevance triage. Recommendations never constitute human corpus approval."""
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
# Conservative exceptions identified by inspecting saved content, not just URL location.
EXCLUDE = {
 ('grca','rss-feed-info.htm'): 'Generic RSS/browser help rather than park visitor planning.',
 ('grca','virtual-grand-canyon-star-party-2020.htm'): 'Archived virtual astronomy lectures, outside the on-site visitor-planning scope.',
 ('grca','virtual-grand-canyon-star-party-2021.htm'): 'Archived virtual astronomy lectures, outside the on-site visitor-planning scope.',
 ('yell','winterusenews.htm'): 'Historical winter-use planning newsletters/meeting archives rather than visitor instructions.',
 ('yell','if-you-cant-come-to-yellowstone.htm'): 'Alternative destinations across other NPS sites; outside the three selected parks.',
 ('yell','suppliers-travel-hazardous-permits.htm'): 'Commercial transport, supplier and hazardous-material permits rather than recreational visitor planning.',
 ('yose','ymc-privacy.htm'): 'Clinic health-information privacy policy, outside visitor planning. This public policy is not itself a patient record.',
 ('yose','hantaletter.htm'): 'Historical 2012 disease notification letter; outside the scoped visitor-planning knowledge base.',
 ('yose','hantafaq.htm'): 'Disease symptoms, epidemiology and clinical information; outside the travel-planning scope.',
 ('yose','hantavirus.htm'): 'Disease-focused public-health information; outside the travel-planning scope.',
 ('yose','plague.htm'): 'Disease-focused clinical/public-health information; outside the travel-planning scope.',
 ('yose','plaguefaq.htm'): 'Disease/outbreak FAQ; outside the travel-planning scope.',
}
CLOSER = {
 ('grca','backcountry-cua-regs.htm'): 'Specialist commercial guide requirements; decide whether operator-facing material belongs in scope.',
 ('grca','cultural-demonstration-celebration.htm'): 'Past May 2025 event mixed with continuing program background; consider retaining only enduring visitor information.',
 ('grca','grand-canyon-star-party.htm'): 'June 2026 event has ended; relevant activity, but needs historical/current-use decision.',
 ('grca','indigenous-people-s-day.htm'): 'Specific dated event schedule; verify year and whether historical events are in scope.',
 ('grca','native-american-heritage-month.htm'): 'November 2024 event archive; decide whether to retain historical programming.',
 ('grca','north-kaibab-trail-reopening-update-june-16-2023.htm'): '2023 reopening update; relevant trail topic, but should not imply current access.',
 ('grca','havasupai.htm'): 'Explicitly outside national park jurisdiction; useful nearby-trip clarification if scope allows it.',
 ('grca','skywalk.htm'): 'Tribal destination outside the national park; useful boundary clarification if scope allows nearby attractions.',
 ('grca','nearbyattractions.htm'): 'Nearby-trip logistics mixed with distant parks; decide boundaries or retain only relevant sections.',
 ('yell','nearbyattractions.htm'): 'Nearby attractions outside Yellowstone; decide geographic boundaries.',
 ('yose','gateways.htm'): 'Gateway communities outside Yosemite; potentially useful access/lodging logistics.',
 ('grca','medical_services.htm'): 'Visitor medical-service locations/hours mixed with healthcare descriptions; consider logistics/referrals only.',
 ('yose','ymc.htm'): 'Visitor clinic logistics mixed with resident/employee healthcare and staff references; consider logistics/referrals only.',
 ('yose','health.htm'): 'General visitor health precautions overlap medical guidance; determine what public travel warnings to retain.',
 ('grca','religious-services.htm'): 'Visitor service information mixed with named local contacts; decide scope, with privacy checked separately.',
 ('grca','river_support.htm'): 'River-trip logistics mixed with business contact listings; determine whether directory sections should remain.',
 ('grca','the-history-behind-southwestern-arts-video-series.htm'): 'Educational video content with program context; decide whether it supports planning or only cultural background.',
 ('yell','expeditionyell.htm'): 'School-group residential education program; retain only if educational/group visits are within scope.',
 ('yell','fee-dollars-at-work.htm'): 'Fee-funded project background, rather than entrance price/pass instructions.',
 ('yell','parkfacts.htm'): 'General park background/statistics; optional supporting domain context rather than direct trip logistics.',
 ('yell','safety-press.htm'): 'Journalist-facing kit includes visitor safety information; consider useful sections and duplication.',
 ('yell','flood-recovery.htm'): 'Historical flood response mixed with operations; distinguish enduring travel information from recovery history.',
 ('yose','haleysblog.htm'): 'Ranger patrol narrative and date-specific trail observations; prefer current visitor-facing trail sources.',
 ('yose','rimfire-directions.htm'): 'Incident-specific alternate routes; decide historical inclusion and avoid current-route assumptions.',
 ('yose','lpr.htm'): 'Traffic-system/camera policy background; optional context rather than direct route/parking guidance.',
 ('yose','osos.htm'): 'Spanish food-storage guidance; relevant topic but English embedding/BM25 support and translation policy need a decision.',
 ('yose','zipcode.htm'): 'Resident non-recreational pass-through rules; outside typical tourist questions but potentially useful fee exception.',
 ('yell','first-amendment.htm'): 'Specialist expressive-event permitting; decide whether this is within visitor scope.',
 ('yell','ashscattering.htm'): 'Special-purpose memorial permits; decide whether the permit scope includes these.',
 ('yose','ashes.htm'): 'Special-purpose memorial permits; decide whether the permit scope includes these.',
}
TOPICS = {
 'camping and lodging': r'camp|lodg|sleep|reserv|backpack|canister|food storage|food locker|snow creek cabin',
 'fees and permits': r'fee|pass(?:es)?\b|permit|lotter|waiver|regulat|weapons|firearms|organized non-commercial|river launch',
 'transportation and access': r'transport|shuttle|road|driv|directions|parking|airport|air travel|train|vehicle|traffic|tire chain|traveling green',
 'visitor services and accessibility': r'accessib|deaf|services|dining|food options|restaurant|grocer|visitor center|information station|visitor education|lost.*found|bookstore|gift shops|market plaza|internet|cell service|refill|water bottle',
 'seasonal conditions and safety': r'season|winter|spring|summer|fall\b|weather|condition|closure|water restriction|alerts|safety|lightning|fire restriction|bear|wildlife|water safety|drinking water|hike smart',
 'activities and destinations': r'fitness|hik|trail|bik|fish|boat|river trip|rafting|mule|horse|stock use|tour|picnic|climb|stargaz|night sky|geys|hot springs|ranger program|things to do|places to go|art.*photog|art.*exhibit|historic|museum|cultural|viewpoint|waterfall|rock formations|sequoia|grove|meadow|lake|canyon village|desert view|hermit|tuweep|wawona|yosemite valley|glacier point|hetch hetchy|tuolumne|old faithful|norris|tower|mammoth|frazil|horsetail|birdwatch|wedding|ceremon|program|shrine',
 'general visit planning': r'plan your visit|basic information|brochure|guide|map|nps app|mobile app|park brochure|things to know|tips for|yellowstone pledge|visitation|leave no trace|pet|swimming|water activities',
}


def main():
 docs=[json.loads(line) for line in (ROOT/'data/normalized/documents.jsonl').read_text().splitlines()]
 human_path=ROOT/'data/review/relevance_queue.json'
 human=json.loads(human_path.read_text()) if human_path.exists() else {'decisions':[]}
 by_id={r['doc_id']:r for r in human['decisions']}
 records=[]
 for number,d in enumerate(docs,1):
  parsed=urlsplit(d['url']);key=(d['park'],Path(parsed.path).name)
  text=' '.join(b.get('text','') for b in d['blocks'])
  headings=' '.join(' '.join(b.get('heading_path',[])) for b in d['blocks'])
  identity=d['title']+' '+parsed.path+' '+headings
  topics=[label for label,pattern in TOPICS.items() if re.search(pattern,identity,re.I)]
  evidence_counts={label:len(re.findall(pattern,text,re.I)) for label,pattern in TOPICS.items()}
  sha=hashlib.sha256(json.dumps(d,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
  h=by_id.get(d['doc_id'],{})
  confirmed=h.get('human_decision') in ('include','exclude','needs_review') and h.get('normalized_document_sha256')==sha
  if parsed.netloc!='www.nps.gov' or d['park'] not in ('yose','yell','grca') or not parsed.path.startswith('/'+d['park']+'/'):
   bucket='closer_review';reason='Park/site identity mismatch requires manual verification.'
  elif key in EXCLUDE:
   bucket='likely_exclusion';reason=EXCLUDE[key]
  elif key in CLOSER:
   bucket='closer_review';reason=CLOSER[key]
  elif topics and any(evidence_counts[t] for t in topics):
   bucket='clear_match';reason='Title/headings and saved text support visitor-planning topics: '+', '.join(topics)+'.'
  else:
   bucket='closer_review';reason='No sufficiently explicit visitor-planning topic found by the conservative screen.'
  if confirmed:
   bucket=('user_batch_'+h['human_decision'] if h.get('acceptance_method')=='scope_batch_keep_all' else 'human_confirmed_'+h['human_decision']);reason=h.get('decision_note','Previously recorded human decision.')
  flags=[]
  if d['page_type']=='pdf': flags.append('PDF reading order/maps/tables need separate review')
  if re.search(r'\b20(?:0\d|1\d|2[0-5])\b',text): flags.append('Historical year mentioned; separate temporal review required (not proof the whole page is outdated)')
  if any(t in topics for t in ['seasonal conditions and safety','fees and permits']): flags.append('Changing or safety/policy information; separate validity review required')
  records.append({'number':number,'doc_id':d['doc_id'],'normalized_document_sha256':sha,'title':d['title'],'url':d['url'],'park':d['park'],'page_type':d['page_type'],'bucket':bucket,'reason':reason,'topic_matches':topics,'body_topic_match_counts':evidence_counts,'excerpt':' '.join(text.split()[:90]),'other_review_flags':flags,'human_decision':h.get('human_decision','pending') if confirmed else 'pending','recommendation_only':not confirmed})
 assert len(records)==len(docs) and len({r['doc_id'] for r in records})==len(docs)
 counts={b:sum(r['bucket']==b for r in records) for b in sorted({r['bucket'] for r in records})}
 report={'screening_date':'2026-10-10','scope':'Yosemite, Yellowstone and Grand Canyon visitor planning: camping, fees, permits, access, facilities, accessibility, activities and seasonal information. Nearby destinations and specialist uses require explicit scope decisions.','method':'Offline rule-based title/heading/full-text signal screening plus inspected exceptions. This is AI-assisted triage, not human review of all source content.','corpus_documents':len(records),'counts':counts,'source_sha256':hashlib.sha256((ROOT/'data/normalized/documents.jsonl').read_bytes()).hexdigest(),'approval_effect':'None. Human decisions remain in relevance_queue.json. Normalized data, chunks, review flags and indexes are unchanged.','records':records}
 out=ROOT/'data/review';out.mkdir(exist_ok=True)
 (out/'relevance_screening.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
 lines=['# Relevance screening — recommendations, not corpus approval','',f"{sum(r['human_decision']!='pending' for r in records)} user relevance decisions are recorded, including batch scope acceptance. These do not establish individual inspection or other review approvals.",'','## Scope','',report['scope'],'','## Results','']
 for bucket,count in counts.items(): lines.append(f'- {bucket.replace("_"," ")}: **{count}**')
 lines+=['','## Interpretation','','Clear matches have visitor-planning signals in titles/headings and saved text. They still need human relevance acceptance. Likely exclusions are recommendations only. A closer-review source may be kept wholly, kept with restricted sections, or excluded. Historical references do not automatically make a source irrelevant.','','Privacy, dates, PDF extraction, table alignment and final indexing approval remain separate and pending. No source content or active index was modified.','','## Review files','','- [Sources needing closer review](relevance_closer_review.md)','- [Likely exclusions](relevance_likely_exclusions.md)','- [Clear-match inventory](relevance_clear_matches.md)','','## Screening method','',report['method']]
 (out/'relevance_screening_summary.md').write_text('\n'.join(lines)+'\n')
 for bucket,filename in [('closer_review','relevance_closer_review.md'),('likely_exclusion','relevance_likely_exclusions.md'),('clear_match','relevance_clear_matches.md')]:
  group=[r for r in records if r['bucket']==bucket]
  lines=[f'# {bucket.replace("_"," ").title()} — {len(group)} sources','','AI recommendations only; no human approval has been inferred.','']
  for r in group:
   lines += [f"## {r['number']}. {r['title']} ({r['park']})",'',f"Source: {r['url']}",'',r['reason'],'']
   if bucket!='clear_match': lines += ['Saved-content excerpt (not a current-information assertion):','',r['excerpt'],'']
  (out/filename).write_text('\n'.join(lines)+'\n')
 print(json.dumps({'counts':counts,'total':len(records),'pending_relevance':sum(r['human_decision']=='pending' for r in records)},indent=2))
 print('Additional closer-review items:',[(r['number'],r['title']) for r in records if r['bucket']=='closer_review' and (r['park'],Path(urlsplit(r['url']).path).name) not in CLOSER])


if __name__=='__main__':
 main()
