import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import GOTIFY_PRIORITY, GOTIFY_TOKEN, GOTIFY_URL

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 10


class GotifyError(Exception):
    """Push failed. retryable=True only when the message was surely not delivered."""

    def __init__(self, message: str, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


def _is_timeout(reason: object) -> bool:
    return isinstance(reason, TimeoutError)


def send_message(title: str, message: str, priority: int = GOTIFY_PRIORITY) -> None:
    """POST a message to Gotify (`/message`, app token in `X-Gotify-Key`)."""
    if not GOTIFY_URL or not GOTIFY_TOKEN:
        raise GotifyError("Gotify is not configured", retryable=False)
    body = json.dumps(
        {"title": title, "message": message, "priority": priority}
    ).encode("utf-8")
    request = Request(
        f"{GOTIFY_URL}/message",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "X-Gotify-Key": GOTIFY_TOKEN},
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status = response.status
    except HTTPError as error:
        # The server answered; 5xx and 429 mean it did not store the message.
        retryable = error.code >= 500 or error.code == 429
        error.close()
        logger.error(
            "Gotify rejected message",
            extra={"status": error.code, "title": title, "retryable": retryable},
        )
        raise GotifyError(
            f"Gotify returned HTTP {error.code}", retryable=retryable
        ) from error
    except URLError as error:
        # A timeout is ambiguous: the server may have stored the message already.
        retryable = not _is_timeout(error.reason)
        logger.error(
            "Gotify request failed",
            extra={"reason": str(error.reason), "title": title, "retryable": retryable},
        )
        raise GotifyError(
            f"Gotify request failed: {error.reason}", retryable=retryable
        ) from error
    except TimeoutError as error:
        logger.error("Gotify request timed out", extra={"title": title})
        raise GotifyError("Gotify request timed out", retryable=False) from error
    except OSError as error:
        # Connection reset after sending may already have delivered the message.
        logger.error(
            "Gotify connection error", extra={"reason": str(error), "title": title}
        )
        raise GotifyError(
            f"Gotify connection error: {error}", retryable=False
        ) from error
    logger.info("Gotify message sent", extra={"status": status, "title": title})
