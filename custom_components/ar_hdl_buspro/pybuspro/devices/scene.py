"""Scene device wrapper."""
from __future__ import annotations

from .control import _SceneControl
from .device import Device


class Scene(Device):
    """A scene control object."""

    def __init__(self, buspro, device_address, scene_address, name: str = "") -> None:
        """Initialize the scene.

        device_address is (subnet, device) of the panel that holds the scene.
        scene_address is (area_number, scene_number).
        """
        super().__init__(buspro, scene_address, name)
        self._buspro = buspro
        self._device_address = device_address
        self._scene_address = scene_address

    async def run(self) -> None:
        """Trigger the scene."""
        ctrl = _SceneControl(self._buspro)
        ctrl.subnet_id, ctrl.device_id = self._device_address
        ctrl.area_number, ctrl.scene_number = self._scene_address
        await ctrl.send()
