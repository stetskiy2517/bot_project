# Telegram-бот «Личный секретарь»

Рабочий MVP модуля AI Smart Planner. Бот принимает русские фразы, создаёт
события в Google Calendar, хранит задачи и отправляет Telegram-напоминания.

## Быстрый запуск

1. Скопируйте `.env.example` в `.env` и заполните переменные.
2. В Google Cloud добавьте значение `REDIRECT_URI` в разрешённые OAuth redirect URI.
3. Создайте ключ шифрования OAuth-токенов и положите его в `TOKEN_ENCRYPTION_KEY`:

   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

4. Запустите:

   ```bash
   docker compose up --build -d
   ```

5. Откройте бота и выполните `/start` для подключения Google Calendar.

Бот получает Telegram-сообщения через polling. Порт `8080` используется только
для Google OAuth callback.

Команды:

- `/start` — подключить Google Calendar или проверить готовность;
- `/planner` — показать примеры Planner;
- `/cancel` — отменить текущий уточняющий диалог;
- `GET /health` — проверить состояние процесса на сервере.

## Примеры

- `Завтра в 15 встреча с Иваном`;
- `Позвонить Петрову в 16:00` — бот уточнит дату;
- `Послезавтра подготовить отчёт` — задача со сроком без выдуманного времени;
- `Напомни через два часа проверить почту`;
- `На следующей неделе встретиться с Иваном` — бот уточнит день и время.

## Проверка

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m compileall -q -x '(^|/)(venv|\.venv)/' .
```

Реальные операции Google и Telegram требуют рабочих токенов. Unit-тесты не
обращаются к внешним API.
