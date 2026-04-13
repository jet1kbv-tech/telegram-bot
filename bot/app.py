import os

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.handlers.places import (
    add_city_country,
    add_city_place_comment,
    add_city_place_link,
    add_city_place_title,
    add_city_place_visited_comment,
    add_city_title,
    add_moscow_place_comment,
    add_moscow_place_link,
    add_moscow_place_title,
    places_callback_router,
    start,
)
from bot.states import (
    ADDING_CITY_COUNTRY,
    ADDING_CITY_PLACE_COMMENT,
    ADDING_CITY_PLACE_LINK,
    ADDING_CITY_PLACE_TITLE,
    ADDING_CITY_PLACE_VISITED_COMMENT,
    ADDING_CITY_TITLE,
    ADDING_MOSCOW_PLACE_COMMENT,
    ADDING_MOSCOW_PLACE_LINK,
    ADDING_MOSCOW_PLACE_TITLE,
    MENU,
    SECTION,
)


def build_app() -> Application:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Переменная окружения BOT_TOKEN не установлена")

    app = Application.builder().token(token).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MENU: [
                CallbackQueryHandler(places_callback_router),
            ],
            SECTION: [
                CallbackQueryHandler(places_callback_router),
            ],
            ADDING_MOSCOW_PLACE_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_moscow_place_title)],
            ADDING_MOSCOW_PLACE_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_moscow_place_link)],
            ADDING_MOSCOW_PLACE_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_moscow_place_comment)],
            ADDING_CITY_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_city_title)],
            ADDING_CITY_COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_city_country)],
            ADDING_CITY_PLACE_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_city_place_title)],
            ADDING_CITY_PLACE_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_city_place_link)],
            ADDING_CITY_PLACE_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_city_place_comment)],
            ADDING_CITY_PLACE_VISITED_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_city_place_visited_comment)],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    return app
