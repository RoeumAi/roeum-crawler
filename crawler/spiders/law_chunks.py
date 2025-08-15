# spiders/laws_chunks_better.py
import re
import json
import hashlib
import scrapy
from dataclasses import dataclass, asdict
from typing import Iterable, List, Tuple, Optional
from scrapy_playwright.page import PageMethod

# ========== 텍스트 유틸 ==========

_CIRCLES = {chr(0x2460+i): i+1 for i in range(20)}  # ①..⑳
_HANGUL_ITEMS = "가나다라마바사아자차카타파하"

def clean_spaces(s: str) -> str:
    s = (s or "").replace("\xa0", " ")
    s = re.sub(r"(?i)&nbsp;", " ", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"[ \t]*\n[ \t]*", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = s.replace("［", "[").replace("］", "]").replace("（", "(").replace("）", ")")
    return s.strip()

def is_noise_line(t: str) -> bool:
    ban = {"판례","연혁","위임행정규칙","규제","생활법령","한눈보기"}
    t = (t or "").strip()
    if not t: return True
    if t in ban: return True
    if len(t) <= 2 and not re.search(r"\d", t):  # 메뉴성 단어
        return True
    if re.search(r"\d{2,3}-\d{3,4}-\d{4}", t):  # 전화
        return True
    if re.fullmatch(r"[▷▶◀◁■□●○\-\–\—•·\*]+", t):  # 불릿 모양
        return True
    return False

def drop_noise_lines(lines: List[str]) -> List[str]:
    out = []
    for ln in lines:
        t = clean_spaces(ln)
        if not is_noise_line(t):
            out.append(t)
    return out

def circle_or_hang_to_int(token: str) -> Optional[int]:
    if not token:
        return None
    ch = token.strip()[0]
    if ch in _CIRCLES:
        return _CIRCLES[ch]
    m = re.match(r"제\s*(\d+)\s*항", token)  # "제3항"
    if m: return int(m.group(1))
    if ch in _HANGUL_ITEMS:
        return _HANGUL_ITEMS.index(ch)+1
    m2 = re.match(r"(\d+)\.", token)  # "1."
    if m2: return int(m2.group(1))
    return None

# ① / 제n항 → 항 단위 분리
def split_paragraphs(text: str) -> List[Tuple[Optional[int], str]]:
    pat = re.compile(r"(?m)^(?:\s*(제\s*\d+\s*항)|\s*([\u2460-\u2473]))\s*")
    idx = [(m.start(), circle_or_hang_to_int(m.group(1) or m.group(2))) for m in pat.finditer(text)]
    if not idx:
        return [(None, text.strip())]
    out = []
    for i, (st, pno) in enumerate(idx):
        ed = idx[i+1][0] if i+1 < len(idx) else len(text)
        chunk = text[st:ed]
        chunk = re.sub(r"^(?:\s*제\s*\d+\s*항|\s*[\u2460-\u2473])\s*", "", chunk, flags=re.M)
        chunk = clean_spaces(chunk)
        if chunk:
            out.append((pno, chunk))
    return out

# 1./가./나. → 호 단위 분리
def split_items(para_text: str) -> List[Tuple[Optional[int], str]]:
    pat = re.compile(r"(?m)^\s*((?:\d+|[가-힣])\.)\s+")
    idx = [(m.start(), m.group(1)) for m in pat.finditer(para_text)]
    if not idx:
        return [(None, para_text.strip())]
    out = []
    for i, (st, tok) in enumerate(idx):
        ed = idx[i+1][0] if i+1 < len(idx) else len(para_text)
        body = para_text[st:ed]
        body = re.sub(r"^\s*(?:\d+|[가-힣])\.\s*", "", body)
        no = circle_or_hang_to_int(tok)
        body = clean_spaces(body)
        if body:
            out.append((no, body))
    return out

# 과도 길이 시 문장 재분할
_SENT_END = re.compile(r"([.!?]|(?:다|니다|요|함|됨|바)\.)\s+")

def split_sentences(s: str) -> list[str]:
    if not s:
        return []
    # 문장 끝(캡처된 구두점 포함) 뒤에 개행을 넣고 분할
    s = _SENT_END.sub(r"\1\n", s)
    return [x.strip() for x in s.splitlines() if x.strip()]

def rechunk_by_sentence(text: str, target=900, maxlen=1400) -> List[str]:
    sents = split_sentences(text)
    out, cur = [], ""
    for sent in sents:
        if not cur:
            cur = sent
            continue
        if len(cur) + 1 + len(sent) <= target:
            cur = cur + " " + sent
        elif len(cur) <= maxlen:
            out.append(cur)
            cur = sent
        else:
            out.append(cur[:maxlen])
            cur = sent
    if cur:
        out.append(cur)
    return out

def text_hash(s: str, size=8) -> str:
    return hashlib.blake2s(s.encode("utf-8"), digest_size=size).hexdigest()

def tokens_estimate(s: str) -> int:
    # 대략적인 추정(영어 4, 한국어 2.5자당 1토큰으로 가정)
    return max(1, int(len(s) / 2.5))

# 교차참조 추출: 「근로기준법」 제17조
REF_RE = re.compile(r"「\s*([^」]+?)\s*」\s*제\s*(\d+)\s*조")

# ========== 데이터 모델 ==========

@dataclass
class DocHeader:
    doc_id: str
    title: str
    short_title: Optional[str]
    ministry: Optional[str]
    efYd: Optional[str]
    lsiSeq: Optional[str]
    source_url: str
    aliases: List[str]
    lang: str = "ko"
    source: str = "국가법령정보센터"
    retrieval_hint: Optional[str] = None
    kind: str = "document"

@dataclass
class Chunk:
    chunk_id: str
    logical_key: str
    text: str
    display_text: str
    breadcrumbs: List[str]
    chapter_no: Optional[int]
    chapter_title: Optional[str]
    article_no: Optional[int]
    article_title: Optional[str]
    paragraph_no: Optional[int]
    item_no: Optional[int]
    tokens_est: int
    links_out: List[dict]
    source_url: str
    efYd: Optional[str]
    lsiSeq: Optional[str]
    lang: str = "ko"
    source: str = "국가법령정보센터"
    kind: str = "chunk"

def make_chunk_id(lsiSeq, efYd, ch, art, para, item, text) -> Tuple[str, str]:
    logical = f"law:{lsiSeq}:{efYd}:ch{ch or 0}:art{art or 0}:para{para or 0}:item{item or 0}"
    rev = text_hash(text, size=4)
    return f"{logical}:{rev}", logical

# ========== 스파이더 ==========

class LawsChunksBetterSpider(scrapy.Spider):
    name = "laws_chunks"

    custom_settings = {
        "FEED_EXPORT_ENCODING": "utf-8",
        "AUTOTHROTTLE_ENABLED": True,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 0.8,
        # 두 개의 피드로 분리 저장
        "FEEDS": {
            "documents.jsonl": {"format": "jsonlines", "encoding": "utf-8", "item_export_kwargs": {"ensure_ascii": False}},
            "chunks.jsonl": {"format": "jsonlines", "encoding": "utf-8", "item_export_kwargs": {"ensure_ascii": False}},
        },
        # items를 피드별로 라우팅하기 위한 익스텐션
        "ITEM_PIPELINES": {
            "__main__.FeedRouterPipeline": 100,
        }
    }

    def __init__(self, dept="1492000", max_pages=2, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dept = str(dept)
        self.max_pages = int(max_pages)

    start_urls = [
        "https://www.law.go.kr/LSW/lsAstSc.do?menuId=391&subMenuId=397&tabMenuId=437&query=#AJAX"
    ]

    async def _route(self, route, request):
        if request.resource_type in ("image","font","media"):
            return await route.abort()
        return await route.continue_()

    def start_requests(self):
        yield scrapy.Request(
            self.start_urls[0],
            meta={
                "playwright": True,
                "playwright_page_methods": [
                    PageMethod("route", "**/*", self._route),
                    PageMethod("wait_for_load_state", "domcontentloaded"),
                    PageMethod("wait_for_selector", f"#cptOfi{self.dept}", timeout=60000),
                    PageMethod("click", f"#cptOfi{self.dept}"),
                    PageMethod("wait_for_load_state", "networkidle"),
                    PageMethod("wait_for_selector", "#resultTableDiv a[onclick*='lsReturnSearch']", timeout=60000),
                ],
            },
            callback=self.parse_list
        )

    def parse_list(self, resp: scrapy.http.Response):
        # 목록 → 상세 URL 구성
        for a in resp.css("#resultTableDiv a[onclick*='lsReturnSearch']"):
            oc = a.attrib.get("onclick","")
            m = re.search(r"lsReturnSearch\((.*?)\)", oc)
            if not m:
                continue
            args = re.findall(r"'([^']+)'", m.group(1))
            efYd = next((x for x in args if re.fullmatch(r"\d{8}", x)), None)
            nums = [x for x in args if re.fullmatch(r"\d{5,}", x)]
            nums = [x for x in nums if x != efYd]
            lsiSeq = nums[-1] if nums else None
            if not lsiSeq:
                continue
            url = f"https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq={lsiSeq}"
            if efYd:
                url += f"&efYd={efYd}"
            yield scrapy.Request(
                url,
                meta={
                    "playwright": True,
                    "playwright_page_methods": [
                        PageMethod("route", "**/*", self._route),
                        PageMethod("wait_for_load_state", "domcontentloaded"),
                        PageMethod("wait_for_selector", "#contentBody, #conScroll, #content", timeout=60000),
                    ],
                    "keys": (lsiSeq, efYd),
                },
                callback=self.parse_detail
            )

        # 페이지 이동
        m = re.search(r"\((\d+)\s*/\s*(\d+)\)", resp.text or "")
        cur, last = (int(m.group(1)), int(m.group(2))) if m else (1,1)
        for p in range(cur+1, min(last, self.max_pages)+1):
            yield scrapy.Request(
                resp.url, dont_filter=True,
                meta={
                    "playwright": True,
                    "playwright_page_methods": [
                        PageMethod("evaluate", f"pageSearch('lsListDiv','{p}')"),
                        PageMethod("wait_for_load_state", "networkidle"),
                        PageMethod("wait_for_selector", "#resultTableDiv a[onclick*='lsReturnSearch']", timeout=60000),
                    ],
                },
                callback=self.parse_list
            )

    def parse_detail(self, resp: scrapy.http.Response):
        lsiSeq, efYd = resp.meta.get("keys", (None, None))
        sel = resp.selector

        # 제목/부제/약칭/부처
        title = clean_spaces("".join(sel.css("#contentBody h2::text, #conScroll h2::text, h2::text").getall()))
        subtitle = clean_spaces(" ".join(sel.css(".ct_sub ::text").getall()))
        ministry = clean_spaces(" ".join(sel.css(".ct_sub ::text").re(r"(고용노동부|.*부)\s*")) or "")
        short_title = None
        mshort = re.search(r"약칭[:：]\s*([^)]+)\)", resp.text or "")
        if mshort:
            short_title = clean_spaces(mshort.group(1))

        # 본문 컨테이너 텍스트 수집
        texts = sel.css("#contentBody, #conScroll, #content").xpath(".//text()").getall()
        lines = [clean_spaces(t) for t in texts]
        lines = [l for l in lines if l]
        lines = drop_noise_lines(lines)
        full = "\n".join(lines)

        # 헤더(documents) 1건 출력
        doc = DocHeader(
            doc_id=f"law:{lsiSeq}:{efYd}",
            title=title or "법령",
            short_title=short_title,
            ministry=ministry or None,
            efYd=efYd,
            lsiSeq=lsiSeq,
            source_url=resp.url.split("#")[0],
            aliases=[a for a in {short_title or "", title} if a],
        )
        yield asdict(doc)

        # 장/조 패턴
        re_chapter = re.compile(r"(?m)^\s*제\s*(\d+)\s*장\s*(.*)$")
        re_article = re.compile(r"(?m)^\s*제\s*(\d+)\s*조\s*[（(]?([^)）]*)[)）]?\s*$")

        # 조 경계 계산
        art_marks = list(re.finditer(re_article, full))
        chap_marks = list(re.finditer(re_chapter, full))
        chap_pos = [(m.start(), int(m.group(1)), clean_spaces(m.group(2))) for m in chap_marks]

        def find_chapter_for(pos):
            cand = [(p,cno,ct) for (p,cno,ct) in chap_pos if p <= pos]
            return (cand[-1][1], cand[-1][2]) if cand else (None, None)

        # 조별 본문 범위 추출
        art_spans = []
        for i, m in enumerate(art_marks):
            st = m.end()
            ed = art_marks[i+1].start() if i+1 < len(art_marks) else len(full)
            a_no = int(m.group(1))
            a_title = clean_spaces(m.group(2))
            a_body = clean_spaces(full[st:ed])
            art_spans.append((m.start(), a_no, a_title, a_body))

        seen = set()

        for pos, a_no, a_title, a_body in art_spans:
            ch_no, ch_title = find_chapter_for(pos)

            # 항 → 호 분할
            for p_no, p_text in split_paragraphs(a_body):
                for i_no, i_text in split_items(p_text):
                    if not i_text or len(i_text) < 2:
                        continue

                    # 과도 길이면 문장 재분할
                    leaf_parts = rechunk_by_sentence(i_text, target=900, maxlen=1400)
                    order = 0
                    for leaf in leaf_parts:
                        leaf = clean_spaces(leaf)
                        if not leaf:
                            continue
                        # 중복 방지
                        sig = f"{lsiSeq}:{efYd}:{ch_no}:{a_no}:{p_no}:{i_no}:{text_hash(leaf,4)}"
                        if sig in seen:
                            continue
                        seen.add(sig)

                        # 링크 추출
                        links = [{"law": m.group(1), "article": int(m.group(2))} for m in REF_RE.finditer(leaf)]

                        breadcrumbs = []
                        if ch_no is not None:
                            breadcrumbs.append(f"제{ch_no}장 {ch_title or ''}".strip())
                        if a_no is not None:
                            breadcrumbs.append(f"제{a_no}조{f'({a_title})' if a_title else ''}")
                        if p_no: breadcrumbs.append(f"{p_no}항")
                        if i_no: breadcrumbs.append(f"{i_no}호")

                        display_prefix = " > ".join(breadcrumbs)
                        display = f"{display_prefix} — {leaf}" if display_prefix else leaf

                        chunk_id, logical_key = make_chunk_id(lsiSeq, efYd, ch_no, a_no, p_no, i_no, leaf)

                        item = Chunk(
                            chunk_id=chunk_id,
                            logical_key=logical_key,
                            text=leaf,
                            display_text=display,
                            breadcrumbs=breadcrumbs,
                            chapter_no=ch_no, chapter_title=ch_title,
                            article_no=a_no, article_title=a_title,
                            paragraph_no=p_no, item_no=i_no,
                            tokens_est=tokens_estimate(leaf),
                            links_out=links,
                            source_url=resp.url.split("#")[0],
                            efYd=efYd, lsiSeq=lsiSeq,
                        )
                        yield asdict(item)
                        order += 1


# ========== 피드 라우팅 파이프라인 ==========
# 문서/청크를 서로 다른 파일로 내보내기 위해 "kind" 필드로 라우팅
class FeedRouterPipeline:
    def process_item(self, item, spider):
        # Scrapy FEEDS는 파일별로 전체 파이프라인을 공유하므로
        # 여기서는 단순히 pass: FEEDS 설정이 파일 2개를 모두 받게 하되,
        # item_export_kwargs 가 동일 파일에 모두 기록하는 걸 허용.
        # 실제 운영에선 ItemExporter를 커스터마이즈하거나, 두 번 크롤링/저장을 분리해도 좋음.
        return item
