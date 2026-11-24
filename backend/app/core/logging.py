import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger("technical_rag")


def event(name: str, **fields):
    logger.info(json.dumps({
        "event": name, "at": datetime.now(timezone.utc).isoformat(), **fields,
    }, ensure_ascii=False))


def configure():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
