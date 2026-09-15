# Personal Secretary / AI Smart Planner — снимок проекта

Дата фиксации: 15.09.2026, 11:42 MSK  
Статус: **рабочий online MVP / активная beta**  
Основная ветка продукта: `main`  
Зафиксированный production commit: `542f88ed97ea73a98884f3a472d7c942735e9139` (`Separate free deterministic core from paid AI features`)  
CI этого commit: **success** — Python 3.10 / 3.11 / 3.13 + browser audit  
Production deploy: **success**  
Публичный MVP: `https://213.171.26.210.sslip.io`  
Healthcheck: `https://213.171.26.210.sslip.io/api/health`

---

## 1. Ключевое состояние продукта

Проект развивается как **web-first персональный секретарь**. Telegram остаётся будущим дополнительным интерфейсом поверх того же router и бизнес-логики.

Текущая базовая архитектурная граница:

```text
FREE CORE
  календарь
  напоминания
  повторения
  поиск и свободные окна
  голосовой ввод
  детерминированный парсер
  базовые заметки

PAID AI LAYER
  сложное понимание естественного языка
  AI-диалог
  долговременная AI Memory
  анализ привычек
  персональные рекомендации
  проактивные действия
```

Главный продуктовый принцип на этом этапе:

> Бесплатная версия должна быть полноценным и надёжным планировщиком. AI делает продукт умнее, но не чинит базовые функции календаря и напоминаний.

---

## 2. Что подтверждено рабочим

### Авторизация и web/PWA

- Google OAuth;
- web/PWA интерфейс;
- Caddy + Hypercorn + Flask;
- systemd service `personal-secretary.service`;
- автоматический deploy после зелёного CI;
- health-check после выкладки.

### Calendar

- создание событий естественными командами;
- изменение и удаление событий;
- поиск событий;
- просмотр расписания;
- поиск свободных окон;
- повторяющиеся события;
- категории и пользовательские цвета;
- Google Calendar как внешнее календарное хранилище.

Примеры бесплатных команд:

```text
Встреча завтра в 15:00
Прогулка с собакой каждый день в 19:00
Что у меня в пятницу?
Когда завтра свободное окно?
```

### Reminders

- standalone-напоминания, не зависящие от Google Calendar;
- повторяющиеся напоминания;
- Web Push;
- действия из push;
- хранение состояния в SQLite.

Пример бесплатной команды:

```text
Напомни каждый день в 22:00 принять таблетку
```

### Voice

- запись по удержанию кнопки;
- отпускание запускает обработку;
- голосовой запрос идёт через speech integration в общий router;
- архитектура позволяет использовать голос не только для календаря.

### Notes / Saved

- заметки;
- поиск и открытие;
- контекстная работа с открытой заметкой;
- экран «Сохранённое»;
- soft-delete и история действий.

### Navigation

В кодовой базе присутствуют отдельные модули навигации и хранилище пользовательских настроек (`core/navigation_store.py`, `integrations/navigation_2gis.py`, соответствующие web/API модули). Модуль должен развиваться отдельно от календарного ядра.

---

## 3. AI Core

AI-провайдер текущей beta: **GigaChat-2**.

Основной интеграционный слой:

```text
integrations/ai.py
```

Реализовано:

- OAuth access token;
- кеширование и обновление токена;
- повтор после 401;
- отдельный CA bundle для российских корневых сертификатов;
- structured output;
- безопасная обработка ошибок;
- секреты не хранятся в коде.

Проверка провайдера на production после deploy: **OK**.

---

## 4. Разделение Free / Paid AI

Добавлен отдельный entitlement-слой:

```text
core/feature_access.py
```

Режимы:

```text
AI_ACCESS_MODE=beta
AI_ACCESS_MODE=entitled
AI_ACCESS_MODE=off
```

### `beta`

Текущий режим разработки. AI доступен авторизованным beta-пользователям.

### `entitled`

Будущий коммерческий режим. AI доступен только пользователям с явным entitlement после оплаты/подписки.

### `off`

AI полностью выключен.

Таблица:

```text
feature_entitlements
```

хранит per-user доступ к AI.

Базовые календарные и reminder-функции не зависят от entitlement и GigaChat.

---

## 5. AI Memory v1

Реализована provider-neutral долговременная память.

Основные части:

```text
core/ai_memory_store.py
core/memory_store.py
modules/memory.py
modules/ai_assistant.py
core/ai_prompts.py
```

Память хранит структурированные элементы:

- fact;
- preference;
- habit;
- relationship;
- goal;
- observation.

Есть confidence, источник, evidence, active/suppressed status.

AI получает компактный контекст памяти пользователя, но данные остаются в нашей базе и не зависят от конкретной модели.

Исправлена совместимость с GigaChat: в запросе используется только **одно** `system`-сообщение, первым в `messages`.

