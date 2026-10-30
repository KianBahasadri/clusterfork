locals {
  group       = "rg-clusterfork-automation"
  location    = "eastus"
  app         = "clusterfork-dispatch-7604cac329"
  storage     = "cfdispatch7604cac329"
  group_id    = "/subscriptions/${var.subscription_id}/resourceGroups/${local.group}"
  storage_id  = "${local.group_id}/providers/Microsoft.Storage/storageAccounts/${local.storage}"
  app_id      = "${local.group_id}/providers/Microsoft.Web/sites/${local.app}"
  identity_id = "${local.group_id}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/clusterfork-github-deploy"
  tables = toset([
    "ClusterforkAccounts", "ClusterforkDispatches", "ClusterforkInstructions", "ClusterforkLinearSync",
    "ClusterforkOperations", "ClusterforkReadiness", "ClusterforkRepositoryQueue", "ClusterforkWorkers"
  ])
  containers = toset([
    "app-package-clusterforkdispatch7604cac329-5663218", "azure-webjobs-hosts", "azure-webjobs-secrets"
  ])
  secrets = toset(keys(var.credential_versions))
}

resource "azurerm_resource_group" "automation" {
  name     = local.group
  location = local.location
  tags     = { application = "clusterfork-routing-pilot" }
  lifecycle { prevent_destroy = true }
}

resource "azurerm_storage_account" "runtime" {
  name                            = local.storage
  resource_group_name             = azurerm_resource_group.automation.name
  location                        = local.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  account_kind                    = "StorageV2"
  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  allow_nested_items_to_be_public = false
  lifecycle { prevent_destroy = true }
}

resource "azurerm_storage_container" "runtime" {
  for_each              = local.containers
  name                  = each.key
  storage_account_id    = azurerm_storage_account.runtime.id
  container_access_type = "private"
  lifecycle { prevent_destroy = true }
}

resource "azurerm_storage_queue" "events" {
  for_each           = toset(["clusterfork-events", "clusterfork-events-poison"])
  name               = each.key
  storage_account_id = azurerm_storage_account.runtime.id
  lifecycle { prevent_destroy = true }
}

resource "azurerm_storage_table" "runtime" {
  for_each           = local.tables
  name               = each.key
  storage_account_id = azurerm_storage_account.runtime.id
  lifecycle { prevent_destroy = true }
}

resource "azurerm_service_plan" "functions" {
  name                = "ASP-rgclusterforkautomation-6178"
  resource_group_name = azurerm_resource_group.automation.name
  location            = local.location
  os_type             = "Linux"
  sku_name            = "FC1"
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

resource "azurerm_role_assignment" "deploy_website" {
  name                 = "ab552766-babf-40dc-a97a-ec2a1ed62fae"
  scope                = azurerm_function_app_flex_consumption.broker.id
  role_definition_name = "Website Contributor"
  principal_id         = azurerm_user_assigned_identity.deploy.principal_id
}

resource "azurerm_role_assignment" "deploy_control" {
  name                 = "9f7dccac-efc2-413a-a81e-6840039a4b12"
  scope                = azurerm_storage_table.runtime["ClusterforkOperations"].id
  role_definition_name = "Storage Table Data Contributor"
  principal_id         = azurerm_user_assigned_identity.deploy.principal_id
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

resource "azurerm_function_app_flex_consumption" "broker" {
  name                                           = local.app
  resource_group_name                            = azurerm_resource_group.automation.name
  location                                       = local.location
  service_plan_id                                = azurerm_service_plan.functions.id
  storage_container_type                         = "blobContainer"
  storage_container_endpoint                     = azurerm_storage_container.runtime["app-package-clusterforkdispatch7604cac329-5663218"].url
  storage_authentication_type                    = "StorageAccountConnectionString"
  storage_access_key                             = azurerm_storage_account.runtime.primary_access_key
  runtime_name                                   = "python"
  runtime_version                                = "3.12"
  maximum_instance_count                         = 40
  instance_memory_in_mb                          = 512
  https_only                                     = false
  client_certificate_mode                        = "Required"
  webdeploy_publish_basic_authentication_enabled = false
  identity { type = "SystemAssigned" }
  site_config {
    application_insights_connection_string = azurerm_application_insights.automation.connection_string
  }
  # The provider owns AzureWebJobsStorage and DEPLOYMENT_STORAGE_CONNECTION_STRING
  # through storage_access_key; repeating them here produces perpetual drift.
  app_settings = merge({
    GITHUB_APP_ID                          = "5503155"
    LINEAR_OAUTH_CLIENT_ID                 = "9f7805b369221d513ecad1d2559095ef"
    "AzureWebJobs.reconcile.Disabled"      = "true"
    "AzureWebJobs.route_event.Disabled"    = "true"
    "AzureWebJobs.linear_webhook.Disabled" = "true"
    "AzureWebJobs.worker.Disabled"         = "true"
  }, { for name, secret in azurerm_key_vault_secret.runtime : name => "@Microsoft.KeyVault(SecretUri=${secret.versionless_id})" })
  lifecycle { prevent_destroy = true }
}

# Read after the imported app enables its identity; its pre-import identity is null.
data "azurerm_linux_function_app" "broker_identity" {
  count               = var.adopting_identity ? 1 : 0
  name                = azurerm_function_app_flex_consumption.broker.name
  resource_group_name = azurerm_resource_group.automation.name
  depends_on          = [azurerm_function_app_flex_consumption.broker]
}

resource "azurerm_role_assignment" "broker_secrets" {
  scope                = azurerm_key_vault.runtime.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = var.adopting_identity ? data.azurerm_linux_function_app.broker_identity[0].identity[0].principal_id : azurerm_function_app_flex_consumption.broker.identity[0].principal_id
}

output "function_hostname" { value = azurerm_function_app_flex_consumption.broker.default_hostname }
output "deployment_client_id" { value = azurerm_user_assigned_identity.deploy.client_id }
