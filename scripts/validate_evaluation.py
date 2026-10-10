"""Validate the offline evaluation draft; reject unreviewed gold when requested."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUOTAS = {
    "single_hop": (9, 6), "multi_hop": (4, 2),
    "comparative": (2, 2), "temporal": (2, 2),
    "unanswerable": (5, 3), "ambiguous_adversarial": (2, 1),
}


def validate(items, documents, corpus_sha256, manifest, require_verified=False):
    errors = []
    docs = {d["doc_id"]: d for d in documents}
    counts = Counter((x["category"], x["split"]) for x in items)
    if len(items) != 40 or len({x["id"] for x in items}) != 40:
        errors.append("Exactly 40 unique item IDs are required.")
    for category, (dev, test) in QUOTAS.items():
        if (counts[category, "dev"], counts[category, "test"]) != (dev, test):
            errors.append(f"Category quotas do not match: {category}")
    if any(c not in QUOTAS or s not in {"dev", "test"} for c, s in counts):
        errors.append("Unknown category or split.")
    families, units = {}, {}
    for item in items:
        prefix = item["id"]
        if item["corpus_sha256"] != corpus_sha256:
            errors.append(f"{prefix}: corpus fingerprint changed.")
        family = item["family_id"]
        if families.setdefault(family, item["split"]) != item["split"]:
            errors.append(f"{prefix}: family crosses splits.")
        if not item["question"].strip() or not item["required_facts"]:
            errors.append(f"{prefix}: missing question or required facts.")
        evidence = item["evidence"]
        if item["expected_behavior"] in {"answer", "answer_ignore_injection"} and not evidence:
            errors.append(f"{prefix}: answer has no evidence.")
        if item["category"] == "multi_hop" and len({e["doc_id"] for e in evidence}) < 2:
            errors.append(f"{prefix}: multi-hop needs two distinct pages.")
        if item["category"] == "unanswerable" and not item["absence_searches"]:
            errors.append(f"{prefix}: missing absence search leads.")
        for e in evidence:
            if units.setdefault(e["unit_id"], item["split"]) != item["split"]:
                errors.append(f"{prefix}: shared fact crosses splits.")
            d = docs.get(e["doc_id"])
            if not d or d["content_sha256"] != e["content_sha256"] or d["url"] != e["url"]:
                errors.append(f"{prefix}: source identity mismatch.")
                continue
            if not 0 <= e["block_index"] < len(d["blocks"]):
                errors.append(f"{prefix}: invalid source block.")
                continue
            b = d["blocks"][e["block_index"]]
            quote = e["quote"]
            start = e["block_char_start"]
            if not quote or len(quote.split()) > 25 or b["text"][start:start + len(quote)] != quote:
                errors.append(f"{prefix}: quote is not exact or exceeds 25 words.")
            if e["page_number"] != b.get("page_number"):
                errors.append(f"{prefix}: PDF page mismatch.")
            if b["type"] == "table":
                t = e.get("table_index", -1)
                if not 0 <= t < len(d["tables"]) or quote not in "\n".join(" | ".join(r) for r in d["tables"][t]["rows"]):
                    errors.append(f"{prefix}: table provenance mismatch.")
        review = item["review"]
        if require_verified and (review["status"] != "approved" or not review.get("reviewer") or not review.get("reviewed_at")):
            errors.append(f"{prefix}: human verification is pending.")
    if manifest["corpus_sha256"] != corpus_sha256 or manifest["seed"] != 633:
        errors.append("Split manifest fingerprint or seed mismatch.")
    for split in ("dev", "test"):
        if sorted(manifest[split]) != sorted(x["id"] for x in items if x["split"] == split):
            errors.append(f"Split manifest differs from items: {split}")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-verified", action="store_true")
    args = parser.parse_args()
    data = ROOT / "data/evaluation"
    raw = (ROOT / "data/normalized/documents.jsonl").read_bytes()
    documents = [json.loads(line) for line in raw.decode().splitlines()]
    items = [json.loads(line) for line in (data / "golden_set.draft.jsonl").read_text().splitlines()]
    manifest = json.loads((data / "split_draft.json").read_text())
    errors = validate(items, documents, hashlib.sha256(raw).hexdigest(), manifest, args.require_verified)
    result = {"valid": not errors, "require_verified": args.require_verified,
              "items": len(items), "human_approved": sum(x["review"]["status"] == "approved" for x in items),
              "dev": sum(x["split"] == "dev" for x in items),
              "test": sum(x["split"] == "test" for x in items), "errors": errors}
    print(json.dumps(result, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
