"""
PDF 파일 → MongoDB 삽입 스크립트

DB/판례/   → case 컬렉션
DB/행정해석/ → interpretation 컬렉션
DB/결정례/  → decision 컬렉션

실행:
  python3 scripts/pdf_ingest/pdf_to_mongo.py --type all
  python3 scripts/pdf_ingest/pdf_to_mongo.py --type 판례
  python3 scripts/pdf_ingest/pdf_to_mongo.py --type 행정해석 --dry-run
"""

import argparse
import hashlib
import os
import sys
from datetime import datetime
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.core.database.mongo_client import MongoClientSingleton
from scripts.utils.document_extraction import call_extract_text, require_complete_pdf
from scripts.core.database.source_versioning import enrich_source_document

# PDF 파일 디렉토리 (Mac Mini 경로)
PDF_BASE = Path("/Users/loum/loum/pdf_db")

PDF_DIRS = {
    "판례":   (PDF_BASE / "판례",   "case"),
    "행정해석": (PDF_BASE / "행정해석", "interpretation"),
    "결정례":  (PDF_BASE / "결정례",  "decision"),
}

# 청크 최대 크기 (문자 수)
MAX_CHUNK_CHARS = 3000
OVERLAP_CHARS = 200


# ─── 텍스트 추출 ──────────────────────────────────────────

def extract_pdf_document(pdf_path: Path) -> dict:
    """Use the same native/visual parser and coverage gate as scheduled crawls."""
    result = call_extract_text(pdf_path.read_bytes(), pdf_path.name)
    require_complete_pdf(result)
    return result


def extract_pdf_text(pdf_path: Path) -> str:
    return extract_pdf_document(pdf_path)['structured_text']


# ─── 파일명 파싱 ──────────────────────────────────────────

def _filename_date(value: str) -> str:
    parts = value.replace(' ', '').strip('.').split('.')
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return ''
    try:
        datetime(*(int(part) for part in parts))
    except ValueError:
        return ''
    return '.'.join(parts)


def parse_case_filename(name: str) -> dict:
    result = {"court": "", "date": "", "case_number": "", "title": name}
    before, marker, after = name.partition(' 선고 ')
    if marker:
        court, separator, date = before.partition(' ')
        case_number, ending, _ = after.partition(' 판결')
        date = _filename_date(date)
        if '법원' in court and separator and date and ending and case_number and ' ' not in case_number:
            return dict(court=court, date=date, case_number=case_number,
                        title=f'{court} {date}. 선고 {case_number} 판결')
    court, separator, case_number = name.partition('-')
    if separator and '법원' in court and case_number and case_number[0].isdigit():
        return dict(court=court.strip(),date='',case_number=case_number.strip(),title=f'{court.strip()} {case_number.strip()} 판결')
    return result


def _dated_filename(name: str):
    normalized = name.replace('（', '(').replace('）', ')')
    body, separator, date = normalized.partition('(')
    return body.strip(), date.rstrip(')').strip() if separator else ''


def parse_interpretation_filename(name: str) -> dict:
    body, date = _dated_filename(name)
    parts = body.split()
    for i, part in enumerate(parts):
        department, separator, number = part.partition('-')
        if separator and department and number.isdigit():
            return dict(dept=' '.join(parts[:i]) or '고용노동부',doc_number=part,date=date,title=name)
    return dict(dept='고용노동부',doc_number='',date='',title=name)


def parse_decision_filename(name: str) -> dict:
    body, date = _dated_filename(name)
    committee, separator, case_number = body.partition('위원회')
    if separator and date and case_number.strip():
        return dict(committee=committee+'위원회',case_number=case_number.strip(),date=date,title=name)
    return dict(committee='',case_number='',date='',title=name)


# ─── doc_id 생성 ──────────────────────────────────────────

def make_doc_id(prefix: str, name: str) -> str:
    """파일명 기반 doc_id 생성 (prefix_해시8자리)"""
    h = hashlib.md5(name.encode("utf-8")).hexdigest()[:8]
    safe = ''.join(c if ('가' <= c <= '힣' or 'a' <= c <= 'z' or '0' <= c <= '9' or c in '_-') else '_' for c in name.lower())[:30]
    return f"pdf_{prefix}_{safe}_{h}"


