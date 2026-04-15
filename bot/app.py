import logging
import os

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.config import NOTIFICATION_CHECK_INTERVAL
from bot.handlers.afisha import (
    add_event_date,
    add_event_end_date,
    add_event_end_time,
    add_event_link,
    add_event_place,
    add_event_time,
    add_event_title,
    configure_afisha_handlers,
)
from bot.handlers.backlog import add_backlog_description, add_backlog_title, configure_backlog_handlers
from bot.handlers.calendar import (
    add_calendar_event_comment,
    add_calendar_event_date,
    add_calendar_event_end_time,
    add_calendar_event_start_time,
    add_calendar_event_title,
    configure_calendar_handlers,
)
from bot.handlers.common import back_to_main, cancel, configure_common_handlers, noop, quick_return_to_main_menu, start, whoami
from bot.handlers.films import (
    add_film_comment,
    add_film_sasha_rating,
    add_film_title,
    add_film_vova_rating,
    configure_films_handlers,
)
from bot.handlers.leisure import add_leisure_comment, add_leisure_title, configure_leisure_handlers
from bot.handlers.spark import add_spark_description, add_spark_title, configure_spark_handlers, spark_callback_router
from bot.handlers.places import (
    add_city_country,
    add_city_name,
    add_city_place_comment,
    add_city_place_link,
    add_city_place_name,
    add_city_place_visit_comment,
    add_place_comment,
    add_place_link,
    add_place_name,
    configure_places_handlers,
    places_callback_router,
)
from bot.handlers.text_commands import configure_text_commands, quick_text_command_filter, quick_text_command_router
from bot.handlers.wishlist import (
    add_wishlist_comment,
    add_wishlist_link,
    add_wishlist_title,
    configure_wishlist_handlers,
)
from bot.states import (
    ADDING_BACKLOG_DESCRIPTION,
    ADDING_BACKLOG_TITLE,
    ADDING_CALENDAR_EVENT_COMMENT,
    ADDING_CALENDAR_EVENT_DATE,
    ADDING_CALENDAR_EVENT_END_TIME,
    ADDING_CALENDAR_EVENT_START_TIME,
    ADDING_CALENDAR_EVENT_TITLE,
    ADDING_EVENT_DATE,
    ADDING_EVENT_END_DATE,
    ADDING_EVENT_END_TIME,
    ADDING_EVENT_LINK,
    ADDING_EVENT_PLACE,
    ADDING_EVENT_TIME,
    ADDING_EVENT_TITLE,
    ADDING_FILM_COMMENT,
    ADDING_FILM_SASHA_RATING,
    ADDING_FILM_TITLE,
    ADDING_FILM_VOVA_RATING,
    ADDING_LEISURE_COMMENT,
    ADDING_LEISURE_TITLE,
    ADDING_SPARK_DESCRIPTION,
    ADDING_SPARK_TITLE,
    CITY_ADD_COUNTRY,
    CITY_ADD_NAME,
    CITY_PLACE_ADD_COMMENT,
    CITY_PLACE_ADD_LINK,
    CITY_PLACE_ADD_NAME,
    CITY_PLACE_VISIT_COMMENT,
    PLACE_ADD_COMMENT,
    PLACE_ADD_LINK,
    PLACE_ADD_NAME,
    ADDING_WISHLIST_COMMENT,
    ADDING_WISHLIST_LINK,
    ADDING_WISHLIST_TITLE,
    MENU,
    SECTION,
)

from bot.keyboards.common import item_keyboard, main_menu_keyboard
from bot.runtime import (
    check_afisha_notifications,
    menu_router,
    notify_other_user_about_wishlist_item,
    safe_edit_message,
    section_router,
)
from bot.ui.common import build_item_text

logger = logging.getLogger(__name__)
MAIN_MENU_TEXT = "🏠 В меню"


async def handle_application_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled exception while processing Telegram update.", exc_info=context.error)

    if not isinstance(update, Update):
        return

    if update.effective_message is None:
        return

    try:
        await update.effective_message.reply_text("Что-то пошло не так. Попробуй ещё раз.")
    except Exception:
        logger.exception("Failed to send generic error message to user.")


