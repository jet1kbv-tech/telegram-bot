import logging
import os

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
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
from bot.handlers.common import back_to_main, cancel, configure_common_handlers, noop, start, whoami
from bot.handlers.films import (
    add_film_comment,
    add_film_sasha_rating,
    add_film_title,
    add_film_vova_rating,
    configure_films_handlers,
)
from bot.handlers.leisure import add_leisure_comment, add_leisure_title, configure_leisure_handlers
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
    ADDING_WISHLIST_COMMENT,
    ADDING_WISHLIST_LINK,
    ADDING_WISHLIST_TITLE,
    MENU,
    SECTION,
)

from bot.runtime import (
    build_item_text,
    check_afisha_notifications,
    item_keyboard,
    main_menu_keyboard,
    menu_router,
    notify_other_user_about_wishlist_item,
    safe_edit_message,
    section_router,
)

logger = logging.getLogger(__name__)


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
    configure_wishlist_handlers(
        build_item_text=build_item_text,
        item_keyboard=item_keyboard,
        notify_other_user_about_wishlist_item=notify_other_user_about_wishlist_item,
    )

    configure_calendar_handlers(
        safe_edit_message=safe_edit_message,
        main_menu_keyboard=main_menu_keyboard,
    )

    if app.job_queue is not None:
        app.job_queue.run_repeating(check_afisha_notifications, interval=NOTIFICATION_CHECK_INTERVAL, first=30, name="afisha_notifications")
    else:
        logger.warning("JobQueue недоступна. Для уведомлений за день до события нужен APScheduler в requirements.")

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MENU: [
                CallbackQueryHandler(back_to_main, pattern=r"^main$"),
                CallbackQueryHandler(menu_router, pattern=r"^menu\|(films|wishlist|leisure|afisha|backlog)$"),
                CallbackQueryHandler(section_router),
            ],
            SECTION: [
                CallbackQueryHandler(noop, pattern=r"^noop$"),
                CallbackQueryHandler(back_to_main, pattern=r"^main$"),
                CallbackQueryHandler(menu_router, pattern=r"^menu\|(films|wishlist|leisure|afisha|backlog)$"),
                CallbackQueryHandler(section_router),
            ],
            ADDING_FILM_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_film_title)],
            ADDING_FILM_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_film_comment)],
            ADDING_FILM_SASHA_RATING: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_film_sasha_rating)],
            ADDING_FILM_VOVA_RATING: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_film_vova_rating)],
            ADDING_CALENDAR_EVENT_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_calendar_event_title)],
            ADDING_CALENDAR_EVENT_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_calendar_event_date)],
            ADDING_CALENDAR_EVENT_START_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_calendar_event_start_time)],
            ADDING_CALENDAR_EVENT_END_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_calendar_event_end_time)],
            ADDING_CALENDAR_EVENT_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_calendar_event_comment)],
            ADDING_BACKLOG_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_backlog_title)],
            ADDING_BACKLOG_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_backlog_description)],
            ADDING_WISHLIST_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_wishlist_title)],
            ADDING_WISHLIST_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_wishlist_link)],
            ADDING_WISHLIST_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_wishlist_comment)],
            ADDING_LEISURE_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_leisure_title)],
            ADDING_LEISURE_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_leisure_comment)],
            ADDING_EVENT_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_title)],
            ADDING_EVENT_PLACE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_place)],
            ADDING_EVENT_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_date)],
            ADDING_EVENT_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_time)],
            ADDING_EVENT_END_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_end_date)],
            ADDING_EVENT_END_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_end_time)],
            ADDING_EVENT_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_event_link)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("whoami", whoami))
    app.add_handler(conv_handler)
    return app
