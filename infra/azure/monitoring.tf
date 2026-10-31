# Direct ingestion only: neither a journal agent nor database/log file uploads.
locals {
  controller_columns = jsondecode(file("${path.module}/monitoring/schema.json"))
  controller_queries = {
    for name in fileset("${path.module}/monitoring", "*.kql") : trimsuffix(name, ".kql") =>
    replace(file("${path.module}/monitoring/${name}"), "__EVENTS__", file("${path.module}/monitoring/events.kql"))
  }
}

# AzureRM's workspace_table resource manages retention of existing tables only.
# AzAPI owns the custom table's schema as well as its unchanged 30-day retention.
resource "azapi_resource" "controller_table" {
  type      = "Microsoft.OperationalInsights/workspaces/tables@2022-10-01"
  name      = "ClusterforkController_CL"
  parent_id = azurerm_log_analytics_workspace.automation.id
  body = {
    properties = {
      schema               = { name = "ClusterforkController_CL", columns = local.controller_columns }
      plan                 = "Analytics"
      retentionInDays      = azurerm_log_analytics_workspace.automation.retention_in_days
      totalRetentionInDays = azurerm_log_analytics_workspace.automation.retention_in_days
    }
  }
  lifecycle { prevent_destroy = true }
}

resource "azurerm_monitor_data_collection_rule" "controller" {
  name                = "clusterfork-controller"
  resource_group_name = azurerm_resource_group.automation.name
  location            = azurerm_log_analytics_workspace.automation.location
  kind                = "Direct"
  destinations {
    log_analytics {
      name                  = "controller"
      workspace_resource_id = azurerm_log_analytics_workspace.automation.id
    }
  }
  stream_declaration {
    stream_name = "Custom-ClusterforkController"
    dynamic "column" {
      for_each = local.controller_columns
      content {
        name = column.value.name
        type = column.value.type
      }
    }
  }
  data_flow {
    streams       = ["Custom-ClusterforkController"]
    destinations  = ["controller"]
    transform_kql = "source"
    output_stream = "Custom-ClusterforkController_CL"
  }
  depends_on = [azapi_resource.controller_table]
}

resource "azurerm_role_assignment" "controller_telemetry" {
  scope                = azurerm_monitor_data_collection_rule.controller.id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = azurerm_linux_virtual_machine.controller.identity[0].principal_id
}

# The existing trusted deployment job queries its smoke record. Workers receive
# neither this identity nor workspace query permissions.
resource "azurerm_role_assignment" "deploy_monitoring_reader" {
  scope                = azurerm_log_analytics_workspace.automation.id
  role_definition_name = "Log Analytics Reader"
  principal_id         = azurerm_user_assigned_identity.deploy.principal_id
}

resource "azurerm_application_insights_workbook" "controller" {
  name                = "2a9df7db-106b-5389-8446-178dd108e9ec"
  resource_group_name = azurerm_resource_group.automation.name
  location            = azurerm_log_analytics_workspace.automation.location
  display_name        = "Clusterfork controller"
  source_id           = lower(azurerm_log_analytics_workspace.automation.id)
  data_json = templatefile("${path.module}/monitoring/workbook.json.tftpl", {
    workspace_id = azurerm_log_analytics_workspace.automation.id
    insights_id  = azurerm_application_insights.automation.id
    queries      = local.controller_queries
  })
  tags       = { application = "clusterfork" }
  depends_on = [azapi_resource.controller_table]
}

output "controller_monitoring" {
  value = {
    workspace_id   = azurerm_log_analytics_workspace.automation.workspace_id
    workbook_id    = azurerm_application_insights_workbook.controller.id
    workbook_url   = "https://portal.azure.com/#@${var.tenant_id}/resource${azurerm_application_insights_workbook.controller.id}/workbook"
    resource_group = azurerm_resource_group.automation.name
  }
}
