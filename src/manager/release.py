"""Identity of the running build and its verified NAS deployment."""
import json
import os
from datetime import datetime
from pathlib import Path


def release_info(database_path):
    version = os.environ.get('APP_VERSION', 'development')
    deployed_at = None
    try:
        marker = json.loads(Path(database_path).with_name('deployment.json').read_text())
        if isinstance(marker, dict) and marker.get('version') == version:
            stamp = datetime.fromisoformat(marker['deployed_at'])
            if stamp.tzinfo is not None:
                deployed_at = stamp.isoformat()
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return {'version': version, 'deployed_at': deployed_at}
