"""Build replacement chunks while preserving existing document/chunk identities.

Quality warnings do not prevent replacement. Empty extraction or major text loss
keeps the existing document. The caller prepares vectors and commits each entire
document in a MongoDB transaction, after comparing with these before-images.
"""
from bisect import bisect_left
from copy import deepcopy
from hashlib import sha256

from scripts.core.database.source_versioning import enrich_source_document
from scripts.utils.document_extraction import usable_document


def compact(text):
    return ''.join(char for char in text if not char.isspace())


def repartition(text, previous_lengths):
    """Keep existing chunk count; move boundaries to adjacent whole words."""
    count = len(previous_lengths)
    boundaries = [i for i in range(1, len(text)) if text[i - 1].isspace() and not text[i].isspace()]
    if len(boundaries) < count - 1:
        raise ValueError('insufficient_word_boundaries')
    cuts, consumed, position = [0], 0, 0
    total = sum(previous_lengths)
    for index, length in enumerate(previous_lengths[:-1]):
        consumed += length
        target = len(text) * consumed / total
        limit = len(boundaries) - (count - 1 - index)
        right = min(max(bisect_left(boundaries, target), position), limit)
        choices = [right] + ([right - 1] if right > position else [])
        chosen = min(choices, key=lambda item: abs(boundaries[item] - target))
        cuts.append(boundaries[chosen])
        position = chosen + 1
    cuts.append(len(text))
    chunks = [text[a:b].strip() for a, b in zip(cuts, cuts[1:])]
    assert all(chunks) and compact(''.join(chunks)) == compact(text)
    return chunks


def replacement_document(before, extracted, collection, provenance, minimum_retention=0.65):
    try:
        document = usable_document(extracted)
    except ValueError:
        return {'action': 'retain', 'reason': 'no_usable_extraction'}
    text = document['structured_text']
    old_chars = sum(len(compact(row['content'])) for row in before)
    new_chars = len(compact(text))
    retention = new_chars / max(old_chars, 1)
    if retention < minimum_retention:
        return {'action': 'retain', 'reason': 'major_text_loss', 'retention': retention}
    chunks = repartition(text, [max(len(row['content']), 1) for row in before])
    after, embeddings = [], []
    quality = document['quality_complete']
    status = 'complete' if quality else 'partial'
    for index, (old, content) in enumerate(zip(before, chunks)):
        row = deepcopy(old)
        row['content'] = content
        metadata = row['metadata']
        metadata.update(pdf_extraction_status=status, pdf_retry_needed=not quality,
                        paragraph_projection=document['extraction_metadata']['paragraph_projection'],
                        parsing_recovery=dict(provenance, quality_status='complete' if quality else 'needs_review'))
        if index == 0:
            metadata.update(document_blocks=document['document_blocks'], document_text=text,
                            document_blocks_scope='section', extraction_metadata=document['extraction_metadata'],
                            raw_document_text=extracted.get('full_text', ''))
        row = enrich_source_document(row, collection)
        if metadata.get('is_searchable'):
            recipe = metadata.get('embed_recipe')
            if recipe == 'title+whole@4096':
                embedding_input = '\n'.join([row['title'], text])[:4096]
            elif recipe == 'title+sub_title+content@4096':
                embedding_input = '\n'.join([row['title'], row.get('sub_title', ''), content])[:4096]
            else:
                raise ValueError('unknown_embedding_recipe')
            key = sha256(embedding_input.encode()).hexdigest()
            embeddings.append(dict(index=index, embedding_input=embedding_input, embedding_input_sha256=key))
            row['metadata']['parsing_recovery']['embedding_input_sha256'] = key
            # The caller fills this only from the vector cache for this input.
            row.pop('embedding', None)
            row.pop('embedding_hash', None)
        after.append(row)
    return dict(action='replace', retention=retention, quality_complete=quality,
                before=before, after=after, embeddings=embeddings)
