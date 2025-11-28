#!/bin/bash

set -e

# Configuration
AWS_REGION="ap-southeast-1"
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

echo "Pushing GPU Operator v25.3.4 images to ECR..."
echo "ECR Registry: ${ECR_REGISTRY}"

# Login to ECR
echo "Logging into ECR..."
aws ecr get-login-password --region ${AWS_REGION} | docker login --username AWS --password-stdin ${ECR_REGISTRY}

# Read images from file and process
while IFS= read -r line; do
    # Skip comments and empty lines
    [[ "$line" =~ ^#.*$ ]] && continue
    [[ -z "$line" ]] && continue
    
    SOURCE_IMAGE="$line"
    
    # Extract repository name and tag
    REPO_NAME=$(echo "$SOURCE_IMAGE" | sed 's|nvcr.io/||' | sed 's|:[^:]*$||')
    IMAGE_TAG=$(echo "$SOURCE_IMAGE" | sed 's|.*:||')
    ECR_REPO="${ECR_REGISTRY}/${REPO_NAME}:${IMAGE_TAG}"
    
    echo "Processing: $SOURCE_IMAGE"
    
    # Create ECR repository if it doesn't exist
    REPO_NAME_ONLY=$(echo "$SOURCE_IMAGE" | sed 's|nvcr.io/||' | sed 's|:[^:]*$||')
    aws ecr describe-repositories --repository-names "$REPO_NAME_ONLY" --region ${AWS_REGION} >/dev/null 2>&1 || {
        echo "Creating ECR repository: $REPO_NAME_ONLY"
        aws ecr create-repository --repository-name "$REPO_NAME_ONLY" --region ${AWS_REGION} >/dev/null
    }
    
    # Pull, tag, and push
    echo "Pulling $SOURCE_IMAGE..."
    docker pull "$SOURCE_IMAGE"
    
    echo "Tagging as $ECR_REPO..."
    docker tag "$SOURCE_IMAGE" "$ECR_REPO"
    
    echo "Pushing to ECR..."
    docker push "$ECR_REPO"
    
    echo "✓ Completed: $SOURCE_IMAGE -> $ECR_REPO"
    echo ""
    
done < gpu-operator-v25.3.4-images.txt

echo "All images pushed to ECR successfully!"
echo ""
echo "To use these images in GPU Operator, update your values:"
