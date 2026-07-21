# Citation Provenance Migration

## Purpose

The citation resolver compares the source saved with a chat answer against the
current MongoDB chunk. Every legal chunk therefore needs these fields:

- `content_hash`: SHA-256 of the exact `content` string.
- `source_version_id`: `{collection}:{chunk_id}:{content_hash}`.
- `metadata.is_active`: current chunks are `true`; preserved historical chunks
  remain `false`.

## Safe rollout order

1. Deploy `roeum-manage-server` with active-only current lookup and legacy hash
   compatibility.
2. Deploy `chat_generation` and `chat_agent` provenance propagation.
3. Deploy crawler writers that generate SHA-256 provenance fields.
4. Audit existing MongoDB data with the migration command.
5. Apply the backfill after reviewing per-collection counts.
6. Deploy `roeum-web`.

The resolver remains compatible with old chats throughout this sequence.

## Audit

The command is read-only unless `--apply` is supplied.

```bash
python3 scripts/migrations/backfill_source_provenance.py
python3 scripts/migrations/backfill_source_provenance.py \
  --collections law case interpretation
```

Review these counters:

- `content_hash.missing` and `content_hash.mismatch`
- `source_version_id.missing` and `source_version_id.mismatch`
- `active_state.missing`
- `would_update`

## Apply

```bash
python3 scripts/migrations/backfill_source_provenance.py --apply
```

The apply mode updates only calculated provenance fields and missing active
state. Explicit `metadata.is_active=false` historical versions are preserved.
It also creates:

- `idx_citation_chunk_hash` on `(chunk_id, content_hash)`
- `idx_source_version_id` on `source_version_id`

## Rollback

The added fields and indexes are additive. Application rollback does not
require deleting them. If index removal is required:

```javascript
db.<collection>.dropIndex("idx_citation_chunk_hash")
db.<collection>.dropIndex("idx_source_version_id")
```

Do not remove `content_hash`, `source_version_id`, or historical inactive
documents after chat answers have begun referencing them.
