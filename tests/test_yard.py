"""Tests for the yard map: zone outlines saved from the sidebar panel."""

from __future__ import annotations

from typing import Any

import jwt
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser

from custom_components.rainbird_extended.const import DOMAIN
from custom_components.rainbird_extended.panel import (
    _panel_config,
    async_setup_panel_api,
)
from custom_components.rainbird_extended.yard import (
    YardStore,
    apple_token,
    area_m2,
    async_get_yard_store,
    clean_apple,
    clean_shape,
    default_background,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import RAINBIRD_UNIQUE_ID

# About 10 m by 10 m at 45° north.
SQUARE = [
    [45.0, -100.0],
    [45.0, -99.9998728],
    [45.0000899, -99.9998728],
    [45.0000899, -100.0],
]


def test_shapes_are_checked_and_rounded() -> None:
    assert clean_shape([[1.123456789, 2], [3, 4], [5, 6]]) == [
        [1.1234568, 2.0],
        [3.0, 4.0],
        [5.0, 6.0],
    ]
    for bad in (
        [[1, 2], [3, 4]],
        [[1, 2], [3, 4], [91, 6]],
        [[1, 2], [3, 4], [5]],
        [[1, 2], [3, 4], ["x", 6]],
        [[1, 2], [3, 4], [float("nan"), 6]],
        "not a list",
        [[0, 0]] * 201,
    ):
        with pytest.raises((ValueError, TypeError)):
            clean_shape(bad)


def test_area() -> None:
    assert area_m2(SQUARE) == pytest.approx(100, abs=1)
    assert area_m2(SQUARE[:2]) == 0


def test_default_background() -> None:
    assert default_background("US") == "aerial"
    assert default_background("CA") == "plain"
    assert default_background(None) == "plain"


async def test_store_keeps_what_fits(hass: HomeAssistant, hass_storage: dict) -> None:
    hass_storage[f"{DOMAIN}.yard"] = {
        "version": 1,
        "key": f"{DOMAIN}.yard",
        "data": {
            "controllers": {
                "entry": {
                    "background": "esri",
                    "esri_key": "key",
                    "zones": {"1": SQUARE, "x": SQUARE, "2": [[1, 2]]},
                },
                "other": {"background": "moon"},
            }
        },
    }
    store = YardStore(hass)
    await store.async_load()
    yard = store.get("entry")
    assert yard["background"] == "esri"
    assert list(yard["zones"]) == ["1"]
    assert yard["areas"]["1"] == pytest.approx(100, abs=1)
    assert store.get("other", "US")["background"] == "aerial"
    assert store.get("missing")["zones"] == {}


async def _client(hass, hass_ws_client):
    async_setup_panel_api(hass)
    return await hass_ws_client(hass)


async def _ws(client, **msg: Any) -> dict:
    await client.send_json_auto_id(msg)
    return await client.receive_json()


async def test_save_and_read_the_yard(
    hass: HomeAssistant,
    hass_ws_client,
    hass_storage: dict,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
) -> None:
    hass.config.country = "US"
    client = await _client(hass, hass_ws_client)

    reply = await _ws(client, type=f"{DOMAIN}/panel/config")
    (controller,) = reply["result"]["controllers"]
    assert controller["yard"]["background"] == "aerial"
    assert controller["yard"]["zones"] == {}

    entry_id = extended_entry.entry_id
    reply = await _ws(
        client, type=f"{DOMAIN}/panel/yard", entry_id=entry_id, zone=2, shape=SQUARE
    )
    assert reply["success"], reply
    assert reply["result"]["zones"]["2"] == SQUARE
    assert reply["result"]["areas"]["2"] == pytest.approx(100, abs=1)

    reply = await _ws(
        client,
        type=f"{DOMAIN}/panel/yard",
        entry_id=entry_id,
        background="esri",
        esri_key="  my-key  ",
    )
    assert reply["result"]["background"] == "esri"
    assert reply["result"]["esri_key"] == "my-key"

    # A bad shape or background is refused and nothing changes.
    reply = await _ws(
        client, type=f"{DOMAIN}/panel/yard", entry_id=entry_id, zone=3, shape=[[1, 2]]
    )
    assert reply["error"]["code"] == "invalid"
    reply = await _ws(
        client, type=f"{DOMAIN}/panel/yard", entry_id=entry_id, background="moon"
    )
    assert not reply["success"]
    reply = await _ws(client, type=f"{DOMAIN}/panel/yard", entry_id="nope", zone=1)
    assert reply["error"]["code"] == "not_found"

    reply = await _ws(client, type=f"{DOMAIN}/panel/config")
    assert reply["result"]["controllers"][0]["yard"]["zones"] == {"2": SQUARE}
    await hass.async_block_till_done()
    assert hass_storage[f"{DOMAIN}.yard"]["data"]["controllers"][entry_id]["zones"] == {
        "2": SQUARE
    }

    # Removing an outline.
    reply = await _ws(
        client, type=f"{DOMAIN}/panel/yard", entry_id=entry_id, zone=2, shape=None
    )
    assert reply["result"]["zones"] == {}


async def test_yard_is_for_admins(
    hass: HomeAssistant,
    hass_ws_client,
    hass_admin_user: MockUser,
    extended_entry: MockConfigEntry,
) -> None:
    hass_admin_user.groups = []
    client = await _client(hass, hass_ws_client)
    reply = await _ws(
        client,
        type=f"{DOMAIN}/panel/yard",
        entry_id=extended_entry.entry_id,
        zone=1,
        shape=SQUARE,
    )
    assert reply["error"]["code"] == "unauthorized"


async def test_removing_the_entry_forgets_the_yard(
    hass: HomeAssistant, extended_entry: MockConfigEntry
) -> None:
    store = await async_get_yard_store(hass)
    await store.async_update(extended_entry.entry_id, zone=1, shape=SQUARE)
    assert store.get(extended_entry.entry_id)["zones"]
    await hass.config_entries.async_remove(extended_entry.entry_id)
    await hass.async_block_till_done()
    assert store.get(extended_entry.entry_id)["zones"] == {}


def test_zones_name_their_valve_and_sensors(
    hass: HomeAssistant,
    rainbird_entry: MockConfigEntry,
    extended_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    created = {
        key: entity_registry.async_get_or_create(
            platform,
            DOMAIN,
            f"{RAINBIRD_UNIQUE_ID}-1-{key}",
            config_entry=extended_entry,
            suggested_object_id=f"zone_1_{key}",
        )
        for platform, key in (
            ("valve", "valve"),
            ("sensor", "time_remaining"),
            ("sensor", "last_run"),
        )
    }
    (zone,) = _panel_config(hass)["controllers"][0]["zones"]
    assert zone["valve_entity"] == created["valve"].entity_id
    assert zone["time_remaining_entity"] == created["time_remaining"].entity_id
    assert zone["last_run_entity"] == created["last_run"].entity_id


def _p8() -> str:
    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric import ec  # noqa: PLC0415

    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def test_apple_key_is_checked() -> None:
    pem = _p8()
    apple = clean_apple(" abcde12345 ", "KEY1234567", pem)
    assert apple["team_id"] == "ABCDE12345"
    for team, key, private in (
        ("SHORT", "KEY1234567", pem),
        ("ABCDE12345", "KEY-123456", pem),
        ("ABCDE12345", "KEY1234567", "not a key"),
    ):
        with pytest.raises(ValueError):
            clean_apple(team, key, private)


def test_apple_token_is_signed_for_mapkit() -> None:
    from cryptography.hazmat.primitives.serialization import load_pem_private_key  # noqa: PLC0415

    pem = _p8()
    apple = clean_apple("ABCDE12345", "KEY1234567", pem)
    token = apple_token(apple, "https://ha.example.com", 1_000_000)
    public = load_pem_private_key(pem.encode(), password=None).public_key()
    claims = jwt.decode(
        token, public, algorithms=["ES256"], options={"verify_exp": False}
    )
    assert claims == {
        "iss": "ABCDE12345",
        "iat": 1_000_000,
        "exp": 1_001_800,
        "origin": "https://ha.example.com",
    }
    assert jwt.get_unverified_header(token)["kid"] == "KEY1234567"
    assert "origin" not in jwt.decode(
        apple_token(apple, None, 1),
        public,
        algorithms=["ES256"],
        options={"verify_exp": False},
    )


async def test_apple_key_over_the_websocket(
    hass: HomeAssistant,
    hass_ws_client,
    hass_storage: dict,
    extended_entry: MockConfigEntry,
) -> None:
    client = await _client(hass, hass_ws_client)
    reply = await _ws(client, type=f"{DOMAIN}/panel/yard/apple_token")
    assert reply["error"]["code"] == "not_set_up"

    reply = await _ws(
        client,
        type=f"{DOMAIN}/panel/yard/apple_key",
        team_id="ABCDE12345",
        key_id="KEY1234567",
        private_key="nope",
    )
    assert reply["error"]["code"] == "invalid"

    pem = _p8()
    reply = await _ws(
        client,
        type=f"{DOMAIN}/panel/yard/apple_key",
        team_id="ABCDE12345",
        key_id="KEY1234567",
        private_key=pem,
    )
    assert reply["result"] == {
        "apple": {"team_id": "ABCDE12345", "key_id": "KEY1234567"}
    }
    # The panel learns the key is there, never the private key itself.
    reply = await _ws(client, type=f"{DOMAIN}/panel/config")
    assert reply["result"]["controllers"][0]["yard"]["apple"]["key_id"] == "KEY1234567"
    assert "PRIVATE" not in str(reply["result"])

    reply = await _ws(
        client,
        type=f"{DOMAIN}/panel/yard/apple_token",
        origin="https://ha.example.com",
    )
    assert jwt.get_unverified_header(reply["result"]["token"])["kid"] == "KEY1234567"
    reply = await _ws(
        client, type=f"{DOMAIN}/panel/yard/apple_token", origin="javascript:alert(1)"
    )
    assert not reply["success"]

    # Kept across a restart, then removed.
    await hass.async_block_till_done()
    store = YardStore(hass)
    await store.async_load()
    assert store.get("x")["apple"]["team_id"] == "ABCDE12345"
    reply = await _ws(client, type=f"{DOMAIN}/panel/yard/apple_key", clear=True)
    assert reply["result"] == {"apple": None}
