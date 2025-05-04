import configparser
import os

from util.singleton import Singleton


class ConfigHandler(metaclass=Singleton):
    def __init__(self):
        self._config_path = os.environ["CONFIG_PATH"]
        print(self._config_path)

        config = configparser.ConfigParser()
        config.read(self._config_path)
        self._config = config

    def get_section(self, section):
        if not section in self._config.sections():
            self._config.add_section(section)

        return self._config[section]

    def save_section(self, section, data):
        config_section = self.get_section(section)
        for key, value in data.items():
            config_section[key] = value

        with open(self._config_path, "w") as configfile:
            self._config.write(configfile)