def build_app() -> Application:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Переменная окружения BOT_TOKEN не установлена.")

    app = Application.builder().token(token).build()
    configure_common_handlers(main_menu_keyboard=main_menu_keyboard, safe_edit_message=safe_edit_message)
    configure_backlog_handlers(build_item_text=build_item_text, item_keyboard=item_keyboard)
    configure_films_handlers(
        safe_edit_message=safe_edit_message,
        build_item_text=build_item_text,
        item_keyboard=item_keyboard,
        main_menu_keyboard=main_menu_keyboard,
    )
    configure_leisure_handlers(build_item_text=build_item_text, item_keyboard=item_keyboard)
    configure_spark_handlers(safe_edit_message=safe_edit_message)
    configure_wishlist_handlers(
        build_item_text=build_item_text,
        item_keyboard=item_keyboard,
        notify_other_user_about_wishlist_item=notify_other_user_about_wishlist_item,
    )
    configure_afisha_handlers(build_item_text=build_item_text, item_keyboard=item_keyboard)
    configure_places_handlers(safe_edit_message=safe_edit_message)

    configure_calendar_handlers(
        safe_edit_message=safe_edit_message,
        main_menu_keyboard=main_menu_keyboard,
    )
    configure_text_commands(
        menu_router=menu_router,
        section_router=section_router,
        places_callback_router=places_callback_router,
    )

    if app.job_queue is not None:
        app.job_queue.run_repeating(check_afisha_notifications, interval=NOTIFICATION_CHECK_INTERVAL, first=30, name="afisha_notifications")
    else:
        logger.warning("JobQueue недоступна. Для уведомлений за день до события нужен APScheduler в requirements.")

    quick_commands_filter = quick_text_command_filter()

    def text_state(handler):
        return [
            MessageHandler(quick_commands_filter, quick_text_command_router),
            MessageHandler(filters.Regex(rf"^{MAIN_MENU_TEXT}$"), quick_return_to_main_menu),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handler),
        ]

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MENU: [
                MessageHandler(quick_commands_filter, quick_text_command_router),
                CallbackQueryHandler(back_to_main, pattern=r"^(main|menu:main)$"),
                CallbackQueryHandler(menu_router, pattern=r"^menu\|(films|wishlist|leisure|afisha|backlog)$"),
                CallbackQueryHandler(places_callback_router, pattern=r"^places:"),
                CallbackQueryHandler(spark_callback_router, pattern=r"^spark:"),
                CallbackQueryHandler(section_router),
            ],
            SECTION: [
                MessageHandler(quick_commands_filter, quick_text_command_router),
                CallbackQueryHandler(noop, pattern=r"^noop$"),
                CallbackQueryHandler(back_to_main, pattern=r"^(main|menu:main)$"),
                CallbackQueryHandler(menu_router, pattern=r"^menu\|(films|wishlist|leisure|afisha|backlog)$"),
                CallbackQueryHandler(places_callback_router, pattern=r"^places:"),
                CallbackQueryHandler(spark_callback_router, pattern=r"^spark:"),
                CallbackQueryHandler(section_router),
            ],
            ADDING_FILM_TITLE: text_state(add_film_title),
            ADDING_FILM_COMMENT: text_state(add_film_comment),
            ADDING_FILM_SASHA_RATING: text_state(add_film_sasha_rating),
            ADDING_FILM_VOVA_RATING: text_state(add_film_vova_rating),
            ADDING_CALENDAR_EVENT_TITLE: text_state(add_calendar_event_title),
            ADDING_CALENDAR_EVENT_DATE: text_state(add_calendar_event_date),
            ADDING_CALENDAR_EVENT_START_TIME: text_state(add_calendar_event_start_time),
            ADDING_CALENDAR_EVENT_END_TIME: text_state(add_calendar_event_end_time),
            ADDING_CALENDAR_EVENT_COMMENT: text_state(add_calendar_event_comment),
            ADDING_BACKLOG_TITLE: text_state(add_backlog_title),
            ADDING_BACKLOG_DESCRIPTION: text_state(add_backlog_description),
            ADDING_WISHLIST_TITLE: text_state(add_wishlist_title),
            ADDING_WISHLIST_LINK: text_state(add_wishlist_link),
            ADDING_WISHLIST_COMMENT: text_state(add_wishlist_comment),
            ADDING_LEISURE_TITLE: text_state(add_leisure_title),
            ADDING_LEISURE_COMMENT: text_state(add_leisure_comment),
            ADDING_SPARK_TITLE: text_state(add_spark_title),
            ADDING_SPARK_DESCRIPTION: text_state(add_spark_description),
            ADDING_EVENT_TITLE: text_state(add_event_title),
            ADDING_EVENT_PLACE: text_state(add_event_place),
            ADDING_EVENT_DATE: text_state(add_event_date),
            ADDING_EVENT_TIME: text_state(add_event_time),
            ADDING_EVENT_END_DATE: text_state(add_event_end_date),
            ADDING_EVENT_END_TIME: text_state(add_event_end_time),
            ADDING_EVENT_LINK: text_state(add_event_link),
            PLACE_ADD_NAME: text_state(add_place_name),
            PLACE_ADD_LINK: text_state(add_place_link),
            PLACE_ADD_COMMENT: text_state(add_place_comment),
            CITY_ADD_NAME: text_state(add_city_name),
            CITY_ADD_COUNTRY: text_state(add_city_country),
            CITY_PLACE_ADD_NAME: text_state(add_city_place_name),
            CITY_PLACE_ADD_LINK: text_state(add_city_place_link),
            CITY_PLACE_ADD_COMMENT: text_state(add_city_place_comment),
            CITY_PLACE_VISIT_COMMENT: text_state(add_city_place_visit_comment),
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("whoami", whoami))
    app.add_handler(conv_handler)
    app.add_error_handler(handle_application_error)
    return app
