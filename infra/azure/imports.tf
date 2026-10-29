# Adoption receipts are safe to retain after import. They never manage table rows or blob contents.
import {
  for_each = var.adopt_existing ? toset(["existing"]) : toset([])
  to       = azurerm_resource_group.automation
  id       = local.group_id
}
import {
  for_each = var.adopt_existing ? toset(["existing"]) : toset([])
  to       = azurerm_storage_account.runtime
  id       = local.storage_id
}
import {
  for_each = var.adopt_existing ? local.containers : toset([])
  to       = azurerm_storage_container.runtime[each.key]
  id       = "${local.storage_id}/blobServices/default/containers/${each.key}"
}
import {
  for_each = var.adopt_existing ? toset(["existing"]) : toset([])
  to       = azurerm_storage_queue.events["clusterfork-events"]
  id       = "${local.storage_id}/queueServices/default/queues/clusterfork-events"
}
import {
  for_each = var.adopt_existing ? local.tables : toset([])
  to       = azurerm_storage_table.runtime[each.key]
  id       = "${local.storage_id}/tableServices/default/tables/${each.key}"
}
import {
  for_each = var.adopt_existing ? toset(["existing"]) : toset([])
  to       = azurerm_service_plan.functions
  id       = "${local.group_id}/providers/Microsoft.Web/serverFarms/ASP-rgclusterforkautomation-6178"
}
import {
  for_each = var.adopt_existing ? toset(["existing"]) : toset([])
  to       = azurerm_function_app_flex_consumption.broker
  id       = local.app_id
}
import {
  for_each = var.adopt_existing ? toset(["existing"]) : toset([])
  to       = azurerm_log_analytics_workspace.automation
  id       = "${local.group_id}/providers/Microsoft.OperationalInsights/workspaces/${local.app}-logs"
}
import {
  for_each = var.adopt_existing ? toset(["existing"]) : toset([])
  to       = azurerm_application_insights.automation
  id       = "${local.group_id}/providers/Microsoft.Insights/components/${local.app}-insights"
}
import {
  for_each = var.adopt_existing ? toset(["existing"]) : toset([])
  to       = azurerm_monitor_action_group.smart_detection
  id       = "${local.group_id}/providers/Microsoft.Insights/actionGroups/Application Insights Smart Detection"
}
import {
  for_each = var.adopt_existing ? toset(["existing"]) : toset([])
  to       = azurerm_user_assigned_identity.deploy
  id       = local.identity_id
}
import {
  for_each = var.adopt_existing ? toset(["existing"]) : toset([])
  to       = azurerm_federated_identity_credential.github
  id       = "${local.identity_id}/federatedIdentityCredentials/github-azure-production"
}
import {
  for_each = var.adopt_existing ? toset(["existing"]) : toset([])
  to       = azurerm_role_assignment.deploy_website
  id       = "${local.app_id}/providers/Microsoft.Authorization/roleAssignments/ab552766-babf-40dc-a97a-ec2a1ed62fae"
}
import {
  for_each = var.adopt_existing ? toset(["existing"]) : toset([])
  to       = azurerm_role_assignment.deploy_control
  id       = "${local.storage_id}/tableServices/default/tables/ClusterforkOperations/providers/Microsoft.Authorization/roleAssignments/9f7dccac-efc2-413a-a81e-6840039a4b12"
}
