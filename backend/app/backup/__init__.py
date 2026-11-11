"""Backup monitoring module — Veritas NetBackup over its read-only REST API.

client.py         REST client (API key or login token), pagination, payload parsers
demo_generator.py synthetic NetBackup API payloads (NETBACKUP_i_DEMO=true)
collector.py      sync one primary server into the database (jobs upsert, policy/storage snapshots)
status_codes.py   NetBackup status codes → Vietnamese description + failure cause group
analysis.py       KPIs, client RPO status, policy/storage health, VM coverage, findings
"""
