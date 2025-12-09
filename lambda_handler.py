"""
AWS Lambda handler for AlphaESS Charging Optimizer

Triggered by EventBridge schedule to optimize battery charging/discharging.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta

# Configure logging for CloudWatch
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Suppress noisy loggers
logging.getLogger('aiohttp').setLevel(logging.WARNING)
logging.getLogger('asyncio').setLevel(logging.WARNING)


async def run_optimization(mode: str, config_path: str) -> dict:
    """
    Async optimization runner - must be called within event loop
    so aiohttp can create its session properly.
    """
    from ESS import ESSOptimizer
    
    optimizer = ESSOptimizer(config_path)
    
    try:
        if mode == 'tomorrow':
            target_date = datetime.now() + timedelta(days=1)
            logger.info(f"Optimizing for tomorrow: {target_date.date()}")
            success = await optimizer.optimize_for_day(target_date)
            
        elif mode == 'today':
            target_date = datetime.now()
            logger.info(f"Optimizing for today: {target_date.date()}")
            success = await optimizer.optimize_for_day(target_date)
            
        elif mode == 'reactive':
            current_hour = datetime.now().hour
            logger.info(f"Running reactive check at hour {current_hour}")
            success = await optimizer.reactive_check(current_hour)
            
        else:
            raise ValueError(f"Unknown mode: {mode}")
        
        return {
            'success': success,
            'mode': mode,
            'timestamp': datetime.now().isoformat(),
            'message': 'Optimization completed' if success else 'Optimization failed'
        }
    finally:
        await optimizer.client.close()


def lambda_handler(event, context):
    """
    AWS Lambda entry point for ESS optimization.
    
    Trigger options:
    - Scheduled via EventBridge (daily at 18:00 for next-day optimization)
    - Manual invocation with optional parameters
    
    Event parameters (all optional):
    - mode: "tomorrow" (default), "today", or "reactive"
    - config_path: Override config file path
    
    Environment variables (required):
    - APP_ID: AlphaESS API app ID
    - APP_SECRET: AlphaESS API app secret  
    - SERIAL_NUMBER: ESS serial number
    """
    logger.info(f"Lambda invoked with event: {json.dumps(event)}")
    
    # Parse event parameters
    mode = event.get('mode', 'tomorrow')
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
        result = asyncio.run(run_optimization(mode, config_path))
        
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
    test_event = {'mode': 'tomorrow'}
    result = lambda_handler(test_event, None)
    print(json.dumps(result, indent=2))
