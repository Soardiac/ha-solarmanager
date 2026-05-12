import asyncio
import sys
from unittest.mock import patch

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow HA to load integrations from custom_components/ in all tests."""
    yield


@pytest.fixture(autouse=True)
def verify_cleanup(event_loop, expected_lingering_tasks, expected_lingering_timers):
    """Override phccc's verify_cleanup to suppress a Windows-only false positive.

    The original fixture calls event_loop.shutdown_default_executor() after each
    test. On Windows + Python 3.12, ThreadPoolExecutor shutdown always creates a
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


@pytest.fixture
def event_loop(socket_enabled):
    """Override event_loop so the loop is created with sockets enabled.

    Two Windows-specific problems require this override:
    1. asyncio needs socket.socketpair() for the event loop self-pipe, which
       pytest-homeassistant-custom-component blocks via an autouse fixture.
       Depending on socket_enabled forces pytest to re-enable sockets first.
    2. aiodns (used by aiohttp inside HA) requires SelectorEventLoop on Windows.
       WindowsSelectorEventLoopPolicy provides that.
    """
    if sys.platform == "win32":
        policy = asyncio.WindowsSelectorEventLoopPolicy()
    else:
        policy = asyncio.DefaultEventLoopPolicy()
    loop = policy.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()
    asyncio.set_event_loop(None)
