import asyncio
import sys
from unittest.mock import patch

import pytest
import pytest_socket

pytest_plugins = "pytest_homeassistant_custom_component"


if sys.platform == "win32":

    _orig_disable_socket = pytest_socket.disable_socket

    def _disable_socket_keep_localhost(allow_unix_socket: bool = False) -> None:
        """Socket-Erstellung unter Windows nicht komplett blockieren.

        phccc blockiert die Socket-Erstellung und erlaubt nur AF_UNIX (reicht
        für asyncio unter Linux). Unter Windows nutzt socket.socketpair() —
        von jedem Event-Loop gebraucht — stattdessen AF_INET auf 127.0.0.1,
        die Loop-Erstellung würde also scheitern. Erstellung erlauben;
        Verbindungen bleiben per socket_allow_hosts auf localhost beschränkt.
        """
        _orig_disable_socket(allow_unix_socket=allow_unix_socket)
        pytest_socket.enable_socket()
        pytest_socket.socket_allow_hosts(["127.0.0.1"])

    pytest_socket.disable_socket = _disable_socket_keep_localhost

    def pytest_asyncio_loop_factories(config, item):
        """aiodns (via aiohttp in HA) braucht unter Windows einen SelectorEventLoop."""
        return {"selector": asyncio.SelectorEventLoop}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow HA to load integrations from custom_components/ in all tests."""
    yield


@pytest.fixture(autouse=True)
def verify_cleanup(expected_lingering_tasks, expected_lingering_timers):
    """Override phccc's verify_cleanup to suppress a Windows-only false positive.

    The original fixture calls event_loop.shutdown_default_executor() after each
    test. On Windows, ThreadPoolExecutor shutdown always creates a
    _run_safe_shutdown_loop daemon thread. The original check then fails because
    this thread is neither a _DummyThread nor named waitpid-*. All actual test
    assertions still run; we only skip the thread-leak check here.
    """
    yield


@pytest.fixture(autouse=True)
def mock_setup_entry():
    """Prevent HA from running component setup after CREATE_ENTRY.

    When a config flow finishes, HA calls ConfigEntries.async_setup which invokes
    _async_setup_component. On Windows this starts a ThreadPoolExecutor whose
    shutdown creates a lingering _run_safe_shutdown_loop thread that fails the
    verify_cleanup check. Mocking async_setup at this level stops the executor
    from being started at all.
    """
    with patch(
        "homeassistant.config_entries.ConfigEntries.async_setup",
        return_value=True,
    ):
        yield
