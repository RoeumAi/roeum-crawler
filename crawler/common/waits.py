from .selectors import LIST_SEL

def wait_mask_clear() -> str:
    # 로딩 마스크가 사라질 때까지 대기
    return """
        () => {
          const nodes = Array.from(document.querySelectorAll('.loadmask, .loadmask-msg'));
          return !nodes.some(n => {
            const s = window.getComputedStyle(n);
            return s && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
          });
        }
    """

def wait_list_ready_cases(list_sel: str = LIST_SEL) -> str:
    # 목록에 사건(precInfoP.do) 링크가 채워졌는지
    return f"""
        () => {{
          const box = document.querySelector('{list_sel}');
          if (!box) return false;
          if (box.querySelector('a[href*="precInfoP.do"]')) return true;
          if (box.querySelector('a[onclick*="precInfoP.do"]')) return true;
          return false;
        }}
    """

def wait_prec_detail_ready(min_len: int = 120) -> str:
    # 상세 본문이 충분히 채워졌는지 (타이틀 존재 + 본문 길이 확인)
    return f"""
        () => {{
          const root = document.querySelector('#contentBody, #content, #conScroll, #conSubScroll, body');
          if (!root) return false;
          const hasTitle = !!document.querySelector('h2[data-brl-use="PH/H1"]');
          const txt = (root.innerText || '').replace(/\\s+/g, ' ').trim();
          return hasTitle && txt.length >= {min_len};
        }}
    """

def click_possible_expanders() -> str:
    # 더보기/펼침 버튼 눌러서 '주문/이유'가 숨김이면 펼치기
    return """
        () => {
          try { window.scrollTo(0, document.body.scrollHeight); } catch(e) {}
          const sels = [
            '.btn_more',
            '.btn-open',
            'a[onclick*="more"]',
            'a[onclick*="open"]',
            'button[onclick*="more"]',
            'button[onclick*="open"]'
          ];
          for (const s of sels) {
            document.querySelectorAll(s).forEach(b => { try { b.click(); } catch(e){} });
          }
        }
    """
