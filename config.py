import os
from typing import Dict

import yaml


def load_env() -> None:
    env_path = '.env'
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key] = value


class Config:
    def __init__(self, config_path: str):
        self.config_path = config_path
        load_env()
        self.data = self.load_config()
        self.validate_config()

    def load_config(self) -> Dict:
        with open(self.config_path) as f:
            data = yaml.safe_load(f)
        # Override with environment variables if available
        env_keys = ['app_id', 'app_secret', 'serial_number']
        for key in env_keys:
            env_key = key.upper()
            if env_key in os.environ:
                data[key] = os.environ[env_key]
        return data

    def validate_config(self) -> None:
        required_keys = ["app_id", "app_secret", "serial_number", "price_multiplier", "charge_rate_kw"]
        for key in required_keys:
            if key not in self.data:
                raise ValueError(f"{key} not found in config")

    def __getitem__(self, key):
        return self.data[key]

    def get(self, key, default=None):
        return self.data.get(key, default)
