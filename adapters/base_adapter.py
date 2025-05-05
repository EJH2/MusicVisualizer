from abc import abstractmethod
from io import BytesIO
from typing import Optional

from util import Config, Singleton


class BaseAdapter(metaclass=Singleton):
    def __init__(self, adapter_name: str):
        self._config = Config()
        self._settings = self._config.get_section(adapter_name)
        self.adapter_name = adapter_name

    def _save_config(self, data) -> None:
        self._config.save_section(self.adapter_name, data)

    @abstractmethod
    async def get_current_song(self) -> tuple[str, list, Optional[BytesIO]]:
        pass
