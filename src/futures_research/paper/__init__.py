"""Local, app-owned paper-trading runtime.

The package deliberately has no broker order transport.  Interactive Brokers is
used only by :mod:`futures_research.paper.ib_market_data` as a read-only market
data source.
"""
