"""Structured logging with colour and tqdm compatibility."""
import logging
import sys
from pathlib import Path
from datetime import date
from tqdm import tqdm


class TqdmHandler(logging.StreamHandler):
    """Log handler that writes through tqdm.write to avoid bar corruption."""
    def emit(self, record):
        try:
            tqdm.write(self.format(record), file=sys.stdout)
        except Exception:
            self.handleError(record)


def setup_logger(name: str = "viral_pipeline",
                 log_dir: str = "logs",
                 level: int = logging.INFO) -> logging.Logger:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_file = Path(log_dir) / f"pipeline_{date.today()}.log"

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)-20s  %(message)s",
        datefmt="%H:%M:%S",
    )

    fh = logging.FileHandler(log_file)
    fh.setFormatter(fmt)

    sh = TqdmHandler(sys.stdout)
    sh.setFormatter(fmt)

    logger = logging.getLogger(name)
    logger.setLevel(level)
    if not logger.handlers:
        logger.addHandler(fh)
        logger.addHandler(sh)

    return logger


def get_logger(module: str) -> logging.Logger:
    return logging.getLogger(f"viral_pipeline.{module}")
