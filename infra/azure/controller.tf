# One authorized task owner. Application releases do not recreate this host/disk.
locals {
  controller_name = "clusterfork-controller-7604cac329"
}

variable "controller_location" {
  type    = string
  default = "mexicocentral"
}
variable "controller_size" {
  type    = string
  default = "Standard_B2ats_v2"
}

resource "azurerm_virtual_network" "controller" {
  name                = local.controller_name
  resource_group_name = azurerm_resource_group.automation.name
  location            = var.controller_location
  address_space       = ["10.43.0.0/24"]
}

resource "azurerm_subnet" "controller" {
  name                 = "controller"
  resource_group_name  = azurerm_resource_group.automation.name
  virtual_network_name = azurerm_virtual_network.controller.name
  address_prefixes     = ["10.43.0.0/26"]
}

resource "azurerm_network_security_group" "controller" {
  name                = local.controller_name
  resource_group_name = azurerm_resource_group.automation.name
  location            = var.controller_location
  security_rule {
    name                       = "public-https-and-certificate-validation"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_ranges    = ["80", "443"]
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }
}

resource "azurerm_public_ip" "controller" {
  name                = local.controller_name
  resource_group_name = azurerm_resource_group.automation.name
  location            = var.controller_location
  allocation_method   = "Static"
  sku                 = "Standard"
  domain_name_label   = local.controller_name
  lifecycle { prevent_destroy = true }
}

resource "azurerm_network_interface" "controller" {
  name                = local.controller_name
  resource_group_name = azurerm_resource_group.automation.name
  location            = var.controller_location
  ip_configuration {
    name                          = "controller"
    subnet_id                     = azurerm_subnet.controller.id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.controller.id
  }
}
resource "azurerm_network_interface_security_group_association" "controller" {
  network_interface_id      = azurerm_network_interface.controller.id
  network_security_group_id = azurerm_network_security_group.controller.id
}

resource "azurerm_linux_virtual_machine" "controller" {
  name                            = local.controller_name
  resource_group_name             = azurerm_resource_group.automation.name
  location                        = var.controller_location
  size                            = var.controller_size
  admin_username                  = "vmadmin"
  disable_password_authentication = true
  network_interface_ids           = [azurerm_network_interface.controller.id]
  identity { type = "SystemAssigned" }
  admin_ssh_key {
    username   = "vmadmin"
    public_key = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCmc+btPfjlWHHmB2bQc/dbpLWWbFG1uhD1cSIymoyoh+vtfMOmaC9/NB25LCaV3ZejUo/FUPdtnPoVTGhFU3QjB2kuMvaIOF2UytKRpq+J9fiJvabd8KiXMJW6sgigH9Qb0UaQgKjxHZts7QtWnLw9pB0oPhv6gmyeiST9Ar9McBHvP5b0TO1ibDX0IWO4oJCSYTMmpCCj14eGAGT7iEQeO8SAC9gsFYBMw1sTU26PU5RJPUVeZwR8N21wAmKYEmntcnQJUBSj1tFuH+Pifv9LbMv9lriktEDGr71tVCXELia2BFhYC6UKkqwvlhyvQWVFdMBYjAjo0QnoMba5bO6h"
  }
  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
    disk_size_gb         = 32
  }
  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server"
    version   = "24.04.202610210"
  }
  lifecycle { prevent_destroy = true }
}

resource "azurerm_managed_disk" "controller_tasks" {
  name                          = "${local.controller_name}-tasks"
  resource_group_name           = azurerm_resource_group.automation.name
  location                      = var.controller_location
  storage_account_type          = "StandardSSD_LRS"
  create_option                 = "Empty"
  disk_size_gb                  = 32
  network_access_policy         = "DenyAll"
  public_network_access_enabled = false
  lifecycle { prevent_destroy = true }
}
resource "azurerm_virtual_machine_data_disk_attachment" "controller_tasks" {
  managed_disk_id    = azurerm_managed_disk.controller_tasks.id
  virtual_machine_id = azurerm_linux_virtual_machine.controller.id
  lun                = 0
  caching            = "None"
}
resource "azurerm_role_assignment" "controller_accounts" {
  for_each             = toset(["ClusterforkAccounts", "ClusterforkInstructions"])
  scope                = azurerm_storage_table.runtime[each.key].id
  role_definition_name = "Storage Table Data Contributor"
  principal_id         = azurerm_linux_virtual_machine.controller.identity[0].principal_id
}
resource "azurerm_role_assignment" "controller_secrets" {
  scope                = azurerm_key_vault.runtime.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_linux_virtual_machine.controller.identity[0].principal_id
}
output "controller_vm_id" { value = azurerm_linux_virtual_machine.controller.id }
output "controller_hostname" { value = azurerm_public_ip.controller.fqdn }
output "controller_config" {
  value = {
    vm_resource_id         = azurerm_linux_virtual_machine.controller.id
    storage_account        = azurerm_storage_account.runtime.name
    key_vault              = azurerm_key_vault.runtime.name
    github_app_id          = "5503155"
    linear_oauth_client_id = "9f7805b369221d513ecad1d2559095ef"
    hostname               = azurerm_public_ip.controller.fqdn
    azure_tenant_id        = var.tenant_id
    account_operators      = [var.operator_object_id]
  }
}
