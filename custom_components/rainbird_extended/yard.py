"""The yard map: zone outlines drawn on a map in the sidebar panel.

Stored for the whole integration in its own Store (`rainbird_extended.yard`),
keyed by config entry id, never in options, so saving a shape doesn't reload
the controller. Each controller keeps:

- ``background``: ``aerial`` (USGS aerial photos, United States, no key),
  ``esri`` (Esri World Imagery, needs the user's own ArcGIS API key),
  ``apple`` (Apple Maps satellite through MapKit JS, needs the user's own
  Apple Developer MapKit key) or ``plain`` (no map, just the shapes);
- ``esri_key``: that ArcGIS key, shown only to admins (the panel is admin only);
- ``zones``: zone number (as text) to a list of ``[lat, lng]`` points.

The Apple MapKit key (team id, key id and the private key) is kept once for
the whole integration. The private key never leaves Home Assistant: the panel
asks for a short-lived MapKit JS token signed with it (``apple_token``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import copy
import math
import time
from typing import Any

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_pem_private_key
import jwt

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

STORAGE_KEY = f"{DOMAIN}.yard"
STORAGE_VERSION = 1
BACKGROUND_AERIAL = "aerial"
BACKGROUND_ESRI = "esri"
BACKGROUND_APPLE = "apple"
BACKGROUND_PLAIN = "plain"
BACKGROUNDS = (BACKGROUND_AERIAL, BACKGROUND_ESRI, BACKGROUND_APPLE, BACKGROUND_PLAIN)
APPLE_TOKEN_SECONDS = 30 * 60
_APPLE_ID = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
MIN_POINTS = 3
MAX_POINTS = 200
MAX_KEY_LENGTH = 512
_DATA = f"{DOMAIN}_yard_store"
_LOCK = f"{DOMAIN}_yard_lock"


def default_background(country: str | None) -> str:
    """USGS aerial photos cover the United States; elsewhere, no map."""
    return BACKGROUND_AERIAL if country == "US" else BACKGROUND_PLAIN


def clean_shape(points: Any) -> list[list[float]]:
    """A zone's outline: 3 to 200 [lat, lng] points, rounded to ~1 cm.

    Raises ValueError for anything else.
    """
    if not isinstance(points, list) or not MIN_POINTS <= len(points) <= MAX_POINTS:
        raise ValueError(f"A zone needs {MIN_POINTS} to {MAX_POINTS} points")
    shape: list[list[float]] = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("Each point is a latitude and a longitude")
        lat, lng = (float(value) for value in point)
        if not (math.isfinite(lat) and math.isfinite(lng)):
            raise ValueError("Each point is a latitude and a longitude")
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise ValueError("A point is off the map")
        shape.append([round(lat, 7), round(lng, 7)])
    return shape


def clean_apple(team_id: str, key_id: str, private_key: str) -> dict[str, str]:
    """An Apple MapKit key: 10-character team and key ids and a P-256 .p8 key.

    Raises ValueError for anything else.
    """
    team_id, key_id = team_id.strip().upper(), key_id.strip().upper()
    for label, value in (("Team ID", team_id), ("Key ID", key_id)):
        if len(value) != 10 or any(char not in _APPLE_ID for char in value):
            raise ValueError(f"The {label} is 10 letters and numbers")
    pem = private_key.strip()
    try:
        key = load_pem_private_key(pem.encode(), password=None)
    except (ValueError, TypeError) as err:
        raise ValueError("That isn't a .p8 private key") from err
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise ValueError("That isn't a MapKit key (.p8)")
    return {"team_id": team_id, "key_id": key_id, "private_key": pem + "\n"}


def apple_token(apple: dict[str, str], origin: str | None, now: float) -> str:
    """A MapKit JS token, good for 30 minutes and, when known, one origin."""
    payload: dict[str, Any] = {
        "iss": apple["team_id"],
        "iat": int(now),
        "exp": int(now) + APPLE_TOKEN_SECONDS,
    }
    if origin:
        payload["origin"] = origin
    return jwt.encode(
        payload,
        apple["private_key"],
        algorithm="ES256",
        headers={"kid": apple["key_id"], "typ": "JWT"},
    )


def area_m2(points: list[list[float]]) -> float:
    """A zone's area in square meters (flat-earth, fine for a yard)."""
    if len(points) < MIN_POINTS:
        return 0.0
    lat0 = math.radians(sum(p[0] for p in points) / len(points))
    radius = 6_371_008.8
    xy = [
        (
            math.radians(lng) * radius * math.cos(lat0),
            math.radians(lat) * radius,
        )
        for lat, lng in points
    ]
    total = 0.0
    for (x1, y1), (x2, y2) in zip(xy, xy[1:] + xy[:1], strict=True):
        total += x1 * y2 - x2 * y1
    return abs(total) / 2


