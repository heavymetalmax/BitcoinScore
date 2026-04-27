"""Profit Taking metric — now sourced from SOPR (Spent Output Profit Ratio).

Delegates to scraper.sopr.get_sopr().
Kept for backward compatibility with scraper.py imports.
"""
from scraper.sopr import get_sopr


def get_profit_taking():
    """Return the current adjusted SOPR value (float near 0, or None).

    Negative = selling at loss (bullish), Positive = selling at profit (bearish).
    """
    return get_sopr()


__all__ = ['get_profit_taking']

