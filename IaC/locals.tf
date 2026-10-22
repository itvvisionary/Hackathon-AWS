# locals.tf
# Purpose: Centralised naming and tagging logic. Every resource name and the
# common tag set is derived here so naming stays consistent and configurable.

locals {
  # Standard prefix: <project>-<environment>, e.g. "wa-ai-rpa-dev"
  name_prefix = "${var.project_name}-${var.environment}"

  # Common tags merged into the provider default_tags and available to modules.
  common_tags = merge(
    {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
      Component   = "whatsapp-ai-rpa"
    },
    var.tags,
  )

  # DynamoDB table names: "<prefix>_<key>_table" unless overridden per table.
  dynamodb_table_name_prefix = (
    var.dynamodb_table_name_prefix != ""
    ? var.dynamodb_table_name_prefix
    : replace(local.name_prefix, "-", "_")
  )

  dynamodb_tables = {
    for key, cfg in var.dynamodb_tables : key => merge(cfg, {
      name = coalesce(cfg.name, "${local.dynamodb_table_name_prefix}_${key}_table")
    })
  }
}
