#!/usr/bin/env python3
"""
Search-engine query URL construction.

Shared by the proxy's --dump mode (proxy_server) and the in-page search form
(proxy_handler) so the engine list lives in exactly one place.
"""

SEARCH_ENGINES = {
    'google': 'https://www.google.com/search?q=',
    'bing': 'https://www.bing.com/search?q=',
    'duckduckgo': 'https://duckduckgo.com/?q=',
}


def build_search_url(query, engine='duckduckgo'):
    """Build a search-results URL for the given query and engine.

    Unknown engines fall back to DuckDuckGo.
    """
    base = SEARCH_ENGINES.get(engine, SEARCH_ENGINES['duckduckgo'])
    return base + query.replace(' ', '+')
