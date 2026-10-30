"""law.go.kr 상세 페이지 Playwright 공통 동작 — 로딩 마스크를 기다려 HTML 을 얻고, 실패 시 화면·HTML 을 남긴다."""
import os

from scripts.core.run_report import retry_detail_fetch_async

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


async def load_rendered_page(page, url: str, logger, *, goto_timeout: int,
                             mask_visible_timeout: int, mask_hidden_timeout: int) -> str:
    """페이지로 이동해 로딩 마스크가 사라질 때까지 기다린 뒤 렌더링된 HTML 을 돌려준다(마스크가 안 보여도 계속 진행)."""
    logger.info(f"페이지로 이동 중: {url}")
    await retry_detail_fetch_async(
        lambda: page.goto(url, wait_until='domcontentloaded', timeout=goto_timeout))
    try:
        logger.info("로딩 마스크가 나타나기를 기다리는 중...")
        await page.wait_for_selector(".loadmask-msg", state="visible", timeout=mask_visible_timeout)
        logger.info("로딩 마스크가 사라지기를 기다리는 중...")
        await page.wait_for_selector(".loadmask-msg", state="hidden", timeout=mask_hidden_timeout)
    except Exception:
        logger.warning("로딩 마스크가 감지되지 않았습니다. 페이지 로드가 완료된 것으로 간주하고 계속 진행합니다.")
    logger.info("페이지 최종 렌더링을 잠시 대기합니다...")
    await page.wait_for_timeout(1000)
    logger.info("페이지 HTML 컨텐츠를 가져오는 중...")
    return await page.content()


async def dump_error_artifacts(page, output_name: str, project_root: str = PROJECT_ROOT) -> tuple[str, str]:
    """실패 시점의 스크린샷과 HTML 을 <project_root>/debug 에 저장하고 두 경로를 돌려준다."""
    error_dir = os.path.join(project_root, 'debug')
    if not os.path.exists(error_dir):
        os.makedirs(error_dir)
    screenshot_path = os.path.join(error_dir, f'{output_name}_error.png')
    html_path = os.path.join(error_dir, f'{output_name}_error.html')
    await page.screenshot(path=screenshot_path)
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(await page.content())
    return screenshot_path, html_path
