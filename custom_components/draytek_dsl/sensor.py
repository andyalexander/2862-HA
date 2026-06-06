"""DSL line sensors for DrayTek Vigor."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfDataRate, UnitOfSoundPressure
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DraytekDslCoordinator, LineData


@dataclass(frozen=True)
class DraytekSensorDescription(SensorEntityDescription):
    """Extends SensorEntityDescription with a value extractor."""

    value_fn: Callable[[LineData], float | None] = lambda _: None


def _kbps_to_mbps(key: str) -> Callable[[LineData], float | None]:
    def _fn(data: LineData) -> float | None:
        kbps = data.get(key)
        return round(kbps / 1000, 3) if kbps is not None else None
    return _fn


def _db_value(key: str) -> Callable[[LineData], float | None]:
    def _fn(data: LineData) -> float | None:
        val = data.get(key)
        return float(val) if val is not None else None
    return _fn


SENSOR_DESCRIPTIONS: tuple[DraytekSensorDescription, ...] = (
    DraytekSensorDescription(
        key="download_speed",
        name="DSL Download Speed",
        device_class=SensorDeviceClass.DATA_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        value_fn=_kbps_to_mbps("download_kbps"),
    ),
    DraytekSensorDescription(
        key="upload_speed",
        name="DSL Upload Speed",
        device_class=SensorDeviceClass.DATA_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        value_fn=_kbps_to_mbps("upload_kbps"),
    ),
    DraytekSensorDescription(
        key="snr_upstream",
        name="DSL SNR Upstream",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfSoundPressure.DECIBEL,
        value_fn=_db_value("snr_upstream_db"),
    ),
    DraytekSensorDescription(
        key="snr_downstream",
        name="DSL SNR Downstream",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfSoundPressure.DECIBEL,
        value_fn=_db_value("snr_downstream_db"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: DraytekDslCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        DraytekDslSensor(coordinator, entry, desc)
        for desc in SENSOR_DESCRIPTIONS
    )


class DraytekDslSensor(CoordinatorEntity[DraytekDslCoordinator], SensorEntity):
    """A sensor reporting a single DSL line metric."""

    entity_description: DraytekSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DraytekDslCoordinator,
        entry: ConfigEntry,
        description: DraytekSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"DrayTek Vigor ({coordinator.host})",
            manufacturer="DrayTek",
            model="Vigor 2862",
            configuration_url=f"http://{coordinator.host}",
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
