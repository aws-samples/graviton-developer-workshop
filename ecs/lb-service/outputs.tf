output "application_url" {
  value       = "http://${module.alb.dns_name}/books"
  description = "Copy this value in your browser in order to access the deployed app"
}