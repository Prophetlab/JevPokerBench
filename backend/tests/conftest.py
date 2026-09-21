"""Importing the ASGI module during tests must never open a real match database."""
import os
import socket
from tempfile import TemporaryDirectory

import pytest

_test_data = TemporaryDirectory(prefix='pokerbench-pytest-')
os.environ['POKERBENCH_DATA_DIR'] = _test_data.name


@pytest.fixture(autouse=True)
def no_external_connections(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Tests must stub outbound connections; no real model calls are allowed')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)


def pytest_sessionfinish(session, exitstatus):
    _test_data.cleanup()
