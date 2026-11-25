# BTC extremes reference

Research reference, prepared 2026-11-25. Nothing here is loaded by the production index or allocator.

## Two different meanings of an extreme

1. `btc_extrema_events.csv`: a sparse, attributed registry of well-known historical highs and lows, intended for event-window evaluation. Dates follow the stated source and price basis. They are not a complete list of all turning points. The table deliberately does not reproduce third-party price histories or price columns.
2. `build_btc_extrema_reference.py`: computes consistent daily-close turning points from a price file supplied locally. The default 10%, 20%, and 40% reversals give short/intermediate/large-swing views. These are transparent research granularities, not optimized settings and not definitions of economic market cycles. A large swing need not be a full cycle.

## Historical table limitations

A daily closing high is not an intraday high. Different venues, aggregates and day cutoffs can give different dates. For example, a source using a composite daily series places the 2022 closing low on November 9, whereas the cited Bitstamp-close table places it on November 21. Neither should silently replace the other. Event identifiers keep these distinct.

The 2011 events predate a complete Bitstamp sample. The 2025 high does not establish a completed cycle. No current 2026 bottom is asserted. The published drawdown table was last updated on November 8, 2026 and is not a current-market feed. The existing `data/cycle_extremes.json` is not changed or certified by this reference.

## Reproduce shorter swings locally

From the repository root:

```sh
python docs/build_btc_extrema_reference.py --input data/history/btc_price_history.json --output-dir /tmp/btc-extrema --cutoff 2026-11-24
python docs/build_btc_extrema_reference.py --self-test
```

Input can be CSV (`date,close`, with optional `open,high,low,volume`) or JSON: a list of rows, or an object with `series`. Dates must be unique UTC calendar dates; prices must be positive. `close` or `btc_price` is accepted. No network request is made. The input's owner must establish data-use rights. The existing repository Binance history may end well before the chosen cutoff; the output manifest reports the actual end, never fills missing data or pretends it is current.

Outputs: `swings_10pct.csv`, `swings_20pct.csv`, `swings_40pct.csv`, `annual_close_extrema.csv`, and `manifest.json`. All prices use the same supplied daily-close series. Annual records are calendar-year extrema, not cycle labels. A year is marked complete only if January 1 through December 31 are present with no daily gaps.

## No look-ahead in strategy evaluation

`extreme_date` is a retrospective location, not a date when a trader knew it was a turn. `confirmed_on` is the first later daily close reversing by the threshold; `available_after_utc` is the next UTC midnight, after that candle closes. A strategy may use a confirmed event only then, with execution at a genuinely later available price and realistic costs. Initial sample-edge events are marked `left_censored`; the final candidate has no confirmation and can move. Do not use either as a confirmed trading signal.

The sparse historical registry has no causal confirmation rule and is evaluation-only. Do not join it into training inputs, use it to set adaptive weights, or report performance at its hindsight turning-point prices. For supervised research, define labels and maturity rules separately, split time before tuning, and keep evaluation events out of training/tuning. Use all eligible windows, not only famous successful turns. These files do not validate an index, choose a strategy or change production behavior.

## Sources and data-use boundaries

`btc_extrema_sources.json` records the sources, dates/bases, contradictions and limitations. Only a small factual event registry is stored, not copied source prose, charts, OHLC series or a full extracted third-party dataset. Attribution is not a claim of an open license.

The Bitstamp API documentation says companies seeking commercial use should obtain a commercial Data License Agreement, and expressly includes calculations/statistics among commercial uses. We have not established a commercial license. Accordingly, no Bitstamp raw snapshot or full computed Bitstamp price table is distributed here. The local generator is source-agnostic. A private copy alone would not settle licensing either.

Coin Metrics' community-data repository uses CC BY-NC 4.0, which is not an unrestricted commercial-use alternative. Kaggle mirrors labeled CC0 were found, but upstream ownership/venue semantics were not established, so they were not used as a shortcut.
