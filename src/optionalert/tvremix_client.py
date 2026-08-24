"""Minimal MCP-over-HTTP client for tvremix.xyz (TradingView data via MCP).

Used only as a corroborating IV/Greeks source for the single contract behind
an alert that's already about to fire - tvremix doesn't expose options
volume/open-interest at all (checked directly, see README known
limitations), so it can never replace yfinance/Deribit as the primary data
source, only refine the IV leg of the score right before sending.

Best-effort by design: TVREMIX_API_KEY is optional. Any missing key, network
error, rate limit, or unexpected response shape just returns None, and
callers fall back to the original (yfinance/Deribit) IV - exactly the
behavior before this integration existed. This client is unverified against
a real API key (none was available while writing it) and should be smoke-
tested against a live key before being trusted.
"""

import itertools
import json
import logging
import os

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://tvremix.xyz/api/mcp/v1"
_TIMEOUT = 10
_ids = itertools.count(1)


def _post(payload: dict) -> dict | None:
    token = os.environ.get("TVREMIX_API_KEY")
    if not token:
        return None

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    try:
        resp = requests.post(_BASE_URL, headers=headers, json=payload, timeout=_TIMEOUT)
        if not resp.ok:
            # Same lesson as telegram_client.py: raise_for_status() alone
            # drops the response body, which is where the real reason lives
            # (bad key, wrong scope, rate limit) - log it so a first-time
            # setup issue is diagnosable from CI logs, not just "400/401".
            logger.warning("tvremix request failed (%s): %s", resp.status_code, resp.text[:500])
            return None
        return resp.json()
    except Exception as exc:
        logger.warning("tvremix request failed: %s", exc)
        return None


def _call_tool(name: str, arguments: dict) -> dict | None:
    """Calls one MCP tool and returns its parsed JSON result, or None on any
    failure/unexpected shape. Tries a few plausible MCP response shapes since
    this is unverified against tvremix's actual server behavior."""
    response = _post({
        "jsonrpc": "2.0",
        "id": next(_ids),
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    if response is None:
        return None
    if "error" in response:
        logger.warning("tvremix tool error for %s: %s", name, response["error"])
        return None

    result = response.get("result")
    if result is None:
        return None

    # Standard MCP: result.content is a list of blocks; text blocks carry a
    # JSON string. Some simplified servers instead put the tool's return
    # value directly under result (or result.structuredContent).
    content = result.get("content") if isinstance(result, dict) else None
    if content:
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                try:
                    return json.loads(block["text"])
                except (json.JSONDecodeError, TypeError):
                    continue

    if isinstance(result, dict) and "structuredContent" in result:
        return result["structuredContent"]

    if isinstance(result, dict) and ("success" in result or "data" in result):
        return result

    return None


def refine_iv(tvremix_symbol: str, expiry_iso: str, strike: float, option_kind: str) -> float | None:
    """Returns implied volatility as a decimal fraction (0.45 = 45%) for the
    given contract per tvremix, or None if unavailable for any reason.

    option_kind: 'call' or 'put' (lowercase, matches tvremix's convention).
    """
    if not tvremix_symbol:
        return None

    data = _call_tool("get_option_chain", {
        "symbol": tvremix_symbol,
        "expiration": expiry_iso,
        "option_type": option_kind,
        "min_strike": strike,
        "max_strike": strike,
        "limit": 5,
    })
    if not data or not data.get("success"):
        return None

    contracts = data.get("data", {}).get(f"{option_kind}s", [])
    for c in contracts:
        if abs(c.get("strike", -1) - strike) < 1e-6:
            iv = c.get("iv")
            if iv is not None:
                return float(iv) / 100.0
    return None


def resolve_symbol(ticker: str) -> str | None:
    """Best-effort resolution of a bare ticker (e.g. 'XOM') to tvremix's
    required 'EXCHANGE:TICKER' form (e.g. 'NYSE:XOM'). Prefers a primary US
    listing; returns None if no confident match is found."""
    _US_EXCHANGES = ("NYSE", "NASDAQ", "AMEX", "ARCA", "BATS", "CBOE")
    _USABLE_TYPES = ("stock", "fund", "etf")

    data = _call_tool("search_symbols", {"query": ticker, "limit": 10})
    if not data or not data.get("success"):
        return None

    symbols = data.get("data", {}).get("symbols", [])
    for s in symbols:
        symbol = s.get("symbol", "")
        prefix = symbol.split(":", 1)[0] if ":" in symbol else ""
        if prefix in _US_EXCHANGES and s.get("type") in _USABLE_TYPES:
            return symbol
    return None
