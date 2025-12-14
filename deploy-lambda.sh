#!/bin/bash
set -e

# Configuration - Set these via environment variables or .env file
# DO NOT hardcode values here for public repos!

# Load from .env if it exists
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

# Required environment variables for ECR
AWS_REGION="${AWS_REGION:-eu-central-1}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:?Error: AWS_ACCOUNT_ID not set}"
ECR_REPO="${ECR_REPO:?Error: ECR_REPO not set}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
LAMBDA_FUNCTION_NAME="${LAMBDA_FUNCTION_NAME:-alpha-ess-charging-optimizer}"

# Required environment variables for AWS credentials
AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:?Error: AWS_ACCESS_KEY_ID not set}"
AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:?Error: AWS_SECRET_ACCESS_KEY not set}"

# Required environment variables for Lambda (AlphaESS API)
APP_ID="${APP_ID:?Error: APP_ID not set}"
APP_SECRET="${APP_SECRET:?Error: APP_SECRET not set}"
SERIAL_NUMBER="${SERIAL_NUMBER:?Error: SERIAL_NUMBER not set}"

ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}"

echo "🔧 Deploying to:"
echo "   ECR: ${ECR_URI}:${IMAGE_TAG}"
echo ""

# Step 1: Authenticate Docker with ECR
echo "🔐 Authenticating with ECR..."
aws ecr get-login-password --region ${AWS_REGION} | \
    docker login --username AWS --password-stdin ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com

# Step 2: Build the Docker image (native arm64 for Lambda Graviton)
echo "🏗️  Building Docker image (arm64)..."
docker build -t ${ECR_REPO}:${IMAGE_TAG} .

# Step 3: Tag for ECR
echo "🏷️  Tagging image..."
docker tag ${ECR_REPO}:${IMAGE_TAG} ${ECR_URI}:${IMAGE_TAG}

# Step 4: Push to ECR
echo "⬆️  Pushing to ECR..."
docker push ${ECR_URI}:${IMAGE_TAG}

echo ""
echo "✅ Image pushed to:"
echo "   ${ECR_URI}:${IMAGE_TAG}"

# Step 5: Update Lambda function (if it exists)
echo ""
if aws lambda get-function --function-name ${LAMBDA_FUNCTION_NAME} --region ${AWS_REGION} > /dev/null 2>&1; then
    echo "🔄 Updating Lambda function..."
    
    # Update function code to use new image
    aws lambda update-function-code \
        --function-name ${LAMBDA_FUNCTION_NAME} \
        --image-uri ${ECR_URI}:${IMAGE_TAG} \
        --region ${AWS_REGION} > /dev/null
    
    # Wait for update to complete
    echo "   Waiting for code update..."
    aws lambda wait function-updated --function-name ${LAMBDA_FUNCTION_NAME} --region ${AWS_REGION}
    
    # Update configuration: environment variables, timeout, memory
    echo "🔑 Setting environment variables and configuration..."
    aws lambda update-function-configuration \
        --function-name ${LAMBDA_FUNCTION_NAME} \
        --environment "Variables={APP_ID=${APP_ID},APP_SECRET=${APP_SECRET},SERIAL_NUMBER=${SERIAL_NUMBER}}" \
        --timeout 30 \
        --memory-size 256 \
        --region ${AWS_REGION} > /dev/null
    
    echo "✅ Lambda function '${LAMBDA_FUNCTION_NAME}' updated!"
    echo "   Timeout: 30s, Memory: 256 MB"
else
    echo "⚠️  Lambda function '${LAMBDA_FUNCTION_NAME}' not found."
    echo ""
    echo "📋 Create it manually:"
    echo "   1. Go to AWS Lambda → Create function → Container image"
    echo "   2. Name: ${LAMBDA_FUNCTION_NAME}"
    echo "   3. Select image: ${ECR_URI}:${IMAGE_TAG}"
    echo "   4. ⚠️  Set Architecture to 'arm64'"
    echo "   5. Set timeout to 300 seconds, memory to 512 MB"
    echo ""
    echo "   Then run this script again to configure environment variables."
fi
