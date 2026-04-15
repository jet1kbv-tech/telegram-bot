# Дизайн MVP-фичи «Искра» для Telegram-бота

## Контекст и цель
«Искра» — это подподраздел внутри «Чем займемся» для двух пользователей, чтобы вести общий список совместных активностей.

MVP включает:
- список активных активностей;
- добавление активности (название + описание);
- просмотр карточки активности;
- отметку «выполнено»;
- удаление;
- просмотр выполненных активностей.

Ограничения:
- без переписывания архитектуры;
- по текущим паттернам (handlers / keyboards / storage);
- с переиспользованием пагинации;
- с dependency injection для новых хендлеров;
- без поломки текущих сценариев;
- минимально и production-safe.

---

## 1) Архитектура (модули, handlers, keyboards)

### 1.1. Существующие паттерны, которые сохраняем
- Доменная логика по разделам уже разнесена по `bot/handlers/*`.
- UI-кнопки централизованы в `bot/keyboards/common.py`.
- Рендер текста карточек/списков в `bot/ui/common.py`.
- Универсальная маршрутизация callback-data для CRUD в `section_router`.
- Хендлеры на ввод текста подключаются через `configure_*_handlers(...)` (DI) в `build_app()`.

### 1.2. Новые/изменяемые точки

#### A. Конфиг
- `bot/config.py`:
  - добавить секцию `spark` в `SECTION_CONFIG`:
    - title: `🔥 Искра`;
    - empty_text: `Пока нет совместных активностей.`;
    - statuses: `['active', 'done']`;
    - status_labels: `active -> Активные`, `done -> Завершённые`.

#### B. Состояния диалога
- `bot/states.py`:
  - добавить состояния:
    - `ADDING_SPARK_TITLE`
    - `ADDING_SPARK_DESCRIPTION`

#### C. Хендлеры
- новый модуль `bot/handlers/spark.py` с тем же стилем, как `leisure.py`/`backlog.py`:
  - `configure_spark_handlers(build_item_text, item_keyboard)` — DI для переиспользования общих UI-функций.
  - `add_spark_title(update, context)`:
    - валидация непустого title;
    - сохранение в `context.user_data['spark_title']`;
    - переход в `ADDING_SPARK_DESCRIPTION`.
  - `add_spark_description(update, context)`:
    - `-` => пустое описание;
    - запись в storage новой сущности spark с `status='active'`;
    - ответ карточкой через injected `build_item_text` + `item_keyboard`;
    - очистка временных `context.user_data`;
    - возврат в `SECTION`.

#### D. Клавиатуры
- `bot/keyboards/common.py`:
  - `activity_menu_keyboard()` — добавить кнопку `🔥 Искра` с callback `menu|spark`.
  - `section_menu_keyboard('spark')`:
    - `➕ Добавить активность` → `add|spark`
    - `📋 Активные` → `list|spark|active|0`
    - `✅ Завершённые` → `list|spark|done|0`
    - + стандартная кнопка `🏠 В меню`
  - без новых отдельных генераторов: использовать существующие `list_keyboard`, `item_keyboard`, `delete_confirm_keyboard` с минимальным расширением на spark-ветку.

#### E. Runtime-роутинг
- `bot/runtime.py` в `section_router`:
  - `add|spark` → запуск состояния `ADDING_SPARK_TITLE`.
  - `list|spark|<status>|<page>` как у `films/backlog` (фильтрация по статусу).
  - `view|spark|<id>|<status>|<page>` по паттерну статус-фильтра.
  - `status|spark|<id>|<new_status>|<status>|<page>`.
  - `delete_confirm|spark|...` и `delete|spark|...` по тому же формату.
- `show_list(...)` / `show_item(...)`:
  - добавить обработку spark как секции со status filter (`active/done`) аналогично `films/backlog`.
- Никаких новых глобальных роутеров/ConversationHandler не требуется; только расширение текущих веток.

#### F. Сборка приложения
- `bot/app.py` и/или используемая точка сборки:
  - импорт `configure_spark_handlers`, `add_spark_title`, `add_spark_description`;
  - вызов `configure_spark_handlers(...)` рядом с `configure_leisure_handlers(...)`;
  - добавить состояния `ADDING_SPARK_TITLE` и `ADDING_SPARK_DESCRIPTION` в `ConversationHandler.states` через `text_state(...)`.

#### G. Рендер UI-текста
- `bot/ui/common.py`:
  - `build_item_text('spark', item)`:
    - заголовок: `🔥 {title}`;
    - статус;
    - описание (если есть).
  - `build_list_text(...)` — поддержать ветку `spark` по аналогии с `films/backlog` (отображение текущего статуса в title, если передан `status_filter`).

---

## 2) Namespace для callback_data

Чтобы сохранить совместимость с текущим парсером в `section_router`, используем **тот же DSL** и только добавляем новую секцию `spark`:

- Меню раздела:
  - `menu|spark`
- Добавление:
  - `add|spark`
- Списки:
  - `list|spark|active|0`
  - `list|spark|done|0`
- Просмотр карточки:
  - `view|spark|<item_id>|active|<page>`
  - `view|spark|<item_id>|done|<page>`
