"""Offline privacy triage of normalized source text. Never auto-approves a document."""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
 'email_candidate': r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b',
 'formatted_phone_candidate': r'(?<!\d)(?:\+?1[ /.-]?)?(?:\(\d{3}\)[ /.-]?|\d{3}[ /.-])\d{3}[ /.-]\d{4}(?!\d)',
 'unseparated_phone_candidate': r'(?<![\w])(?:1)?[2-9]\d{2}[2-9]\d{6}(?![\w])',
 'ssn_candidate': r'\b\d{3}-\d{2}-\d{4}\b',
 'identifier_context': r'\b(?:social security|date of birth|patient id|medical record number|account number|credit card number)\b',
 'individual_contact_role': r'\b(?:branch president|contact person|privacy official|privacy officer)\b',
 'postal_address_candidate': r'\b(?:P\.?O\.? Box\s+\d+|\d{1,5}\s+[A-Za-z0-9 .]{1,55}\b(?:Street|St|Road|Rd|Drive|Dr|Avenue|Ave|Boulevard|Blvd|Lane|Ln|Way)\b)',
 'medical_context': r'\b(?:patient|patients|clinic|medical|health information|hantavirus|plague)\b',
}
PRIORITY_FILES = {'medical_services.htm','religious-services.htm','river_support.htm','ymc-privacy.htm','ymc.htm'}


def main():
 source=ROOT/'data/normalized/documents.jsonl'
 docs=list(map(json.loads,source.read_text().splitlines()))
 records=[]
 for n,d in enumerate(docs,1):
  # Examine text and table representations; identifiers in metadata headings are included.
  values=[d['title'],d.get('text','')]
  for b in d['blocks']:values += [b.get('text','')]+b.get('heading_path',[])
  for t in d.get('tables',[]):
   values += [t.get('caption','')]+t.get('heading_path',[])+[str(c) for row in t.get('rows',[]) for c in row]
  text='\n'.join(dict.fromkeys(values))
  counts={label:len(re.findall(pattern,text,re.I)) for label,pattern in PATTERNS.items()}
  strong=any(counts[k] for k in ['email_candidate','formatted_phone_candidate','unseparated_phone_candidate','ssn_candidate'])
  filename=d['url'].rsplit('/',1)[-1]
  priority='identifier_candidate' if strong else ('priority_manual_review' if filename in PRIORITY_FILES or counts['individual_contact_role'] or counts['identifier_context'] else ('context_review' if counts['medical_context'] or counts['postal_address_candidate'] else 'routine_manual_review'))
  records.append({'number':n,'doc_id':d['doc_id'],'title':d['title'],'url':d['url'],'park':d['park'],
                  'normalized_document_sha256':hashlib.sha256(json.dumps(d,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
                  'signals':counts,'priority':priority,'existing_redaction_occurrences':d.get('redaction_occurrences',{}),
                  'privacy_decision':'pending','note':'Pattern signals are candidates, not a finding of personal information or patient records. No-pattern results do not establish privacy approval.'})
 assert len(records)==len(docs) and len({r['doc_id'] for r in records})==len(docs)
 totals={k:sum(r['signals'][k]>0 for r in records) for k in PATTERNS}
 summary={'date':'2026-10-10','documents':len(records),'corpus_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
          'method':'Offline pattern screening of titles, normalized text, blocks, headings and tables. Counts can duplicate data across representations. Person-name detection is not comprehensive.',
          'documents_with_signals':totals,'priorities':{p:sum(r['priority']==p for r in records) for p in sorted({r['priority'] for r in records})},
          'approval_effect':'None. Every privacy decision remains pending; source corpus and indexes are unchanged.',
          'records':records}
 out=ROOT/'data/review';out.mkdir(exist_ok=True)
 (out/'privacy_screening.json').write_text(json.dumps(summary,indent=2)+'\n')
 lines=['# Privacy screening — pending human review','','## Findings','']
 for k,v in totals.items():lines.append(f'- {k.replace("_"," ")}: {v} documents flagged')
 lines += ['','## Interpretation','','These are screening signals, not proof of PII/PHI. Clinic descriptions and a generic privacy notice do not by themselves contain patient records. Business/facility addresses and public author credits should be distinguished from personal contacts. Emergency short codes are not treated as personal phone numbers.','','Existing email/phone/SSN redactions remain in place. Named contacts and mixed directories need contextual review. No document is privacy-approved by this scan. Names, unusual identifiers and information visible only in original PDFs may be missed.','','## Identifier candidate documents','']
 for r in records:
  if r['priority']=='identifier_candidate':lines.append(f"- {r['number']}. {r['title']}: {r['url']}")
 lines+=['','## First manual batch','','[Medical and contact-page review packet](privacy_batch_01_medical_contacts.md)']
 (out/'privacy_screening_summary.md').write_text('\n'.join(lines)+'\n')
 selected=[(d,r) for d,r in zip(docs,records) if d['url'].rsplit('/',1)[-1] in PRIORITY_FILES]
 assert len(selected)==5
 lines=['# Privacy review — batch 1: medical and contact pages','','Five documents pending human privacy review. Source content is unchanged. Relevance decisions are not privacy decisions.','','## Proposed handling','','- Retain public institutional services, facility/business addresses, organization names, generic health information and generic privacy policies if no individual patient information is present.','- Keep email and phone redactions in place.','- Redact named individual contacts in directory contexts, including names connected to religious roles or supplier contact listings. Preserve public author/photo credits unless they reveal sensitive personal information.','- Continue to block staff/patient directories and any actual personal medical records.','- Changes require re-chunking and index refresh before the reviewed corpus is used.','','This proposal does not certify date validity or personalized medical-answer suitability.','']
 for d,r in selected:
  lines += [f"## {r['number']}. {r['title']}",'',d['url'],'','Screening signals: '+json.dumps({k:v for k,v in r['signals'].items() if v}),'','### Saved content','']
  for b in d['blocks']:lines += [('#### ' if b['type']=='heading' else '')+b.get('text',''),'']
 (out/'privacy_batch_01_medical_contacts.md').write_text('\n'.join(lines)+'\n')
 print(json.dumps({k:v for k,v in summary.items() if k not in ['records','corpus_sha256']},indent=2))


if __name__=='__main__':
 main()
