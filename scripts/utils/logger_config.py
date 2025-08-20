import logging
import os
from datetime import datetime

def get_logger(name: str, scraper_type: str = 'general'):
    """
    스크레이퍼 타입과 실행 ID에 따라 동적으로 로그 파일을 생성하는 로거를 반환합니다.
    """
    run_id = os.getenv('SCRAPER_RUN_ID', datetime.now().strftime('%Y%m%d-%H%M%S'))

    log_dir = os.path.join('logs', scraper_type, run_id)

    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    if logger.hasHandlers():
        return logger

    formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(name)s] - %(message)s')

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    file_handler = logging.FileHandler(os.path.join(log_dir, 'run.log'), encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    error_file_handler = logging.FileHandler(os.path.join(log_dir, 'error.log'), encoding='utf-8')
    error_file_handler.setLevel(logging.WARNING)
    error_file_handler.setFormatter(formatter)
    logger.addHandler(error_file_handler)

    return logger