- Статус:
  - `status|spark|<item_id>|done|active|<page>`
  - `status|spark|<item_id>|active|done|<page>`
- Удаление:
  - `delete_confirm|spark|<item_id>|<status_filter>|<page>`
  - `delete|spark|<item_id>|<status_filter>|<page>`

Почему так:
- не ломает текущий split-парсинг (`parts = query.data.split('|')`);
- переиспользует существующие helper’ы клавиатур;
- минимизирует риск регрессий.

---

## 3) Структура хранения (storage)

### 3.1. Корневой ключ
В `JsonStorage.default_data()` добавить:
- `"spark": []`

### 3.2. Формат элемента
```json
{
  "id": "abcd1234",
  "title": "Пробежка в парке",
  "description": "30 минут в субботу утром",
  "status": "active"
}
```

### 3.3. Нормализация
В `storage.py` добавить:
- `SPARK_STATUSES = ['active', 'done']` (через `config.py`);
- `normalize_spark(item)`:
  - поддержка старых/плохих данных production-safe;
  - fallback-поля (`title`, `description`);
  - статус по умолчанию `active`, если мусор.
- в `_normalize_data(...)` обработка `raw_data.get('spark', [])`.

### 3.4. Обратная совместимость
- Если у существующих пользователей нет `spark` в `data.json`, `default_data` + normalize добавят пустой список без миграций.
- Сохранение атомарное уже реализовано (`NamedTemporaryFile + os.replace`) — менять не нужно.

---

## 4) Интеграция в «Чем займемся»

### 4.1. Точка входа
- В `activity_menu_keyboard()` добавить кнопку `🔥 Искра`.
- По нажатию идет `menu|spark` (уже покрывается существующей логикой activity → section_router → menu).

### 4.2. UX-поток MVP
1. Пользователь открывает `🎲 Чем займемся`.
2. Нажимает `🔥 Искра`.
3. Видит меню раздела:
   - добавить;
   - активные;
   - завершенные.
4. Добавляет активность: title → description.
5. В карточке может:
   - отметить завершенной / вернуть в активные;
   - удалить;
   - вернуться к списку.

### 4.3. Пагинация
- Использовать уже существующие `paginate_items`, `list_keyboard`, `build_pagination_row`.
- Для spark передавать `status_filter`, чтобы кнопка «Назад к разделам» и «К списку» сохраняли контекст фильтра.

---

## 5) Нужные состояния conversation

Минимально достаточно 2 новых состояния:
1. `ADDING_SPARK_TITLE` — ввод названия активности.
2. `ADDING_SPARK_DESCRIPTION` — ввод описания (`-` = пусто).

Почему только 2:
- MVP требует 2 поля;
- остальные действия (list/view/status/delete) уже callback-driven и не требуют текстовых состояний.

---

## 6) Риски и edge cases

### 6.1. Парсинг callback_data
**Риск:** конфликт форматов для секций со статус-фильтром.
**Снижение:** обрабатывать spark в тех же ветках, где сейчас `films/backlog`, и унифицировать условие (`section in {'films','backlog','spark'}`).

### 6.2. Потеря контекста страницы/фильтра
**Риск:** после status/delete пользователь попадает «не туда».
**Снижение:** всегда прокидывать `status_filter` и `page` в callback и использовать существующие `build_*_callback` helper’ы.

### 6.3. Длинный callback_data
**Риск:** Telegram лимит на callback_data (64 bytes).
**Снижение:** не добавлять лишние токены; использовать короткие status (`active/done`) и короткий `id` (уже 8 символов).

### 6.4. Повреждённые/старые данные
**Риск:** кривой JSON или отсутствующий ключ `spark`.
**Снижение:** расширить normalize/default-data (уже принятый в проекте подход).

### 6.5. Конкурентное добавление/обновление
**Риск:** гонки записи.
**Снижение:** использовать существующий `JsonStorage` с `RLock` и атомарным `save` без изменений.

### 6.6. Регрессии в существующих flow
**Риск:** случайно поломать leisure/films/backlog парсинг.
**Снижение:** минимальные точечные расширения условий + smoke-проверки ключевых callback-веток.

### 6.7. Доступ только для пары пользователей
**Риск:** сторонний пользователь попадает в раздел.
**Снижение:** ничего нового не добавлять — `ensure_access` уже проверяется в menu/section/text handlers.

---

## Минимальный план внедрения (без рефакторинга)
1. Добавить `spark` в config/status labels.
2. Добавить storage default + normalize_spark.
3. Добавить handler `spark.py` с DI по паттерну `leisure.py`.
4. Расширить `states.py` и `ConversationHandler`.
5. Добавить кнопку в activity-menu и ветки section menu/list/view/status/delete.
6. Добавить рендер текста в `ui/common.py`.
7. Прогнать smoke-проверки сценариев:
   - add/list/view/status/delete для spark;
   - regression check для films/leisure/backlog/wishlist.

Итог: MVP «Искра» внедряется как новая секция в существующую универсальную схему, с минимальными изменениями и без архитектурного сдвига.
