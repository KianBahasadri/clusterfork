terraform {
  required_version = "~> 1.15.9"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "5.12.0"
    }
  }
}
provider "azurerm" {
  features {}
  subscription_id                 = "37d24562-0c19-408a-b492-f765921e443a"
  tenant_id                       = "d61ecb3b-38b1-42d5-82c4-efb2838b925c"
  resource_provider_registrations = "none"
  storage_use_azuread             = true
}
