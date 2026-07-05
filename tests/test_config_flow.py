"""Tests for the config flow."""

from homeassistant import config_entries
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.device_registry import format_mac
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nuki_opener_ble.const import CONF_CREDENTIALS, DOMAIN
from custom_components.nuki_opener_ble.nuki import NukiOpenerCredentials

from .bluetooth_utils import inject_opener_advertisement, make_opener_service_info
from .nuki.fake_device import DEFAULT_ADDRESS, FakeEnvironment


async def start_discovery_flow(hass: HomeAssistant) -> str:
    """Inject the opener beacon and return the discovery flow's id.

    Injecting the advertisement makes Home Assistant start a discovery flow
    itself (our manifest matchers match the beacon). Initiating a second flow
    manually would race it and abort with already_in_progress, so continue
    the automatic flow when it exists.
    """
    inject_opener_advertisement(hass)
    # The discovery flow is created in a background task.
    await hass.async_block_till_done(wait_background_tasks=True)
    if flows := hass.config_entries.flow.async_progress_by_handler(DOMAIN):
        assert len(flows) == 1
        return flows[0]["flow_id"]
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=make_opener_service_info(),
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "choose_method"
    return result["flow_id"]


async def test_bluetooth_discovery_and_pair(
    hass: HomeAssistant, enable_bluetooth: None, environment: FakeEnvironment
) -> None:
    """Full happy path: discovery, menu, pairing."""
    flow_id = await start_discovery_flow(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, {"next_step_id": "pair"})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pair"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Nuki_Opener_11223344"
    assert result["data"][CONF_ADDRESS] == DEFAULT_ADDRESS
    credentials = NukiOpenerCredentials.from_dict(result["data"][CONF_CREDENTIALS])
    assert credentials.shared_key == environment.opener.shared_key
    assert environment.opener.paired_name == "Home Assistant"


async def test_bluetooth_discovery_pair_not_in_pairing_mode(
    hass: HomeAssistant, enable_bluetooth: None, environment: FakeEnvironment
) -> None:
    """Pairing shows an error and can be retried if pairing mode is off."""
    environment.opener.pairing_mode = False
    flow_id = await start_discovery_flow(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, {"next_step_id": "pair"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "not_in_pairing_mode"}

    # User presses the opener button and retries.
    environment.opener.pairing_mode = True
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_bluetooth_discovery_rejects_non_opener(
    hass: HomeAssistant, enable_bluetooth: None
) -> None:
    """A non-opener device aborts the discovery flow."""
    info = make_opener_service_info()
    # Replace the beacon with a smart lock UUID.
    info.manufacturer_data[76] = (
        bytes.fromhex("0215") + bytes.fromhex("a92ee200550111e4916c0800200c9a66") + bytes(5)
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=info,
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


async def test_bluetooth_discovery_already_configured(
    hass: HomeAssistant, enable_bluetooth: None, config_entry: MockConfigEntry
) -> None:
    """Discovery aborts for an already configured opener."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=make_opener_service_info(),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_user_flow_pair(
    hass: HomeAssistant, enable_bluetooth: None, environment: FakeEnvironment
) -> None:
    """User-initiated flow lists discovered openers."""
    inject_opener_advertisement(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: DEFAULT_ADDRESS}
    )
    assert result["type"] is FlowResultType.MENU

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "pair"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ADDRESS] == DEFAULT_ADDRESS


async def test_user_flow_no_devices(hass: HomeAssistant, enable_bluetooth: None) -> None:
    """User flow aborts when nothing was discovered."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_manual_credentials_flow(
    hass: HomeAssistant, enable_bluetooth: None, environment: FakeEnvironment
) -> None:
    """Entering existing credentials creates an entry without pairing."""
    flow_id = await start_discovery_flow(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, {"next_step_id": "manual"})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"

    # Malformed input shows an error.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "private_key": "zz",
            "device_public_key": "00" * 32,
            "auth_id": "02000000",
            "app_id": "42",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_credentials"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "private_key": "11" * 32,
            "device_public_key": environment.opener.public_key.hex(),
            "auth_id": "02000000",
            "app_id": "42",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    credentials = NukiOpenerCredentials.from_dict(result["data"][CONF_CREDENTIALS])
    assert credentials.private_key == bytes.fromhex("11" * 32)
    assert credentials.app_id == 42


async def test_unique_id_is_formatted_mac(
    hass: HomeAssistant, enable_bluetooth: None, environment: FakeEnvironment
) -> None:
    """The config entry unique id is the formatted MAC address."""
    flow_id = await start_discovery_flow(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, {"next_step_id": "pair"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    entry = result["result"]
    assert entry.unique_id == format_mac(DEFAULT_ADDRESS)
