"""The web API: REST reads over the event store, plus a WebSocket relay of live
Home Assistant events. Read-only, always — ingestion (`hia ingest`) is a separate
process; see hia.ingest.store's module docstring for why.
"""
