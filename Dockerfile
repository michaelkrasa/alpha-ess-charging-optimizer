# Optional container build. ZIP packaging is preferred to avoid ECR storage costs.
FROM public.ecr.aws/lambda/python:3.13
COPY requirements-lambda.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r requirements-lambda.txt
COPY src/ ${LAMBDA_TASK_ROOT}/src/
COPY manager.yaml solar_config.yaml lambda_handler.py config.py ${LAMBDA_TASK_ROOT}/
CMD ["lambda_handler.lambda_handler"]
