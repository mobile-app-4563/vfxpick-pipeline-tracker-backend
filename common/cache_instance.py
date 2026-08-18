"""App-wide cache instance.

Lives in its OWN module (not app.py) on purpose:

Starting the dev server via ``python app.py`` causes Python to load app.py
TWICE — once as ``__main__`` and once as ``app`` (because route modules do
``from app import cache``).  That created two ``flask_caching.Cache`` objects:
``create_app()`` initialized the ``__main__`` one, but routes used the ``app``
one, which was never registered in ``current_app.extensions['cache']`` → a
``KeyError`` whose str() is the repr of the uninitialized Cache object (seen as
``"detail": "<flask_caching.Cache object at 0x...>"`` in error responses).

By keeping the singleton here, it is always imported as
``common.cache_instance`` regardless of how the server is started, so exactly
one Cache object exists and ``init_app`` always targets it.
"""

from flask_caching import Cache

cache = Cache(
    config={
        "CACHE_TYPE": "SimpleCache",
        "CACHE_DEFAULT_TIMEOUT": 300,
    }
)
