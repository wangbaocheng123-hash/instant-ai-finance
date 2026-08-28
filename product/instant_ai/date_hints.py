from __future__ import annotations

import re
from datetime import UTC, datetime


_TRAILING_DATE = re.compile(
    r"(?<!\d)(?P<year>19[89]\d|20\d{2})"
    r"[年./-](?P<month>0?[1-9]|1[0-2])"
    r"[月./-](?P<day>0?[1-9]|[12]\d|3[01])日?(?!\d)"
)
_TRAILING_DECORATION = " \t\r\n·|/\\-—_.,，。;；:：()（）[]【】<>《》"


def infer_embedded_published_at(*values: str, now: datetime | None = None) -> str | None:
    """Infer a missing feed date only when a date appears at the text tail.

    Some official feeds return their entire historical index without a pubDate,
    while the article date is still printed at the end of the title/summary.
    Restricting the hint to the final decoration avoids treating an arbitrary
    historical date mentioned inside a current article as its publication date.
    """

    current = (now or datetime.now(UTC)).astimezone(UTC)
    for value in values:
        tail = (value or "").strip()[-240:]
        for match in reversed(list(_TRAILING_DATE.finditer(tail))):
            suffix = tail[match.end():].strip(_TRAILING_DECORATION)
            if suffix:
                continue
            try:
                candidate = datetime(
                    int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")),
                    tzinfo=UTC,
                )
            except ValueError:
                continue
            if candidate <= current:
                return candidate.isoformat()
    return None
