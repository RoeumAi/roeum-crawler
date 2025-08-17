import logging
import os
from datetime import datetime

def get_logger(name: str):
    # 없으면 현재 시간으로 기본값 설정 (개별 테스트용)
    run_id = os.getenv('SCRAPER_RUN_ID', datetime.now().strftime('%Y%m%d-%H%M%S'))

    scraper_type = name.split('.')[1] if '.' in name else 'general' # 'scripts.law.scraper' -> 'law'
    log_dir = os.path.join('logs', scraper_type, run_id)

    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    if logger.hasHandlers():
        return logger

    formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(name)s] - %(message)s')

    # 콘솔 핸들러는 그대로 유지
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 일반 로그와 에러 로그를 하나의 파일에 레벨별로 기록하는 것이 더 효율적일 수 있음
    # 여기서는 실행별로 파일을 분리
    file_handler = logging.FileHandler(os.path.join(log_dir, 'run.log'), encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    error_file_handler = logging.FileHandler(os.path.join(log_dir, 'error.log'), encoding='utf-8')
    error_file_handler.setLevel(logging.WARNING)
    error_file_handler.setFormatter(formatter)
    logger.addHandler(error_file_handler)

    return logger