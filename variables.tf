variable "cloud_id" {
  type        = string
  description = "ID облака Yandex Cloud."
}

variable "folder_id" {
  type        = string
  description = "ID каталога Yandex Cloud."
}

variable "bucket_name" {
  type        = string
  description = "Глобально уникальное имя бакета: vvotNN-<суффикс>"
  validation {
    condition     = can(regex("^vvot[0-9]{2}-[a-z0-9][a-z0-9-]*[a-z0-9]$", var.bucket_name)) && length(var.bucket_name) <= 63
    error_message = "Имя должно иметь вид vvotNN-<суффикс>, содержать строчные латинские буквы, цифры, дефисы и быть не длиннее 63 символов."
  }
}
