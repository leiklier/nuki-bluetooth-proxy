"""Shared fixtures for the Nuki Opener BLE tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import PropertyMock, patch

from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import format_mac
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nuki_opener_ble.const import CONF_CREDENTIALS, DOMAIN

from .bluetooth_utils import inject_opener_advertisement
from .nuki.fake_device import (
    DEFAULT_ADDRESS,
    FakeEnvironment,
    FakeOpener,
    patch_establish_connection,
)
from .nuki.test_client import make_credentials


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    """Enable loading custom integrations in all tests."""
    yield


@pytest.fixture
def expected_lingering_timers(request: pytest.FixtureRequest) -> bool:
    """Downgrade lingering-timer failures to warnings for HA-level tests.

    The enable_bluetooth fixture's mocked scanner never stops cleanly (the
    scanner classes are cython and unpatchable), leaving its device-expiry
    timer behind even for an empty test. HA core waives this check for its
    own component tests the same way (see the pytest plugin's path-based
    default). The pure protocol tests under tests/nuki keep the strict check.
    """
    return request.node.path.parent.name != "nuki"


@pytest.fixture
def entity_registry_enabled_by_default() -> Generator[None]:
    """Enable registry entities that are disabled by default (HA core fixture)."""
    with patch(
        "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
        PropertyMock(return_value=True),
    ):
        yield


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch) -> FakeEnvironment:
    """A simulated Nuki Opener reachable through a patched BLE stack."""
    env = FakeEnvironment(opener=FakeOpener())
    patch_establish_connection(monkeypatch, env)
    return env


@pytest.fixture
def config_entry(environment: FakeEnvironment) -> MockConfigEntry:
    """A config entry paired with the simulated opener."""
    credentials = make_credentials(environment.opener)
    return MockConfigEntry(
        domain=DOMAIN,
        title="Front Door",
        unique_id=format_mac(DEFAULT_ADDRESS),
        data={
            CONF_ADDRESS: DEFAULT_ADDRESS,
            CONF_CREDENTIALS: credentials.to_dict(),
        },
    )


async def setup_entry(hass: HomeAssistant, config_entry: MockConfigEntry) -> MockConfigEntry:
    """Add the opener advertisement and set up the config entry."""
    config_entry.add_to_hass(hass)
    inject_opener_advertisement(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
