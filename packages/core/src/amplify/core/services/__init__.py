"""Pure-logic services that the API and the worker both depend on.

Anything in here must avoid imports from ``app.*`` (the API package)
because the worker container does not ship the API code.
"""