def _clean(data: Any) -> dict[str, Any]:
    """Stored data for one controller, dropping anything that doesn't fit."""
    data = data if isinstance(data, Mapping) else {}
    zones: dict[str, list[list[float]]] = {}
    for zone, points in (data.get("zones") or {}).items():
        if not str(zone).isdigit():
            continue
        try:
            zones[str(int(zone))] = clean_shape(points)
        except TypeError, ValueError:
            continue
    background = data.get("background")
    key = data.get("esri_key")
    return {
        "background": background if background in BACKGROUNDS else None,
        "esri_key": key if isinstance(key, str) else "",
        "zones": zones,
    }


class YardStore:
    """Zone outlines and the map background, per controller."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict[str, dict[str, Any]] = {}
        self._apple: dict[str, str] | None = None

    async def async_load(self) -> None:
        stored = await self._store.async_load() or {}
        apple = stored.get("apple")
        if isinstance(apple, Mapping):
            try:
                self._apple = clean_apple(
                    str(apple.get("team_id", "")),
                    str(apple.get("key_id", "")),
                    str(apple.get("private_key", "")),
                )
            except ValueError:
                self._apple = None
        self._data = {
            str(entry_id): _clean(value)
            for entry_id, value in (stored.get("controllers") or {}).items()
        }

    def get(self, entry_id: str, country: str | None = None) -> dict[str, Any]:
        """A copy of one controller's yard, with its background filled in."""
        yard = copy.deepcopy(self._data.get(entry_id) or _clean({}))
        if yard["background"] is None:
            yard["background"] = default_background(country)
        yard["areas"] = {
            zone: round(area_m2(points), 1) for zone, points in yard["zones"].items()
        }
        # Never the private key.
        yard["apple"] = (
            {"team_id": self._apple["team_id"], "key_id": self._apple["key_id"]}
            if self._apple
            else None
        )
        return yard

    async def async_set_apple(
        self, team_id: str, key_id: str, private_key: str
    ) -> None:
        """Keep the Apple MapKit key (checked first), for every controller."""
        self._apple = clean_apple(team_id, key_id, private_key)
        await self._save()

    async def async_clear_apple(self) -> None:
        self._apple = None
        await self._save()

    def apple_token(self, origin: str | None) -> str:
        """A short-lived MapKit JS token. Raises ValueError without a key."""
        if self._apple is None:
            raise ValueError("Add your Apple MapKit key first")
        return apple_token(self._apple, origin, time.time())

    async def async_update(
        self,
        entry_id: str,
        *,
        background: str | None = None,
        esri_key: str | None = None,
        zone: int | None = None,
        shape: list | None = None,
        remove_zone: bool = False,
    ) -> None:
        """Change the background, the key, or one zone's outline."""
        yard = self._data.setdefault(entry_id, _clean({}))
        if background is not None:
            if background not in BACKGROUNDS:
                raise ValueError("Unknown map background")
            yard["background"] = background
        if esri_key is not None:
            key = esri_key.strip()
            if len(key) > MAX_KEY_LENGTH:
                raise ValueError("That key is too long")
            yard["esri_key"] = key
        if zone is not None:
            if remove_zone:
                yard["zones"].pop(str(zone), None)
            elif shape is not None:
                yard["zones"][str(zone)] = clean_shape(shape)
        await self._save()

    async def async_remove(self, entry_id: str) -> None:
        if self._data.pop(entry_id, None) is not None:
            await self._save()

    async def _save(self) -> None:
        data: dict[str, Any] = {"controllers": self._data}
        if self._apple:
            data["apple"] = self._apple
        await self._store.async_save(data)


async def async_get_yard_store(hass: HomeAssistant) -> YardStore:
    """The one yard store, loaded on first use."""
    async with hass.data.setdefault(_LOCK, asyncio.Lock()):
        if (store := hass.data.get(_DATA)) is None:
            store = YardStore(hass)
            await store.async_load()
            hass.data[_DATA] = store
    return store
