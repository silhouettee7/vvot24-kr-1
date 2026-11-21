
## Подготовка

Нужны Terraform >= 1.5, Python >= 3.10, Yandex Cloud CLI (`yc`), `curl`; для ручных примеров — `jq` и `unzip`.

Открыть [terraform.tfvars](terraform.tfvars) и заполнить `cloud_id`, `folder_id` и `bucket_name`.

```hcl
cloud_id    = "ID облака"
folder_id   = "ID каталога"
bucket_name = "vvotNN-..."
```

ID можно узнать командами:

```bash
yc config get cloud-id
yc config get folder-id
```

Аутентификация Terraform:

```bash
export YC_TOKEN="$(yc iam create-token)"
```

## Развёртывание

```bash
terraform init
terraform fmt -check
terraform validate
terraform plan -out=deploy.tfplan
terraform apply deploy.tfplan
export API_URL="$(terraform output -raw api_url)"
printf '%s\n' "$API_URL"
```

Допустимые версии заданы в [versions.tf](versions.tf).

## Проверка

Полная проверка развёрнутого API:

```bash
python3 scripts/smoke_test.py "$API_URL"
```

Скрипт проверяет `400` для неверных заданий, `202` для принятого задания, ожидает появления записи, проверяет UUIDv7, метаданные, ZIP и ответы `404`. По умолчанию архивируется `https://www.example.com/`; другой небольшой публичный файл можно указать через `--source-url URL`. 

### Ручная проверка

Создать задание:

```bash
curl -i -X POST "$API_URL/tasks" \
  -H 'Content-Type: application/json' \
  -d '{"name":"example.html","url":"https://www.example.com/"}'
```

Получить список:

```bash
curl -fsS "$API_URL/archives" | jq
```

Затем:

```bash
export KEY="$(curl -fsS "$API_URL/archives" | jq -r '[.[] | select(.name == "example.html")] | last | .key // empty')"
test -n "$KEY"
curl -fsS "$API_URL/archives/$KEY" | jq
curl -fS "$API_URL/download/$KEY" -o result.zip
unzip -l result.zip
unzip -p result.zip example.html
```

Валидация и отсутствие архива:

```bash
curl -i -X POST "$API_URL/tasks" \
  -H 'Content-Type: application/json' -d '{"name":"missing-url"}'
curl -i "$API_URL/archives/00000000-0000-7000-8000-000000000000.zip"
curl -i "$API_URL/download/00000000-0000-7000-8000-000000000000.zip"
```

Ожидаются соответственно `400`, `404`, `404`. 

### Проверка ошибки и DLQ


```bash
curl -i -X POST "$API_URL/tasks" \
  -H 'Content-Type: application/json' \
  -d '{"name":"failure.txt","url":"https://does-not-exist.invalid/file.txt"}'
```

Шлюз ответит `202`, поскольку JSON корректен. Функция завершится ошибкой DNS; записи в `archives` не будет. В консоли открыть очередь `vvotNN-dead-letter`; также можно проверить метрики ошибок функции. Перенос не мгновенный: при visibility timeout 900 секунд нужно дождаться повторной доступности сообщения (около 15 минут и опроса очереди).

```bash
terraform output dead_letter_queue_url
```

## Удаление ресурсов

Команда удалит все ресурсы этой конфигурации, включая содержимое бакета и таблицы (`force_destroy = true`).

```bash
export YC_TOKEN="$(yc iam create-token)"
terraform plan -destroy -out=destroy.tfplan
terraform apply destroy.tfplan
terraform state list
```