---

## 6. Привычки и проактивность

В проекте есть база для проактивного помощника:

```text
core/proactive_store.py
core/assistant_preferences.py
web/proactive.js
```

Принцип:

```text
наблюдение -> память -> уверенность -> предложение/действие
```

Автоматические действия разрешаются только при достаточной уверенности и соответствующем opt-in пользователя.

Отдельно регулируются:

- proactive reminders;
- proactive calendar events.

Без AI entitlement эти функции должны быть недоступны, при этом обычные календарь и напоминания продолжают работать.

Для медицинских привычек AI может напоминать уже известную пользователю рутину, но не должен придумывать препарат, дозировку или менять схему.

---

## 7. Граница детерминированного парсера и AI

Зафиксировано следующее поведение.

### Free Core

```text
Прогулка с собакой каждый день в 19:00
```

Это команда планировщика: создаётся повторяющееся событие без AI.

```text
Напомни каждый день в 22:00 принять таблетку
```

Это команда reminder-модуля: создаётся повторяющееся напоминание без AI.

### AI Layer

```text
Я каждый день в 19:00 гуляю с собакой
```

Это описание привычки, а не явная команда календаря. Такой текст относится к AI-интерпретации/Memory.

```text
Я каждый вечер принимаю таблетку в 22:00
```

Это также привычка, а не прямой reminder command.

Таким образом платный AI увеличивает естественность и проактивность, но не отнимает у free-пользователя базовые возможности.

---

## 8. Данные и приватность AI

Для пользователя без AI-доступа содержимое его заметок, календаря и напоминаний не должно отправляться AI-провайдеру.

В текущей реализации entitlement участвует в AI boundary и privacy lifecycle.

Таблица `feature_entitlements` включена в:

- export account;
- account erasure.

AI-memory journal и entitlement должны удаляться вместе с локальными данными пользователя при подтверждённом удалении аккаунта.

---

## 9. Хранилища

Основное локальное хранилище MVP:

```text
SQLite -> data/bot.db
```

Ключевые группы данных:

- users / Google account;
- reminders;
- notes;
- settings;
- command history / undo;
- navigation preferences;
- AI memory events;
- user memories;
- proactive actions;
- feature entitlements;
- push subscriptions.

Секреты должны оставаться только в `.env` / GitHub Secrets / серверном секретном окружении.

---

## 10. CI/CD и production

Production commit снимка:

```text
542f88ed97ea73a98884f3a472d7c942735e9139
```

Проверено перед/после merge:

- unit tests Python 3.10 — success;
- unit tests Python 3.11 — success;
- unit tests Python 3.13 — success;
- browser feature regression — success;
- production deploy — success;
- health-check — success;
- AI provider check — success.

В ходе deploy автоматически создан verified production backup.

---

## 11. Актуальная архитектура верхнего уровня

```text
User Web/PWA
    |
    v
 Caddy
    |
    v
Hypercorn + Flask
    |
    v
web_app.py
    |
    +--> core/web_transport.py
    |        |
    |        v
    |    modules/router.py
    |        |
    |        +--> Calendar
    |        +--> Reminders
    |        +--> Notes
    |        +--> Navigation
    |
    +--> modules/assistant_api.py
             |
             +--> entitlement check
             +--> modules/ai_assistant.py
             +--> Memory / Proactive Engine
             +--> integrations/ai.py -> GigaChat
```

Детерминированный router остаётся первым уровнем. AI не должен напрямую подменять проверенную бизнес-логику календаря и reminders.

---

## 12. Что пока не считать завершённым

- полноценный биллинг и подписки;
- коммерческая выдача AI entitlement;
- окончательная тарифная сетка;
- лимиты AI на пользователя;
- экономика бесплатного голосового распознавания;
- полностью автономный Proactive Engine;
- Android-упаковка;
- Telegram как полноценный второй интерфейс;
- крупные бизнес-модули и корпоративный AI слой.

Tasks остаются в кодовой базе, но отдельный Task-модуль сейчас не является активным продуктовым приоритетом.

---

## 13. Следующий рекомендуемый этап

Не расширять AI новыми крупными функциями, пока не закреплены два контура тестирования:

```text
FREE REGRESSION SUITE
  календарь / reminders / recurrence / voice / search / free slots
  с AI полностью выключенным

AI REGRESSION SUITE
  entitlement / GigaChat / memory / habits / proactive
```

Это защитит продукт от ситуации, когда развитие платного AI случайно ломает бесплатное ядро.

---

## 14. Точка восстановления

Этот snapshot создан в ветке:

```text
docs/project-snapshot-2026-09-15-latest
```

Базовый production commit:

```text
542f88ed97ea73a98884f3a472d7c942735e9139
```

Для анализа состояния проекта на 15.09.2026 этот commit и данный документ считать основной контрольной точкой.
