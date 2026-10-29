resource "azurerm_resource_group" "state" {
  name     = "rg-clusterfork-tfstate"
  location = "eastus"
  lifecycle { prevent_destroy = true }
}
resource "azurerm_storage_account" "state" {
  name                            = "cftfstate7604cac329"
  resource_group_name             = azurerm_resource_group.state.name
  location                        = azurerm_resource_group.state.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  shared_access_key_enabled       = false
  allow_nested_items_to_be_public = false
  min_tls_version                 = "TLS1_2"
  default_to_oauth_authentication = true
  blob_properties {
    versioning_enabled = true
    delete_retention_policy { days = 30 }
    container_delete_retention_policy { days = 30 }
  }
  lifecycle { prevent_destroy = true }
}
resource "azurerm_storage_container" "state" {
  for_each              = toset(["bootstrap-state", "automation-state"])
  name                  = each.key
  storage_account_id    = azurerm_storage_account.state.id
  container_access_type = "private"
  lifecycle { prevent_destroy = true }
}
resource "azurerm_role_assignment" "operator" {
  for_each             = azurerm_storage_container.state
  scope                = each.value.id
  role_definition_name = "Storage Blob Data Owner"
  principal_id         = "c76fa8df-010d-45bb-b764-96392e7aca34"
}
resource "azurerm_role_assignment" "deployment" {
  scope                = azurerm_storage_container.state["automation-state"].id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = "8fadc2dc-659e-491f-98e5-0f6981bd9d61"
}
