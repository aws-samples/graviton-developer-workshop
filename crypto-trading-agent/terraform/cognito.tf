################################################################################
# Cognito User Pool
################################################################################

resource "aws_cognito_user_pool" "main" {
  name = "${var.cluster_name}-users"

  password_policy {
    minimum_length    = 8
    require_lowercase = true
    require_numbers   = true
    require_symbols   = true
    require_uppercase = true
  }

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  schema {
    name                = "email"
    attribute_data_type = "String"
    required            = true
    mutable             = true

    string_attribute_constraints {
      min_length = 1
      max_length = 256
    }
  }

  tags = local.tags
}

################################################################################
# App Client
################################################################################

resource "aws_cognito_user_pool_client" "chat_ui" {
  name         = "${var.cluster_name}-chat-ui"
  user_pool_id = aws_cognito_user_pool.main.id

  explicit_auth_flows = [
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
    "ALLOW_USER_SRP_AUTH",
  ]

  generate_secret = false

  token_validity_units {
    access_token  = "hours"
    id_token      = "hours"
    refresh_token = "days"
  }

  access_token_validity  = 1
  id_token_validity      = 1
  refresh_token_validity = 30
}

################################################################################
# Groups
################################################################################

resource "aws_cognito_user_group" "trader" {
  name         = "trader"
  user_pool_id = aws_cognito_user_pool.main.id
  description  = "Traders with full trading permissions"
}

resource "aws_cognito_user_group" "viewer" {
  name         = "viewer"
  user_pool_id = aws_cognito_user_pool.main.id
  description  = "Viewers with read-only access"
}

################################################################################
# Pre-seeded Users
################################################################################

resource "aws_cognito_user" "trader1" {
  user_pool_id = aws_cognito_user_pool.main.id
  username     = "trader1"
  password     = "Workshop1!"

  attributes = {
    email          = "trader1@workshop.local"
    email_verified = "true"
  }

  message_action = "SUPPRESS"
}

resource "aws_cognito_user" "viewer1" {
  user_pool_id = aws_cognito_user_pool.main.id
  username     = "viewer1"
  password     = "Workshop1!"

  attributes = {
    email          = "viewer1@workshop.local"
    email_verified = "true"
  }

  message_action = "SUPPRESS"
}

################################################################################
# User-Group Memberships
################################################################################

resource "aws_cognito_user_in_group" "trader1_trader" {
  user_pool_id = aws_cognito_user_pool.main.id
  group_name   = aws_cognito_user_group.trader.name
  username     = aws_cognito_user.trader1.username
}

resource "aws_cognito_user_in_group" "viewer1_viewer" {
  user_pool_id = aws_cognito_user_pool.main.id
  group_name   = aws_cognito_user_group.viewer.name
  username     = aws_cognito_user.viewer1.username
}
