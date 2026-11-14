# Public API Monitor — FakerAPI, Python, PostgreSQL, GitHub Actions

Небольшой сервис для периодического сбора синтетических данных из [FakerAPI](https://fakerapi.it/en) с сохранением истории запросов и структурированных записей в PostgreSQL.

## Что делает приложение

- Каждые 900 секунд по умолчанию вызывает выбранные endpoints FakerAPI.
- Сохраняет каждый запуск в таблицу api_sync_runs, а каждый HTTP-вызов и очищенный JSON-ответ — в api_request_logs.
- Разбирает восемь ресурсов в отдельные таблицы: faker_addresses, faker_books, faker_companies, faker_persons, faker_places, faker_products, faker_texts и faker_users.
- Сохраняет обычные поля в типизированные столбцы, а вложенные массивы и объекты (например, адреса компании, изображения и категории товара) — в JSONB.
- Записывает каждый набор данных под своим sync_run_id. ID от API могут повторяться в следующих ответах, поэтому не считаются глобально уникальными.
- Не сохраняет поле password из ресурса users; из JSON-логов рекурсивно удаляются поля password/token/secret-подобного типа.
- Продолжает обработку при ошибке одного endpoint: ошибка сохраняется в журнале, а запуск получает статус success, partial_failure или failed.

FakerAPI не требует регистрации или API-ключа. Общие параметры _quantity (1–1000), _locale и _seed описаны в [официальной документации](https://fakerapi.it/en). Это синтетические тестовые данные, а не реальные бизнес-данные.

## Текущее состояние базы

База api_monitor и роль api_monitor_app уже созданы на сервере. Повторять SQL-команды создания базы или роли не надо. При первом старте приложение создаёт таблицы, если их ещё нет.

## 1. Подготовка сервера (один раз)

Скачай ZIP-архив репозитория или клонируй его на свой компьютер:

~~~bash
git clone https://github.com/jamaxm/api-monitor-python.git
cd api-monitor-python
~~~

Если репозиторий приватный и clone по HTTPS не настроен, скачай ZIP через GitHub → Code → Download ZIP.

Из корневой папки проекта передай на сервер три файла (замени SERVER_IP):

~~~bash
scp api-monitor.service scripts/deploy-api-monitor.sh scripts/bootstrap-server.sh ubuntu@SERVER_IP:/tmp/
~~~

Подключись к Ubuntu:

~~~bash
ssh ubuntu@SERVER_IP
~~~

Выполни:

~~~bash
sudo mkdir -p /tmp/api-monitor-bootstrap
sudo install -m 0644 /tmp/api-monitor.service /tmp/api-monitor-bootstrap/api-monitor.service
sudo install -m 0750 /tmp/deploy-api-monitor.sh /tmp/api-monitor-bootstrap/deploy-api-monitor.sh
sudo install -m 0750 /tmp/bootstrap-server.sh /tmp/api-monitor-bootstrap/bootstrap-server.sh
sudo bash /tmp/api-monitor-bootstrap/bootstrap-server.sh
~~~

Скрипт установит Python/venv, создаст системного пользователя приложения api-monitor и отдельного SSH-пользователя деплоя api-deploy, установит systemd unit и ограниченное sudo-правило для deploy-скрипта.

## 2. Настройка PostgreSQL и сборщика

Открой конфигурацию на сервере:

~~~bash
sudo nano /etc/api-monitor.env
~~~

Укажи пароль, который ты задал роли api_monitor_app при создании базы. Пароль не нужно добавлять в GitHub или исходники.

~~~ini
DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=api_monitor
DB_USER=api_monitor_app
DB_PASSWORD=ВСТАВЬ_СВОЙ_ПАРОЛЬ

FAKERAPI_BASE_URL=https://fakerapi.it/api/v1
API_RESOURCES=addresses,books,companies,persons,places,products,texts,users
API_QUANTITY=5
API_LOCALE=en_US
API_SEED=
INTERVAL_SECONDS=900
REQUEST_TIMEOUT_SECONDS=20
~~~

Оставь API_SEED пустым, чтобы запрашивать новые сгенерированные наборы. Если задашь целое число, FakerAPI рассчитан на повторяемый результат для того же seed. Запросы выполняются последовательно, по одному на каждый ресурс.

Сохрани файл (Ctrl+O, Enter, Ctrl+X) и выставь права:

~~~bash
sudo chown root:root /etc/api-monitor.env
sudo chmod 600 /etc/api-monitor.env
~~~

Таблицы вручную создавать не надо: приложение создаст их при запуске, используя пользователя api_monitor_app.

## 3. Настрой SSH для GitHub Actions

Создай на своём компьютере отдельную пару ключей только для этого проекта. Не используй личный административный ключ.

Windows PowerShell:

~~~powershell
ssh-keygen -t ed25519 -C "github-actions-api-monitor" -f "$env:USERPROFILE\.ssh\api-monitor-github-actions"
~~~

Для автоматического подключения приватный ключ без passphrase хранится только в GitHub Secrets. Публичный ключ нужно установить для пользователя api-deploy.

Из PowerShell скопируй публичный ключ на сервер:

~~~powershell
scp "$env:USERPROFILE\.ssh\api-monitor-github-actions.pub" ubuntu@SERVER_IP:/tmp/api-monitor-github-actions.pub
~~~

Затем:

~~~powershell
ssh ubuntu@SERVER_IP "sudo install -o api-deploy -g api-deploy -m 0600 /tmp/api-monitor-github-actions.pub /home/api-deploy/.ssh/authorized_keys && sudo rm -f /tmp/api-monitor-github-actions.pub"
~~~

### Проверка SSH host key

На сервере получи отпечаток ключа:

~~~bash
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
~~~

В локальном PowerShell выполни:

~~~powershell
ssh-keyscan -t ed25519 SERVER_IP 2>$null | ForEach-Object { $parts = $_ -split ' '; "$($parts[1]) $($parts[2])" } | ssh-keygen -lf -
~~~

Продолжай только если SHA256-отпечатки совпали. Затем получи строку known_hosts:

~~~powershell
ssh-keyscan -H -t ed25519 SERVER_IP 2>$null
~~~

Скопируй её для GitHub Secret. Не отключай проверку host key.

## 4. GitHub Secrets

В репозитории открой Settings → Secrets and variables → Actions:  
https://github.com/jamaxm/api-monitor-python/settings/secrets/actions

Добавь:

| Secret | Значение |
|---|---|
| DEPLOY_HOST | Публичный IP/DNS Ubuntu-сервера |
| DEPLOY_USER | api-deploy |
| DEPLOY_SSH_KEY | Полное содержимое приватного файла api-monitor-github-actions (без .pub) |
| DEPLOY_KNOWN_HOSTS | Проверенная строка known_hosts |
| DEPLOY_PORT | Необязательно; по умолчанию 22 |

Не публикуй приватный ключ и пароль БД в чате, исходниках или логах workflow.

Убедись, что правило OCI Security List/NSG и firewall Ubuntu допускают SSH с GitHub-hosted runner. Не открывай PostgreSQL 5432 для интернета. Если SSH доступен только с твоего IP, runner не сможет подключиться: используй runner с контролируемым исходящим IP или VPN.

## 5. Как проходит CI/CD

Файл .github/workflows/deploy.yml запускает CI на Pull Request и push в main: установка зависимостей, компиляция, unit-тесты и pip check. После успешного push в main workflow по SSH отправляет код на сервер, создаёт релиз, устанавливает зависимости в виртуальное окружение, переключает current и перезапускает systemd. Если сервис не активируется, deploy-скрипт пытается вернуть предыдущий релиз. Хранятся пять последних релизов.

## 6. Проверка после деплоя

На сервере:

~~~bash
sudo systemctl status api-monitor --no-pager
sudo journalctl -u api-monitor -n 100 --no-pager
~~~

Посмотри последние запуски сбора:

~~~bash
sudo -u postgres psql -d api_monitor -c "
SELECT id, started_at, finished_at, status,
       resources_succeeded, resources_failed
FROM api_sync_runs
ORDER BY id DESC
LIMIT 10;"
~~~

Посмотри HTTP-вызовы:

~~~bash
sudo -u postgres psql -d api_monitor -c "
SELECT sync_run_id, resource, http_status, duration_ms, error_message, checked_at
FROM api_request_logs
ORDER BY id DESC
LIMIT 20;"
~~~

Посмотри сохранённые данные:

~~~bash
sudo -u postgres psql -d api_monitor -c "SELECT count(*) FROM faker_users;"
sudo -u postgres psql -d api_monitor -c "SELECT id, sync_run_id, firstname, lastname, email FROM faker_persons ORDER BY id DESC LIMIT 10;"
sudo -u postgres psql -d api_monitor -c "SELECT id, sync_run_id, name, price, categories FROM faker_products ORDER BY id DESC LIMIT 10;"
~~~

Первый запуск создаёт схему и выполняет запросы к выбранным ресурсам. Каждый следующий запуск получает новый sync_run_id, поэтому историю загрузок можно анализировать. Если объём данных станет слишком большим, уменьши список ресурсов/число строк или увеличь INTERVAL_SECONDS.
