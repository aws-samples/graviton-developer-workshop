provider "aws" {
  region = local.region
}

data "aws_availability_zones" "available" {}

locals {
  name           = basename(path.cwd)
  intel_asg_name = "intel-asg"
  arm_asg_name   = "arm-asg"
  region         = var.AWS_REGION

  vpc_cidr = "10.0.0.0/16"
  azs      = slice(data.aws_availability_zones.available.names, 0, 3)

  user_data = <<-EOT
    #!/bin/bash
    cat <<'EOF' >> /etc/ecs/ecs.config
    ECS_CLUSTER=${local.name}
    ECS_LOGLEVEL=debug
    ECS_ENABLE_TASK_IAM_ROLE=true
    EOF
  EOT

  tags = {
    Blueprint  = local.name
    GithubRepo = "github.com/aws-ia/ecs-blueprints"
  }
}

################################################################################
# ECS Blueprint
################################################################################

module "ecs_cluster" {
  source  = "terraform-aws-modules/ecs/aws//modules/cluster"
  version = "~> 5.11"

  cluster_name = local.name

  # Capacity provider - autoscaling groups
  default_capacity_provider_use_fargate = false


  autoscaling_capacity_providers = {
    "intel_capacity_provider" = {
      auto_scaling_group_arn         = module.intel_autoscaling.autoscaling_group_arn
      managed_termination_protection = "DISABLED"

      managed_scaling = {
        maximum_scaling_step_size = 5
        minimum_scaling_step_size = 1
        status                    = "ENABLED"
        target_capacity           = 60
      }

      default_capacity_provider_strategy = {
        weight = 1
      }
    },
    "arm_capacity_provider" = {
      auto_scaling_group_arn         = module.arm_autoscaling.autoscaling_group_arn
      managed_termination_protection = "DISABLED"

      managed_scaling = {
        maximum_scaling_step_size = 5
        minimum_scaling_step_size = 1
        status                    = "ENABLED"
        target_capacity           = 80
      }

      default_capacity_provider_strategy = {
        weight = 1
      }
    }
  }

  tags = local.tags
}


################################################################################
# Supporting Resources
################################################################################

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.8"

  name = local.name
  cidr = local.vpc_cidr

  azs             = local.azs
  private_subnets = [for k, v in local.azs : cidrsubnet(local.vpc_cidr, 4, k)]
  public_subnets  = [for k, v in local.azs : cidrsubnet(local.vpc_cidr, 8, k + 48)]

  enable_nat_gateway = true
  single_nat_gateway = true

  # Manage so we can name
  manage_default_network_acl    = true
  default_network_acl_tags      = { Name = "${local.name}-default" }
  manage_default_route_table    = true
  default_route_table_tags      = { Name = "${local.name}-default" }
  manage_default_security_group = true
  default_security_group_tags   = { Name = "${local.name}-default" }

  tags = local.tags
}

# https://docs.aws.amazon.com/AmazonECS/latest/developerguide/ecs-optimized_AMI.html#ecs-optimized-ami-linux
data "aws_ssm_parameter" "ecs_optimized_ami" {
  name = "/aws/service/ecs/optimized-ami/amazon-linux-2023/recommended"
}

data "aws_ssm_parameter" "ecs_optimized_ami_arm" {
  name = "/aws/service/ecs/optimized-ami/amazon-linux-2023/arm64/recommended"
}

module "intel_autoscaling" {
  source  = "terraform-aws-modules/autoscaling/aws"
  version = "~> 7.4"

  name = local.intel_asg_name

  image_id      = jsondecode(data.aws_ssm_parameter.ecs_optimized_ami.value)["image_id"]
  instance_type = "c5.xlarge"

  security_groups                 = [module.autoscaling_sg.security_group_id]
  user_data                       = base64encode(local.user_data)
  ignore_desired_capacity_changes = true

  create_iam_instance_profile = true
  iam_role_name               = local.name
  iam_role_description        = "ECS role for ${local.name}"
  iam_role_policies = {
    AmazonEC2ContainerServiceforEC2Role = "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
  }

  vpc_zone_identifier = module.vpc.private_subnets
  health_check_type   = "EC2"
  min_size            = 1
  max_size            = 3
  desired_capacity    = 1

  # https://github.com/hashicorp/terraform-provider-aws/issues/12582
  autoscaling_group_tags = {
    AmazonECSManaged = true
  }

  # Required for  managed_termination_protection = "ENABLED"
  protect_from_scale_in = false

  tags = local.tags
}

module "arm_autoscaling" {
  source  = "terraform-aws-modules/autoscaling/aws"
  version = "~> 7.4"

  name = local.arm_asg_name

  image_id      = jsondecode(data.aws_ssm_parameter.ecs_optimized_ami_arm.value)["image_id"]
  instance_type = "c6g.xlarge"

  security_groups                 = [module.autoscaling_sg.security_group_id]
  user_data                       = base64encode(local.user_data)
  ignore_desired_capacity_changes = true

  create_iam_instance_profile = true
  iam_role_name               = local.name
  iam_role_description        = "ECS role for ${local.name}"
  iam_role_policies = {
    AmazonEC2ContainerServiceforEC2Role = "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
  }

  vpc_zone_identifier = module.vpc.private_subnets
  health_check_type   = "EC2"
  min_size            = 1
  max_size            = 3
  desired_capacity    = 1

  # https://github.com/hashicorp/terraform-provider-aws/issues/12582
  autoscaling_group_tags = {
    AmazonECSManaged = true
  }

  # Required for  managed_termination_protection = "ENABLED"
  protect_from_scale_in = false

  tags = local.tags
}

module "autoscaling_sg" {
  source  = "terraform-aws-modules/security-group/aws"
  version = "~> 5.1"

  name        = local.name
  description = "Autoscaling group security group"
  vpc_id      = module.vpc.vpc_id

  ingress_cidr_blocks = [module.vpc.vpc_cidr_block]
  ingress_rules       = ["http-80-tcp"]

  egress_rules = ["all-all"]

  tags = local.tags
}
