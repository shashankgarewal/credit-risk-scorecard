import os
import logging
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()
is_dev_env = bool(os.getenv('DEV_ENV'))

if is_dev_env:
    log_dir = os.path.join(os.getcwd(), 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{datetime.now().date()}.log")
else:
    log_path = None
    
logging.basicConfig(
    filename=log_path,
    level=logging.DEBUG,
    format='%(asctime)s | %(levelname)s | %(filename)s | %(funcName)s:%(lineno)d | %(message)s',
)