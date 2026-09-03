"""Streamlit reader for completed NIFTY volatility snapshots.

The reader deliberately depends on the :class:`SnapshotRepository` protocol
defined in this package.  It contains no exchange client and never contacts
NSE.
"""
