################################################################################
# Pod Identity — agentgateway needs Bedrock access via SigV4
################################################################################

# IAM role for agentgateway (Bedrock access)
resource "aws_iam_role" "agentgateway" {
  name = "${var.cluster_name}-agentgateway"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "pods.eks.amazonaws.com"
        }
        Action = [
          "sts:AssumeRole",
          "sts:TagSession"
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy" "agentgateway_bedrock" {
  name = "bedrock-invoke"
  role = aws_iam_role.agentgateway.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream"
        ]
        Resource = [
          "arn:aws:bedrock:*::foundation-model/*",
          "arn:aws:bedrock:*:${data.aws_caller_identity.current.account_id}:inference-profile/*"
        ]
      }
    ]
  })
}

data "aws_caller_identity" "current" {}

# Pod Identity association — binds the IAM role to the agentgateway ServiceAccount
resource "aws_eks_pod_identity_association" "agentgateway" {
  cluster_name    = module.eks.cluster_name
  namespace       = "gateway"
  service_account = "agentgateway-sa"
  role_arn        = aws_iam_role.agentgateway.arn
}
