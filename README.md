# Public API Monitor — Python, PostgreSQL, GitHub Actions

Небольшой сервис мониторинга открытого REST API. Приложение сразу после запуска делает HTTP GET-запрос, а затем повторяет его с интервалом `INTERVAL_SECONDS`. Результаты записываются в PostgreSQL: время, URL, HTTP-статус, длительность, JSON-ответ или ошибка.

## Стек

- Python 3 и `requests` для HTTP.
- `psycopg` для PostgreSQL.
- `systemd` для постоянной работы сервиса на Ubuntu.
- GitHub Actions для CI и доставки релизов по SSH.

## 1. Создай PostgreSQL-базу

На сервере:

```bash
sudo -u postgres psql
```

В консоли PostgreSQL:

```sql
CREATE ROLE api_monitor_app LOGIN;
\password api_monitor_app
CREATE DATABASE api_monitor OWNER api_monitor_app ENCODING 'UTF8' TEMPLATE template0;
\q
```

Введи надёжный пароль по запросу команды `\password`. Если роль или база уже созданы, не выполняй `CREATE` повторно.

## 2. Подготовь сервер (один раз)

Скопируй эти три файла в одну временную папку сервера: `api-monitor.service`, `scripts/deploy-api-monitor.sh`, `scripts/bootstrap-server.sh`. Например, со своего компьютера:

```bash
scp api-monitor.service scripts/deploy-api-monitor.sh scripts/bootstrap-server.sh ubuntu@SERVER_IP:/tmp/
ssh ubuntu@SERVER_IP
```

На сервере:

```bash
sudo mkdir -p /tmp/api-monitor-bootstrap
sudo install -m 0644 /tmp/api-monitor.service /tmp/api-monitor-bootstrap/api-monitor.service
sudo install -m 0750 /tmp/deploy-api-monitor.sh /tmp/api-monitor-bootstrap/deploy-api-monitor.sh
sudo install -m 0750 /tmp/bootstrap-server.sh /tmp/api-monitor-bootstrap/bootstrap-server.sh
sudo bash /tmp/api-monitor-bootstrap/bootstrap-server.sh
```

Укажи своего SSH-пользователя и публичный IP вместо `ubuntu` и `SERVER_IP`. Скрипт создаст системных пользователей `api-monitor` и `api-deploy`, установит systemd unit и ограниченное sudo-правило для скрипта деплоя.

Настрой файл окружения сервиса:

```bash
sudo nano /etc/api-monitor.env
```

Поля должны соответствовать созданной PostgreSQL-базе:

```ini
DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=api_monitor
DB_USER=api_monitor_app
DB_PASSWORD=ВСТАВЬ_СОЗДАННЫЙ_ПАРОЛЬ
API_URL=https://jsonplaceholder.typicode.com/posts/1
INTERVAL_SECONDS=300
REQUEST_TIMEOUT_SECONDS=20
```

Затем:

```bash
sudo chown root:root /etc/api-monitor.env
sudo chmod 600 /etc/api-monitor.env
```

Не сохраняй этот файл в Git.

## 3. Настрой SSH для GitHub Actions

Создай отдельную пару ключей на своём компьютере (не используй личный административный ключ):

```bash
ssh-keygen -t ed25519 -C "github-actions-api-monitor" -f ~/.ssh/api-monitor-github-actions
```

Приватный ключ — `api-monitor-github-actions`; публичный — `api-monitor-github-actions.pub`. Для автоматического подключения приватный ключ обычно создают без passphrase и хранят только в GitHub Secrets.

Установи публичный ключ для пользователя `api-deploy` на сервере. Например, из PowerShell:

```powershell
scp "$env:USERPROFILE\.ssh\api-monitor-github-actions.pub" ubuntu@SERVER_IP:/tmp/api-monitor-github-actions.pub
ssh ubuntu@SERVER_IP "sudo install -o api-deploy -g api-deploy -m 0600 /tmp/api-monitor-github-actions.pub /home/api-deploy/.ssh/authorized_keys && sudo rm -f /tmp/api-monitor-github-actions.pub"
```

Полученный пользователь имеет право запускать через sudo только ограниченный deploy-скрипт.

Для безопасной проверки SSH host key сравни отпечаток ключа сервера и только после проверки добавь host key в GitHub Secret `DEPLOY_KNOWN_HOSTS`:

```bash
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
ssh-keyscan -H -t ed25519 SERVER_IP 2>/dev/null
```

Сверь fingerprints из доверенного источника/на самом сервере; не отключай `StrictHostKeyChecking`.

## 4. GitHub Secrets

В репозитории открой **Settings → Secrets and variables → Actions** и добавь:

| Secret | Значение |
|---|---|
| `DEPLOY_HOST` | Публичный IP или DNS сервера |
| `DEPLOY_PORT` | SSH-порт (необязательно, по умолчанию 22) |
| `DEPLOY_USER` | `api-deploy` |
| `DEPLOY_SSH_KEY` | Полный текст приватного ключа |
| `DEPLOY_KNOWN_HOSTS` | Проверенная строка host key |

Workflow запускает проверки на pull request. После успешной проверки push в `main` выполняет деплой.

## 5. Как проходит деплой

1. GitHub Actions устанавливает зависимости, проверяет синтаксис, запускает unit-тесты и `pip check`.
2. По SSH отправляет `main.py` и `requirements.txt` в каталог пользователя деплоя.
3. На сервере создаётся отдельный release в `/opt/api-monitor/releases/` и виртуальное окружение.
4. Переключается симлинк `/opt/api-monitor/current`, после чего перезапускается `systemd`.
5. Если сервис не стартует, скрипт пытается восстановить предыдущую версию. Хранятся пять последних релизов.

Для первого запуска GitHub Secrets должны быть заполнены, а bootstrap сервера завершён. Секреты не следует передавать через коммиты, workflow output или обычные файлы репозитория.

## 6. Проверка

```bash
sudo systemctl status api-monitor
sudo journalctl -u api-monitor -n 100 --no-pager
```

Записи в PostgreSQL:

```bash
psql -h 127.0.0.1 -U api_monitor_app -d api_monitor -W
```

```sql
SELECT id, checked_at, api_url, http_status, duration_ms, error_message
FROM api_request_logs
ORDER BY id DESC
LIMIT 20;
```

PostgreSQL слушать в интернете не требуется: приложение подключается к нему через `127.0.0.1`.
