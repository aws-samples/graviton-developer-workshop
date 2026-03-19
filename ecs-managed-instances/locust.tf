# ---------- Locust Load Generator ----------
#
# Drives load against the asianoptions ALB to trigger auto-scaling.
# Runs in headless mode with 500 users to push CPU above 50%.

resource "aws_ecr_repository" "locust" {
  name         = "locust-loadtest"
  force_delete = true
}

resource "null_resource" "locust_image" {
  triggers = {
    dockerfile = filemd5("${path.module}/Dockerfile.locust")
    locustfile = filemd5("${path.module}/locustfile.py")
  }

  provisioner "local-exec" {
    command = <<-EOT
      aws ecr get-login-password --region ${var.region} | docker login --username AWS --password-stdin ${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.region}.amazonaws.com
      docker build --platform linux/amd64 -t locust-loadtest -f ${path.module}/Dockerfile.locust ${path.module}
      docker tag locust-loadtest:latest ${aws_ecr_repository.locust.repository_url}:latest
      docker push ${aws_ecr_repository.locust.repository_url}:latest
    EOT
  }
}

resource "aws_cloudwatch_log_group" "locust" {
  name              = "/ecs/locust-loadtest"
  retention_in_days = 7
}

resource "aws_ecs_task_definition" "locust" {
  family                   = "locust-loadtest"
  network_mode             = "awsvpc"
  requires_compatibilities = ["EC2"]
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.task_execution.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name      = "locust"
    image     = "${aws_ecr_repository.locust.repository_url}:latest"
    essential = true
    command = [
      "-f", "/home/locust/locustfile.py",
      "--headless",
      "--host", "http://${aws_lb.main.dns_name}",
      "-u", "500",
      "-r", "50",
      "--run-time", "15m"
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.locust.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "ecs"
      }
    }
  }])

  depends_on = [null_resource.locust_image]
}

resource "aws_ecs_service" "locust" {
  name                 = "locust-loadtest"
  cluster              = aws_ecs_cluster.main.id
  task_definition      = aws_ecs_task_definition.locust.arn
  desired_count        = 1
  force_new_deployment = true

  capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.amd64.name
    weight            = 1
  }

  network_configuration {
    subnets         = aws_subnet.private[*].id
    security_groups = [aws_security_group.ecs.id]
  }

  depends_on = [aws_ecs_cluster_capacity_providers.main]
}
