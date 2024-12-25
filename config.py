import yaml
from typing import Dict

class Config:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.data = self.load_config()
        self.validate_config()

    def load_config(self) -> Dict:
        with open(self.config_path) as f:
            return yaml.safe_load(f)

    def validate_config(self) -> None:
        required_keys = ["app_id", "app_secret", "serial_number", "price_multiplier", "charge_to_full"]
        for key in required_keys:
            if key not in self.data:
                raise ValueError(f"{key} not found in config")

    def __getitem__(self, key):
        return self.data[key]
