"""Shared primitives extracted out of main.py.

Nothing in this package may import ``main``: routes/, services/ and
pdf_generator.py all import from here, and main.py imports them.  Keeping the
dependency arrow one-way (core -> data/services, never core -> main) is what
makes ``import routes.dm`` work without the FastAPI app having been built.
"""
