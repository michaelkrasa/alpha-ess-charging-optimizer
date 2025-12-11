"""Price caching for today and tomorrow"""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

from models import SLOTS_PER_DAY

logger = logging.getLogger(__name__)


class PriceCache:
    """Manages price caching for today and tomorrow"""

    def __init__(self):
        # Price cache file path - use /tmp in Lambda (ephemeral but works within invocation)
        self._is_lambda = os.environ.get('AWS_LAMBDA_FUNCTION_NAME') is not None
        if self._is_lambda:
            self._price_cache_file = Path('/tmp/price_cache.json')
        else:
            self._price_cache_file = Path('logs/price_cache.json')
            Path('logs').mkdir(exist_ok=True)

    def load(self) -> Dict[str, Dict[str, float]]:
        """Load price cache from disk"""
        if not self._price_cache_file.exists():
            return {}
        try:
            with open(self._price_cache_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load price cache: {e}")
            return {}

    def save(self, cache: Dict[str, Dict[str, float]]) -> None:
        """Save price cache to disk"""
        try:
            with open(self._price_cache_file, 'w') as f:
                json.dump(cache, f)
        except IOError as e:
            logger.warning(f"Failed to save price cache: {e}")

    def cleanup(self, cache: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, float]]:
        """Remove stale entries from price cache, keeping only today and tomorrow"""
        today = datetime.now().date()
        tomorrow = today + timedelta(days=1)
        valid_dates = {str(today), str(tomorrow)}

        stale_dates = [d for d in cache if d not in valid_dates]
        for d in stale_dates:
            del cache[d]
            logger.debug(f"Removed stale price cache entry for {d}")

        return cache

    def get(self, date_str: str) -> Optional[Dict[int, float]]:
        """Get cached prices for a date"""
        cache = self.load()
        cache = self.cleanup(cache)
        
        if date_str in cache:
            logger.debug(f"price_cache.get cached=true date={date_str}")
            # Convert string keys back to int for slot_prices
            return {int(k): v for k, v in cache[date_str].items()}
        return None

    def set(self, date_str: str, slot_prices: Dict[int, float]) -> None:
        """Cache prices for a date (only if today or tomorrow)"""
        today = datetime.now().date()
        tomorrow = today + timedelta(days=1)
        date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
        
        if date_obj in {today, tomorrow}:
            cache = self.load()
            cache = self.cleanup(cache)
            # Store with string keys for JSON serialization
            cache[date_str] = {str(k): v for k, v in slot_prices.items()}
            self.save(cache)
            logger.debug(f"price_cache.set cached=true date={date_obj} slots={SLOTS_PER_DAY}")
        else:
            logger.debug(f"price_cache.set cached=false date={date_obj} slots={SLOTS_PER_DAY} reason=outside_window")

