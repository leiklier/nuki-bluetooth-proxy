"""Tests for reading and writing the advanced configuration."""

import dataclasses

import pytest

from custom_components.nuki_opener_ble.nuki.errors import NukiBadPinError
from custom_components.nuki_opener_ble.nuki.messages import AdvancedConfig

from .fake_device import FakeEnvironment, FakeOpener, patch_establish_connection
from .test_client import make_client, make_credentials
from .test_device import make_device


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch) -> FakeEnvironment:
    env = FakeEnvironment(opener=FakeOpener())
    patch_establish_connection(monkeypatch, env)
    return env


def test_parse_serialize_roundtrip() -> None:
    opener = FakeOpener()
    payload = opener.advanced_config.serialize()
    assert len(payload) == AdvancedConfig._SIZE
    assert AdvancedConfig.parse(payload) == opener.advanced_config


def test_suppression_bit_properties() -> None:
    config = dataclasses.replace(FakeOpener().advanced_config, doorbell_suppression=0x05)
    assert config.suppress_ring is True  # bit 2
    assert config.suppress_rto is False  # bit 1
    assert config.suppress_cm is True  # bit 0


async def test_client_get_and_set_advanced_config(environment: FakeEnvironment) -> None:
    credentials = make_credentials(environment.opener)
    client = make_client(environment, credentials)
    config = await client.get_advanced_config()
    assert config == environment.opener.advanced_config

    updated = dataclasses.replace(config, sound_level=10)
    await client.set_advanced_config(updated, pin=1234)
    assert environment.opener.advanced_config.sound_level == 10
    await client.disconnect()


async def test_client_set_advanced_config_bad_pin(environment: FakeEnvironment) -> None:
    credentials = make_credentials(environment.opener)
    client = make_client(environment, credentials)
    config = await client.get_advanced_config()
    with pytest.raises(NukiBadPinError):
        await client.set_advanced_config(config, pin=1)
    assert environment.opener.advanced_config.sound_level == 80
    await client.disconnect()


async def test_device_change_requires_pin(environment: FakeEnvironment) -> None:
    device = make_device(environment)  # no PIN configured
    with pytest.raises(NukiBadPinError):
        await device.change_advanced_config(sound_level=1)
    await device.client.disconnect()


async def test_device_update_fetches_advanced_config(environment: FakeEnvironment) -> None:
    device = make_device(environment)
    await device.update()
    assert device.advanced_config == environment.opener.advanced_config
    await device.client.disconnect()