# ─── 텍스트 청크 분할 ─────────────────────────────────────

def split_text(text: str, max_chars: int = MAX_CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> list[str]:
    """Search chunks keep whole words; canonical display is stored separately.

    overlap remains in the signature for older callers, but no longer duplicates text.
    A word longer than the budget stays intact instead of splitting an identifier.
    """
    if max_chars < 1:
        raise ValueError('max_chars must be positive')
    chunks, current = [], ''
    for paragraph in text.split('\n\n'):
        if not paragraph:
            continue
        if len(paragraph) <= max_chars:
            pieces = [paragraph]
        else:
            pieces, fragment = [], ''
            for word in paragraph.split():
                if fragment and len(fragment) + len(word) + 1 > max_chars:
                    pieces.append(fragment)
                    fragment = ''
                fragment = (fragment + ' ' + word).lstrip()
            if fragment:
                pieces.append(fragment)
        for piece in pieces:
            if current and len(current) + len(piece) + 2 > max_chars:
                chunks.append(current)
                current = ''
            current = (current + '\n\n' + piece).lstrip()
    if current:
        chunks.append(current)
    return chunks or [text]


# ─── MongoDB 문서 생성 ────────────────────────────────────

def build_case_doc(doc_id: str, chunk_seq: int, total: int, chunk_text: str, meta: dict) -> dict:
    chunk_id = f"case:{doc_id}:section:{chunk_seq}" if total > 1 else f"case:{doc_id}"
    return {
        "doc_id": doc_id,
        "chunk_seq": chunk_seq,
        "doc_type": "판례",
        "content": chunk_text,
        "title": meta["title"],
        "sub_title": meta.get("court_date", ""),
        "article_number": chunk_seq,
        "article_title": f"본문 {chunk_seq}",
        "chunk_id": chunk_id,
        "metadata": {
            "source_type": "pdf",
            "source_file": meta["filename"],
            "court": meta.get("court", ""),
            "case_number": meta.get("case_number", ""),
            "effective": meta.get("date", ""),
            "dept_name": "고용노동부",
            "is_active": True,
            "total_chunks": total,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        },
    }


def build_interpretation_doc(doc_id: str, chunk_seq: int, total: int, chunk_text: str, meta: dict) -> dict:
    chunk_id = f"interpretation:{doc_id}:article:{chunk_seq}" if total > 1 else f"interpretation:{doc_id}"
    return {
        "doc_id": doc_id,
        "doc_type": "법령해석",
        "content": chunk_text,
        "title": meta["title"],
        "sub_title": meta.get("doc_number", ""),
        "article_number": chunk_seq,
        "article_title": "본문" if total == 1 else f"본문 {chunk_seq}",
        "chunk_id": chunk_id,
        "metadata": {
            "source_type": "pdf",
            "source_file": meta["filename"],
            "dept_name": meta.get("dept", "고용노동부"),
            "doc_number": meta.get("doc_number", ""),
            "effective": meta.get("date", ""),
            "is_active": True,
            "total_chunks": total,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        },
    }


def build_decision_doc(doc_id: str, chunk_seq: int, total: int, chunk_text: str, meta: dict) -> dict:
    chunk_id = f"decision:{doc_id}:section:{chunk_seq}" if total > 1 else f"decision:{doc_id}"
    return {
        "doc_id": doc_id,
        "doc_type": "심의결정례",
        "content": chunk_text,
        "title": meta["title"],
        "sub_title": meta.get("case_number", ""),
        "article_number": chunk_seq,
        "chunk_id": chunk_id,
        "metadata": {
            "source_type": "pdf",
            "source_file": meta["filename"],
            "committee": meta.get("committee", ""),
            "case_number": meta.get("case_number", ""),
            "effective": meta.get("date", ""),
            "dept_name": meta.get("committee", "노동위원회"),
            "is_active": True,
            "total_chunks": total,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        },
    }


# ─── 메인 처리 ────────────────────────────────────────────

def process_directory(type_name: str, pdf_dir: Path, collection_name: str, db, dry_run: bool):
    col = db[collection_name]
    pdf_files = sorted(pdf_dir.glob("*.pdf"))
    print(f"\n{'='*60}")
    print(f"[{type_name}] {len(pdf_files)}개 PDF → {collection_name} 컬렉션")
    print(f"{'='*60}")

    inserted = skipped = failed = 0

    for pdf_path in pdf_files:
        name = pdf_path.stem  # 확장자 제외 파일명

        # 이미 처리된 파일 체크 (source_file 기반)
        existing = col.find_one({"metadata.source_file": pdf_path.name})
        if existing:
            skipped += 1
            continue

        # 텍스트 추출
        try:
            extraction = extract_pdf_document(pdf_path)
            text = extraction['structured_text']
        except Exception as exc:
            print(f"  ⚠ 검증되지 않은 PDF: {name[:50]} ({exc})")
            failed += 1
            continue
        if not text or len(text) < 50:
            print(f"  ⚠ 텍스트 부족: {name[:50]}")
            failed += 1
            continue

        # 파일명 파싱
        if type_name == "판례":
            parsed = parse_case_filename(name)
            doc_id = make_doc_id("case", name)
            builder = build_case_doc
            meta_extra = {
                "court": parsed["court"],
                "case_number": parsed["case_number"],
                "date": parsed["date"],
                "court_date": f"{parsed['court']} {parsed['date']}" if parsed["date"] else parsed["court"],
            }
        elif type_name == "행정해석":
            parsed = parse_interpretation_filename(name)
            doc_id = make_doc_id("interp", name)
            builder = build_interpretation_doc
            meta_extra = {
                "dept": parsed["dept"],
                "doc_number": parsed["doc_number"],
                "date": parsed["date"],
            }
        else:  # 결정례
            parsed = parse_decision_filename(name)
            doc_id = make_doc_id("decision", name)
            builder = build_decision_doc
            meta_extra = {
                "committee": parsed["committee"],
                "case_number": parsed["case_number"],
                "date": parsed["date"],
            }

        meta = {"title": parsed["title"], "filename": pdf_path.name, **meta_extra}

        # 청크 분할
        chunks = split_text(text)
        total = len(chunks)

        if dry_run:
            print(f"  [DRY] {name[:50]} → {total}청크, doc_id={doc_id}")
            inserted += 1
            continue

        # MongoDB 삽입
        try:
            for seq, chunk_text in enumerate(chunks, 1):
                doc = builder(doc_id, seq, total, chunk_text, meta)
                if seq == 1:
                    doc['metadata'].update(document_blocks=extraction['document_blocks'],
                        document_text=text, document_blocks_scope='section',
                        paragraph_projection=extraction['extraction_metadata']['paragraph_projection'],
                        extraction_metadata=extraction['extraction_metadata'])
                doc = enrich_source_document(doc, collection_name)
                col.update_one(
                    {"chunk_id": doc["chunk_id"]},
                    {"$set": doc},
                    upsert=True,
                )
            inserted += 1
            if inserted % 50 == 0:
                print(f"  진행: {inserted}/{len(pdf_files)}건 처리")
        except Exception as e:
            print(f"  ✗ 삽입 실패 {name[:40]}: {e}")
            failed += 1

    print(f"\n  ✅ 삽입: {inserted}건 | 건너뜀(기존): {skipped}건 | 실패: {failed}건")
    return inserted, skipped, failed


def main():
    parser = argparse.ArgumentParser(description="PDF → MongoDB 삽입")
    parser.add_argument("--type", choices=["판례", "행정해석", "결정례", "all"], default="all")
    parser.add_argument("--dry-run", action="store_true", help="삽입 없이 미리 보기")
    args = parser.parse_args()

    db = MongoClientSingleton.get_db()

    targets = ["판례", "행정해석", "결정례"] if args.type == "all" else [args.type]
    total_inserted = 0

    for type_name in targets:
        pdf_dir, col_name = PDF_DIRS[type_name]
        if not pdf_dir.exists():
            print(f"⚠ 디렉토리 없음: {pdf_dir}")
            continue
        ins, skp, fail = process_directory(type_name, pdf_dir, col_name, db, args.dry_run)
        total_inserted += ins

    print(f"\n{'='*60}")
    print(f"✅ 전체 완료: {total_inserted}건 처리")


if __name__ == "__main__":
    main()
