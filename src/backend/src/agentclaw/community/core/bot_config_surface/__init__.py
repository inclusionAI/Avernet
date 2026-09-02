"""The rules the public config surface enforces, named in one place.

Deliberately empty of imports. ``coords`` is a leaf that the five category
homes import, and ``table`` imports *from* those homes — so re-exporting either
here would make importing the leaf trigger the table, and the cycle that
follows is not worth the two saved keystrokes at each call site. Import
:mod:`.coords` and :mod:`.table` by name.
"""
