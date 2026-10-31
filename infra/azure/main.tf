locals {
  group    = "rg-clusterfork-automation"
  location = "eastus"
  app      = "clusterfork-dispatch-7604cac329"
  app_id   = "/subscriptions/${var.subscription_id}/resourceGroups/${local.group}/providers/Microsoft.Web/sites/${local.app}"
  secrets  = toset(keys(var.credential_versions))
}

resource "azurerm_resource_group" "automation" {
  name     = local.group
  location = local.location
  tags     = { application = "clusterfork-routing-pilot" }
  lifecycle { prevent_destroy = true }
}

resource "azurerm_log_analytics_workspace" "automation" {
  name                         = "${local.app}-logs"
  resource_group_name          = azurerm_resource_group.automation.name
  location                     = local.location
  sku                          = "PerGB2018"
  retention_in_days            = 30
  daily_quota_gb               = 0.1
  local_authentication_enabled = false
  tags                         = { application = "clusterfork" }
  lifecycle { prevent_destroy = true }
}

resource "azurerm_application_insights" "automation" {
  name                = "${local.app}-insights"
  resource_group_name = azurerm_resource_group.automation.name
  location            = local.location
  application_type    = "web"
  workspace_id        = azurerm_log_analytics_workspace.automation.id
  retention_in_days   = 30
  tags = {
    application                   = "clusterfork"
    "hidden-link:${local.app_id}" = "Resource"
  }
  lifecycle { prevent_destroy = true }
}

resource "azurerm_monitor_action_group" "smart_detection" {
  name                = "Application Insights Smart Detection"
  resource_group_name = azurerm_resource_group.automation.name
  short_name          = "SmartDetect"
  arm_role_receiver {
    name                    = "Monitoring Contributor"
    role_id                 = "749f88d5-cbae-40b8-bcfc-e573ddc772fa"
    use_common_alert_schema = true
  }
  arm_role_receiver {
    name                    = "Monitoring Reader"
    role_id                 = "43d0d8ad-25c7-4714-9337-8ba259a9fe05"
    use_common_alert_schema = true
  }
}

resource "azurerm_user_assigned_identity" "deploy" {
  name                = "clusterfork-github-deploy"
  resource_group_name = azurerm_resource_group.automation.name
  location            = local.location
  lifecycle { prevent_destroy = true }
}

resource "azurerm_federated_identity_credential" "github" {
  name                      = "github-azure-production"
  user_assigned_identity_id = azurerm_user_assigned_identity.deploy.id
  audience                  = ["api://AzureADTokenExchange"]
  issuer                    = "https://token.actions.githubusercontent.com"
  subject                   = "repo:KianBahasadri/clusterfork:environment:azure-production"
}

resource "azurerm_role_assignment" "deploy_infrastructure" {
  scope                = azurerm_resource_group.automation.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.deploy.principal_id
}

resource "azurerm_role_assignment" "deploy_rbac" {
  scope                = azurerm_resource_group.automation.id
  role_definition_name = "Role Based Access Control Administrator"
  principal_id         = azurerm_user_assigned_identity.deploy.principal_id
}

resource "azurerm_key_vault" "runtime" {
  name                       = "cfsecrets7604cac329"
  location                   = local.location
  resource_group_name        = azurerm_resource_group.automation.name
  tenant_id                  = var.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  purge_protection_enabled   = true
  soft_delete_retention_days = 30
  lifecycle { prevent_destroy = true }
}

resource "azurerm_role_assignment" "operator_secrets" {
  scope                = azurerm_key_vault.runtime.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = var.operator_object_id
}

resource "azurerm_role_assignment" "deploy_secrets" {
  scope                = azurerm_key_vault.runtime.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.deploy.principal_id
}

ephemeral "azurerm_key_vault_secret" "runtime" {
  for_each     = var.seed_credentials ? toset([]) : local.secrets
  name         = lower(replace(each.key, "_", "-"))
  key_vault_id = azurerm_key_vault.runtime.id
}

resource "azurerm_key_vault_secret" "runtime" {
  for_each         = local.secrets
  name             = lower(replace(each.key, "_", "-"))
  key_vault_id     = azurerm_key_vault.runtime.id
  value_wo         = var.seed_credentials ? var.runtime_credentials[each.key] : ephemeral.azurerm_key_vault_secret.runtime[each.key].value
  value_wo_version = var.credential_versions[each.key]
  depends_on       = [azurerm_role_assignment.operator_secrets]
  lifecycle {
    prevent_destroy = true
    # The value is deliberately absent from state. The declared version controls rotation.
    ignore_changes = [value_wo]
  }
}

output "deployment_client_id" { value = azurerm_user_assigned_identity.deploy.client_id }
