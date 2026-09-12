# Automatic production deploy

После настройки любой push/merge в `main` проходит обычный workflow `tests`. Если тесты зелёные, workflow `deploy-production` подключается к VM по SSH и разворачивает ровно тот commit, который прошёл тесты.

## Что делает deploy

1. Загружает target commit из `main`.
2. Не допускает отката на более старый commit из запоздавшего workflow.
3. Не трогает `.env` и `data/`.
4. Обновляет Python-зависимости только при изменении `requirements.txt`.
5. Проверяет Python-код через `compileall`.
6. Перезапускает только `personal-secretary.service`.
7. Проверяет `http://127.0.0.1:8080/api/health`.
8. Если health-check не проходит, возвращает предыдущий commit и перезапускает старую версию.

## Одноразовая настройка VM

Выполнять под серверным пользователем, который владеет `~/bot_project`.

Проверь путь `systemctl`:

```bash
command -v systemctl
```

Для Ubuntu обычно это `/usr/bin/systemctl`.

Разреши этому пользователю без пароля перезапускать только сервис приложения. Если пользователь `stavr106` и `systemctl` находится в `/usr/bin/systemctl`:

```bash
echo 'stavr106 ALL=(root) NOPASSWD: /usr/bin/systemctl restart personal-secretary' | sudo tee /etc/sudoers.d/personal-secretary-deploy
sudo chmod 440 /etc/sudoers.d/personal-secretary-deploy
sudo visudo -cf /etc/sudoers.d/personal-secretary-deploy
```

Проверка:

```bash
sudo -n systemctl restart personal-secretary
curl -fsS http://127.0.0.1:8080/api/health
```

## Отдельный SSH-ключ для GitHub Actions

На доверенном компьютере создай отдельный ключ без passphrase:

```bash
ssh-keygen -t ed25519 -f github-actions-deploy -C github-actions-deploy -N ''
```

Добавь публичную часть на сервер:

```bash
ssh-copy-id -i github-actions-deploy.pub stavr106@213.171.26.210
```

Проверь вход:

```bash
ssh -i github-actions-deploy stavr106@213.171.26.210 'cd ~/bot_project && git status --short && sudo -n systemctl restart personal-secretary'
```

## GitHub Actions secrets

Repository -> Settings -> Secrets and variables -> Actions -> New repository secret.

Нужны четыре секрета:

- `DEPLOY_HOST` — IP/hostname VM, сейчас `213.171.26.210`.
- `DEPLOY_USER` — серверный пользователь, сейчас `stavr106`.
- `DEPLOY_SSH_KEY` — полное содержимое приватного файла `github-actions-deploy`.
- `DEPLOY_KNOWN_HOSTS` — trusted SSH host key VM.

Получить строку `known_hosts` можно так:

```bash
ssh-keyscan -H 213.171.26.210
```

Для более строгой проверки сначала сравни fingerprint с ключом непосредственно на VM.

После переноса приватного ключа в GitHub Secrets локальный файл лучше удалить:

```bash
rm -f github-actions-deploy github-actions-deploy.pub
```

## Первый запуск

После добавления секретов merge PR с автодеплоем в `main`. Push в `main` запустит `tests`; после успешных тестов автоматически запустится `deploy-production`.

Проверка после первого deploy:

```bash
curl -fsS https://213.171.26.210.sslip.io/api/health
```

В GitHub Actions оба workflow должны завершиться зелёным статусом: сначала `tests`, затем `deploy-production`.
