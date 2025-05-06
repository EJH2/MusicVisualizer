import configparser
import os
from typing import MutableMapping, Union


class Singleton(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(Singleton, cls).__call__(*args, **kwargs)
        return cls._instances[cls]


class Config(metaclass=Singleton):
    def __init__(self):
        self._config_path = os.environ["CONFIG_PATH"]

        config = configparser.ConfigParser()
        config.read(self._config_path)
        self._config = config

    def get_section(self, section: str):
        if not section in self._config.sections():
            self._config.add_section(section)

        return self._config[section]

    def save_section(self, section: str, data: Union[dict, MutableMapping]):
        config_section = self.get_section(section)
        for key, value in data.items():
            config_section[key] = value

        with open(self._config_path, "w") as configfile:
            self._config.write(configfile)
