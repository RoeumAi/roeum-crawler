"""노동위원회 판정사례(judgment)의 판정 위원회 이름 — metadata.dept_name 에 싣는다.

부제 꼴: `노동위원회(중앙) 2008. 9. 4. 판정 2008부해486`, `중앙노동위원회 …`, `지방노동위원회(서울) …`,
`경남지방노동위원회 …`, 사건번호 앞 지역(`경기2024부해4350`·`중앙2019부해321`), `사건번호 : 국행심 00-2510, …`.
부제·제목에 없으면 본문 앞부분의 위원회 이름을 쓴다. 지역 없이 `지방노동위원회()`면 '지방노동위원회'.
끝내 알 수 없으면 빈 문자열(추측해 채우지 않는다 — 2025년 nlrc 게시분은 부제가 날짜뿐이다).
공개 주소의 위원회(b2c parseRuling)는 부제에서 따로 읽으므로 이 값이 주소를 바꾸지 않는다.
"""
import re
REGIONS = "서울|부산|대구|인천|광주|대전|울산|경기|강원|충북|충남|전북|전남|경북|경남|제주|세종"


def commission(sub_title: str, title: str = "", content: str = "") -> str:
    s = re.sub(r"\s+", " ", (sub_title or "").strip())
    m = re.match(rf"^노동위원회\s*\(\s*중앙\s*\)", s) or re.match(r"^중앙노동위원회", s)
    if m: return "중앙노동위원회"
    m = re.match(rf"^지방노동위원회\s*\(\s*({REGIONS})\s*\)", s)
    if m: return f"{m.group(1)}지방노동위원회"
    m = re.match(rf"^({REGIONS})지방노동위원회", s)
    if m: return f"{m.group(1)}지방노동위원회"
    if re.match(r"^사건번호\s*:\s*국행심", s): return "국무총리행정심판위원회"
    if re.match(r"^사건번호\s*:\s*중노위", s): return "중앙노동위원회"
    # 사건번호 앞 지역(경기2024부해…·중앙2019부해…)
    for text in (s, title or ""):
        m = re.search(rf"(중앙|{REGIONS})\s*(\d{{2}}|\d{{4}})\s*[가-힣]{{1,6}}\s*\d+", text)
        if m: return "중앙노동위원회" if m.group(1) == "중앙" else f"{m.group(1)}지방노동위원회"
    head = (content or "")[:600]
    m = re.search(rf"중앙노동위원회|({REGIONS})지방노동위원회", head)
    if m: return m.group(0)
    if re.match(r"^지방노동위원회", s): return "지방노동위원회"
    return ""
