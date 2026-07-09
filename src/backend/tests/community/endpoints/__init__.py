"""Endpoint test cases for the per-endpoint DI framework.

Any ``test_*.py`` file dropped under this tree is auto-imported by
``conftest.py`` so its ``@endpoint_test`` decorators fire before pytest
collects the parametrized runner. Authors do not register cases
anywhere else — the file's presence is the registration.
"""
