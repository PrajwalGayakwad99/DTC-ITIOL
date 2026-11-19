"""Small, testable HTTP client helpers used by the dashboard."""

import httpx


def fetch_json(
    api_base: str,
    path: str,
    params: dict | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
) -> tuple[object | None, str | None]:
    """Fetch JSON and return a user-displayable error instead of raising."""
    try:
        with httpx.Client(transport=transport, timeout=3.0) as client:
            response = client.get(f"{api_base.rstrip('/')}{path}", params=params)
            response.raise_for_status()
            return response.json(), None
    except (httpx.HTTPError, ValueError) as exc:
        return None, str(exc)
