output "api_url" {
  value = "https://${yandex_api_gateway.archives.domain}"
}
output "function_id" {
  value = yandex_function.worker.id
}
output "queue_url" {
  value = yandex_message_queue.tasks.id
}
output "dead_letter_queue_url" {
  value = yandex_message_queue.dead_letter.id
}
output "database_id" {
  value = yandex_ydb_database_serverless.archives.id
}
output "bucket_name" {
  value = yandex_storage_bucket.archives.bucket
}
