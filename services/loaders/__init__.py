"""Loader package: one module per responsibility behind data_loader's surface.

files       disk JSON reads + cache
sources     normalisation / enrichment rules
races       race + subrace merge
spells      spell merge
indexes     magic items + ITEM_INDEX
feats       feat merge
backgrounds background merge
subclasses  subclass + feature merge
limited_use limited-use registry fill-in

services/data_loader.py re-exports these names, so existing imports keep working.
"""
