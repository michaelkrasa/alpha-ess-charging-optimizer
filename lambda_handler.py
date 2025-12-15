"""
AWS Lambda handler for AlphaESS Charging Optimizer

Triggered by EventBridge schedule at 00:00 daily to optimize battery charging/discharging for that day.
Runs once and exits.
"""

import asyncio
import json
import logging
import os
from datetime import datetime

# Configure logging for CloudWatch - let CloudWatch handle timestamps
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Remove default handlers
for handler in logger.handlers:
    logger.removeHandler(handler)

handler = logging.StreamHandler()
formatter = logging.Formatter('%(levelname)s %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

# Suppress noisy loggers
logging.getLogger('aiohttp').setLevel(logging.WARNING)
logging.getLogger('asyncio').setLevel(logging.WARNING)


async def run_optimization(config_path: str) -> dict:
    """
    Async optimization runner - must be called within event loop
    so aiohttp can create its session properly.
    
    Runs once for today and exits (Lambda execution model).
    """
    from optimizer import ESSOptimizer

    optimizer = ESSOptimizer(config_path)

    try:
        target_date = datetime.now()
        logger.info(f"Optimizing for today: {target_date.date()}")
        success = await optimizer.optimize_for_day(target_date, dry_run=False)

        return {
            'success': success,
            'timestamp': datetime.now().isoformat(),
            'message': 'Optimization completed' if success else 'Optimization failed'
        }
    finally:
        await optimizer.client.close()


def lambda_handler(event, context):
    """
    AWS Lambda entry point for ESS optimization.
    
    Designed to run once at 00:00 daily via EventBridge schedule.
    Optimizes for the current day (today).
    
    Event parameters (all optional):
    - config_path: Override config file path (default: 'config.yaml')
    
    Environment variables (required):
    - APP_ID: AlphaESS API app ID
    - APP_SECRET: AlphaESS API app secret  
    - SERIAL_NUMBER: ESS serial number
    """
    logger.info(f"Lambda invoked with event: {json.dumps(event)}")

    # Parse event parameters
    config_path = event.get('config_path', 'config.yaml')

    # Validate required environment variables
    required_env = ['APP_ID', 'APP_SECRET', 'SERIAL_NUMBER']
    missing = [var for var in required_env if not os.environ.get(var)]
    if missing:
        error_msg = f"Missing required environment variables: {missing}"
        logger.error(error_msg)
        return {
            'statusCode': 400,
            'body': json.dumps({'success': False, 'error': error_msg})
        }

    try:
        # Run the async optimization within asyncio.run()
        # This ensures the event loop is running before ESSOptimizer is created
        result = asyncio.run(run_optimization(config_path))

        response = {
            'statusCode': 200 if result['success'] else 500,
            'body': json.dumps(result)
        }
        logger.info(f"Lambda completed: {response}")
        return response

    except Exception as e:
        logger.exception(f"Lambda execution failed: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'success': False,
                'error': str(e),
                'error_type': type(e).__name__
            })
        }


# For local testing
if __name__ == "__main__":
    test_event = {}
    result = lambda_handler(test_event, None)
    print(json.dumps(result, indent=2))
