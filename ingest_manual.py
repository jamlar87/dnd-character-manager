#!/usr/bin/env python3
"""CLI entry for the D&D manual ingestion engine.

The engine lives in the ingest_manual/ package; this file keeps the command line stable:
  python3 ingest_manual.py --list          List manuals and extraction status
  python3 ingest_manual.py "Volo's Guide"  Deep-sweep a specific manual
  python3 ingest_manual.py --all           Process all un-extracted manuals
  python3 ingest_manual.py --merge         Rebuild merged data from extractions
"""
from ingest_manual import main

if __name__ == "__main__":
    main()
