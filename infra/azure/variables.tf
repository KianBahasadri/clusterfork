variable "subscription_id" {
  type    = string
  default = "37d24562-0c19-408a-b492-f765921e443a"
}
variable "tenant_id" {
  type    = string
  default = "d61ecb3b-38b1-42d5-82c4-efb2838b925c"
}
variable "operator_object_id" {
  type    = string
  default = "c76fa8df-010d-45bb-b764-96392e7aca34"
}
variable "seed_credentials" {
  description = "One-time adoption or explicit credential rotation; values never enter plans/state."
  type        = bool
  default     = false
}


variable "runtime_credentials" {
  type      = map(string)
  sensitive = true
  ephemeral = true
  default   = {}
}
variable "credential_versions" {
  description = "Increment the selected version when rotating a write-only secret."
  type        = map(number)
  default = {
    LINEAR_API_KEY             = 1
    LINEAR_WEBHOOK_SECRET      = 1
    GITHUB_APP_PRIVATE_KEY     = 1
    LINEAR_OAUTH_CLIENT_SECRET = 1
  }
}
