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
import re
import sys
from datetime import datetime
from pathlib import Path

import pymupdf  # pip install pymupdf

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.core.database.mongo_client import MongoClientSingleton

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

def extract_pdf_text(pdf_path: Path) -> str:
    """pymupdf로 PDF 전체 텍스트 추출"""
    try:
        doc = pymupdf.open(str(pdf_path))
        pages = []
        for page in doc:
            text = page.get_text("text")
            if text.strip():
                pages.append(text.strip())
        doc.close()
        return "\n\n".join(pages)
    except Exception as e:
        print(f"  ⚠ 텍스트 추출 실패 {pdf_path.name}: {e}")
        return ""


# ─── 파일명 파싱 ──────────────────────────────────────────

def parse_case_filename(name: str) -> dict:
    """판례 파일명에서 법원명, 사건번호, 제목 추출"""
    # "대법원 1994. 4. 12. 선고 92다20309 판결" 형식
    m = re.match(r"(.+법원[^\s]*)\s+(\d{4})\.\s*(\d+)\.\s*(\d+)\.?\s*선고\s*([^\s]+)\s*판결", name)
    if m:
        court, y, mo, d, case_num = m.groups()
        return {
            "court": court.strip(),
            "date": f"{y}.{mo}.{d}",
            "case_number": case_num.strip(),
            "title": f"{court.strip()} {y}.{mo}.{d}. 선고 {case_num} 판결",
        }
    # "광주고등법원-2016나10826" 하이픈 축약 형식
    m2 = re.match(r"(.+법원[가-힣]*)-(\d+[가-힣]+\d+)", name)
    if m2:
        court, case_num = m2.groups()
        return {
            "court": court.strip(),
            "date": "",
            "case_number": case_num.strip(),
            "title": f"{court.strip()} {case_num} 판결",
        }
    return {"court": "", "date": "", "case_number": "", "title": name}


def parse_interpretation_filename(name: str) -> dict:
    """행정해석 파일명에서 부처, 문서번호 추출"""
    # "고용노동부 근로기준과-2328 (2004.5.12)" 형식
    m = re.match(r"(.+부처?|.+위원회|.+팀|.+과|.+실)?\s*([가-힣]+[-]\d+)\s*[\(（]?(.+?)[\)）]?$", name)
    if m:
        dept = (m.group(1) or "").strip()
        doc_num = m.group(2).strip()
        date_str = (m.group(3) or "").strip().rstrip(")")
        return {"dept": dept or "고용노동부", "doc_number": doc_num, "date": date_str, "title": name}
    return {"dept": "고용노동부", "doc_number": "", "date": "", "title": name}


def parse_decision_filename(name: str) -> dict:
    """결정례 파일명에서 위원회명, 사건번호 추출"""
    m = re.match(r"(.+위원회)\s+(.+?)\s*[\(（](.+?)[\)）]?$", name)
    if m:
        committee, case_num, date_str = m.groups()
        return {
            "committee": committee.strip(),
            "case_number": case_num.strip(),
            "date": date_str.strip(),
            "title": name,
        }
    return {"committee": "", "case_number": "", "date": "", "title": name}


# ─── doc_id 생성 ──────────────────────────────────────────

def make_doc_id(prefix: str, name: str) -> str:
    """파일명 기반 doc_id 생성 (prefix_해시8자리)"""
    h = hashlib.md5(name.encode("utf-8")).hexdigest()[:8]
    safe = re.sub(r"[^가-힣a-z0-9_\-]", "_", name.lower())[:30]
    return f"pdf_{prefix}_{safe}_{h}"


# ─── 텍스트 청크 분할 ─────────────────────────────────────

def split_text(text: str, max_chars: int = MAX_CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> list[str]:
    """텍스트를 max_chars 기준으로 청크 분할 (문단 단위 우선)"""
    if len(text) <= max_chars:
        return [text]

    paragraphs = re.split(r"\n{2,}", text)
    chunks, current = [], ""
    for para in paragraphs:
        if len(current) + len(para) + 2 <= max_chars:
            current = (current + "\n\n" + para).lstrip()
        else:
            if current:
                chunks.append(current)
            # 단일 문단이 max_chars 초과 시 강제 분할
            if len(para) > max_chars:
                for i in range(0, len(para), max_chars - overlap):
                    chunks.append(para[i:i + max_chars])
                current = ""
            else:
                current = para

    if current:
        chunks.append(current)
    return chunks if chunks else [text]


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
        text = extract_pdf_text(pdf_path)
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
