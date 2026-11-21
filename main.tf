locals {
  prefix = substr(var.bucket_name, 0, 7)
  roles = {
    provisioner = ["ymq.admin", "storage.admin", "ydb.editor"]
    gateway     = ["ymq.writer", "ydb.viewer", "storage.viewer"]
    worker      = ["storage.uploader", "ydb.editor"]
    trigger     = ["ymq.reader", "functions.functionInvoker"]
  }
  grants = merge([
    for account, roles in local.roles : {
      for role in roles : "${account}/${role}" => { account = account, role = role }
    }
  ]...)
}

resource "yandex_iam_service_account" "accounts" {
  for_each = local.roles
  name     = "${local.prefix}${each.key}"
}

resource "yandex_resourcemanager_folder_iam_member" "grants" {
  for_each  = local.grants
  folder_id = var.folder_id
  role      = each.value.role
  member    = "serviceAccount:${yandex_iam_service_account.accounts[each.value.account].id}"
}

resource "time_sleep" "iam" {
  depends_on      = [yandex_resourcemanager_folder_iam_member.grants]
  create_duration = "30s"
}

resource "yandex_iam_service_account_static_access_key" "provisioner" {
  service_account_id = yandex_iam_service_account.accounts["provisioner"].id
  description        = "${local.prefix}terraform-access"
  depends_on         = [time_sleep.iam]
}

resource "yandex_iam_service_account_static_access_key" "worker" {
  service_account_id = yandex_iam_service_account.accounts["worker"].id
  description        = "${local.prefix}worker-access"
  depends_on         = [time_sleep.iam]
}

resource "yandex_storage_bucket" "archives" {
  bucket        = var.bucket_name
  access_key    = yandex_iam_service_account_static_access_key.provisioner.access_key
  secret_key    = yandex_iam_service_account_static_access_key.provisioner.secret_key
  max_size      = 10737418240
  force_destroy = true
  anonymous_access_flags {
    read        = false
    list        = false
    config_read = false
  }
}

resource "yandex_message_queue" "dead_letter" {
  name                      = "${local.prefix}dead-letter"
  access_key                = yandex_iam_service_account_static_access_key.provisioner.access_key
  secret_key                = yandex_iam_service_account_static_access_key.provisioner.secret_key
  message_retention_seconds = 1209600
}

resource "yandex_message_queue" "tasks" {
  name                       = "${local.prefix}tasks"
  access_key                 = yandex_iam_service_account_static_access_key.provisioner.access_key
  secret_key                 = yandex_iam_service_account_static_access_key.provisioner.secret_key
  visibility_timeout_seconds = 900
  receive_wait_time_seconds  = 20
  message_retention_seconds  = 86400
  redrive_policy = jsonencode({
    deadLetterTargetArn = yandex_message_queue.dead_letter.arn
    maxReceiveCount     = 1
  })
}

resource "yandex_ydb_database_serverless" "archives" {
  name = "${local.prefix}database"
  serverless_database {
    enable_throttling_rcu_limit = true
    throttling_rcu_limit        = 10
    storage_size_limit          = 1
  }
}

resource "aws_dynamodb_table" "archives" {
  name         = "archives"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "key"
  attribute {
    name = "key"
    type = "S"
  }
  depends_on = [time_sleep.iam]
}

data "archive_file" "worker" {
  type        = "zip"
  source_dir  = "${path.module}/function"
  output_path = "${path.module}/.build/function.zip"
}

resource "yandex_function" "worker" {
  name               = "${local.prefix}archiver"
  runtime            = "python312"
  entrypoint         = "index.handler"
  memory             = 256
  execution_timeout  = "600"
  service_account_id = yandex_iam_service_account.accounts["worker"].id
  user_hash          = data.archive_file.worker.output_base64sha256
  content {
    zip_filename = data.archive_file.worker.output_path
  }
  environment = {
    BUCKET_NAME           = yandex_storage_bucket.archives.bucket
    YDB_DOCUMENT_ENDPOINT = yandex_ydb_database_serverless.archives.document_api_endpoint
    TABLE_NAME            = aws_dynamodb_table.archives.name
    ACCESS_KEY_ID         = yandex_iam_service_account_static_access_key.worker.access_key
    SECRET_ACCESS_KEY     = yandex_iam_service_account_static_access_key.worker.secret_key
  }
}

resource "yandex_function_trigger" "tasks" {
  name = "${local.prefix}queue-trigger"
  message_queue {
    queue_id           = yandex_message_queue.tasks.arn
    service_account_id = yandex_iam_service_account.accounts["trigger"].id
    batch_size         = "1"
    batch_cutoff       = "0"
    visibility_timeout = "900"
  }
  function {
    id                 = yandex_function.worker.id
    service_account_id = yandex_iam_service_account.accounts["trigger"].id
  }
  depends_on = [time_sleep.iam]
}

resource "yandex_api_gateway" "archives" {
  name = "${local.prefix}api"
  spec = templatefile("${path.module}/openapi.yaml", {
    gateway_service_account_id = yandex_iam_service_account.accounts["gateway"].id
    queue_url                  = yandex_message_queue.tasks.id
    folder_id                  = var.folder_id
    database_path              = yandex_ydb_database_serverless.archives.database_path
    bucket_name                = yandex_storage_bucket.archives.bucket
  })
  depends_on = [aws_dynamodb_table.archives, time_sleep.iam]
}
