# AWS Lambda Python 3.12 base image (arm64 for Graviton)
FROM --platform=linux/arm64 public.ecr.aws/lambda/python:3.12

# Install dependencies
COPY pyproject.toml ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir .

# Copy application code
COPY optimizer.py ${LAMBDA_TASK_ROOT}/
COPY models.py ${LAMBDA_TASK_ROOT}/
COPY price_analyzer.py ${LAMBDA_TASK_ROOT}/
COPY battery_manager.py ${LAMBDA_TASK_ROOT}/
COPY ess_client.py ${LAMBDA_TASK_ROOT}/
COPY price_cache.py ${LAMBDA_TASK_ROOT}/
COPY config.py ${LAMBDA_TASK_ROOT}/
COPY config.yaml ${LAMBDA_TASK_ROOT}/
COPY lambda_handler.py ${LAMBDA_TASK_ROOT}/

# Set the Lambda handler
CMD ["lambda_handler.lambda_handler"]

