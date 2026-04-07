import logging
import os
from datetime import datetime


def _build_logger(system_name: str = "bible_copilot") -> logging.Logger:
    os.makedirs(".logs", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = f".logs/{system_name}_{timestamp}.log"

    logger = logging.getLogger(system_name)
    logger.setLevel(logging.DEBUG)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s"))

    logger.addHandler(fh)
    return logger


LOGGER = _build_logger()
