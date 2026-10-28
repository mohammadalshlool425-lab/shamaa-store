"""Optional HTTP-only WSGI adapter, with a per-process, lazily-created ASGI loop.

Install requirements-pythonanywhere.lock before using it. Creating the adapter
at WSGI-file import time can lose its loop thread when uWSGI forks a worker.
The normal Uvicorn entrypoint is unchanged; WebSockets/lifespan are not used here.
"""
from os import getpid
from threading import Lock
from a2wsgi import ASGIMiddleware
from .application import create_app


class WSGIApplication:
    def __init__(self, app, *, loop=None):
        self.app = app
        self.loop = loop
        self._pid = getpid()
        self._adapter = None
        self._lock = Lock()

    def __call__(self, environ, start_response):
        pid = getpid()
        if self._pid != pid:
            # Do not reuse a parent process's loop, adapter, or thread lock.
            self._adapter = None
            self.loop = None
            self._lock = Lock()
            self._pid = pid
        with self._lock:
            if self._adapter is None:
                self._adapter = ASGIMiddleware(self.app,wait_time=20,loop=self.loop)
                self.loop = self._adapter.loop
        return self._adapter(environ,start_response)


def create_wsgi_app(settings=None, *, loop=None):
    # DB initialization is explicit in the app factory, not an ASGI lifespan hook.
    return WSGIApplication(create_app(settings),loop=loop)
