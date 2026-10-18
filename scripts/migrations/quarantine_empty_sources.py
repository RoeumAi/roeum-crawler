"""Quarantine audited empty sources without altering content, IDs or embeddings.

Default: read-only plan. Supply an audit manifest containing collection/doc_id pairs.
--apply requires --backup; deploy the scheduler exclusion fix BEFORE applying.
Usage: PYTHONPATH=. python scripts/migrations/quarantine_empty_sources.py manifest.json
"""
import argparse
import json
import os
from pathlib import Path

from bson import json_util
from pymongo import MongoClient
from scripts.utils.source_quality import has_legal_body


def plan_quarantine(db, manifest):
    plans = []
    for collection, doc_id in sorted({(item['collection'], item['doc_id']) for item in manifest}):
        rows = list(db[collection].find({'doc_id': doc_id, 'metadata.is_active': {'$ne': False}}, {'embedding': 0}))
        # Never quarantine a document with any real section, or one already repaired.
        if not rows or any(has_legal_body({"title": row.get("title"), "content": row.get("content")}) for row in rows):
            continue
        for row in rows:
            if (row.get('metadata') or {}).get('is_searchable') is False:
                continue
            plans.append({'collection': collection, 'row': row})
    return plans


def apply_quarantine(db, plans, backup_path):
    # Exclusive creation avoids overwriting the rollback record. Save before writing.
    with Path(backup_path).open('x') as backup:
        backup.write(json_util.dumps(plans, ensure_ascii=False, indent=2))
    modified = 0
    for plan in plans:
        row = plan['row']
        result = db[plan['collection']].update_one(
            {'_id': row['_id'], 'content': row.get('content'), 'metadata': row.get('metadata', {})},
            {'$set': {'metadata.is_stub': True, 'metadata.is_searchable': False}},
        )
        modified += result.modified_count
    return modified


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup')
    args = parser.parse_args()
    if args.apply and not args.backup:
        parser.error('--apply requires --backup')
    client = MongoClient(os.environ['MONGO_URI'], serverSelectionTimeoutMS=10000)
    db = client['original_db']
    plans = plan_quarantine(db, json.loads(Path(args.manifest).read_text()))
    print(json.dumps([{'collection': p['collection'], 'doc_id': p['row']['doc_id'], 'chunk_id': p['row'].get('chunk_id')} for p in plans], ensure_ascii=False, indent=2))
    if args.apply:
        print(json.dumps({'modified_chunks': apply_quarantine(db, plans, args.backup)}))


if __name__ == '__main__':
    main()
