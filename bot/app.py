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

from bot.config import (AI_ATTACHMENT_MAX_BYTES, AI_ATTACHMENT_TIMEOUT_SECONDS,
                        AI_INTENT_TIMEOUT_SECONDS, NOTIFICATION_CHECK_INTERVAL, TMDB_API_TOKEN,
                        WEATHER_CACHE_TTL_SECONDS, WEATHER_TIMEOUT_SECONDS)
from bot.handlers.nl_assistant import configure_nl_assistant, nl_callback_router, nl_clarification_handler, nl_query_callback_router, nl_text_handler
from bot.services.polza_intent_parser import PolzaIntentParser
from bot.services.ticket_enrichment import PolzaTicketEnricher
from bot.handlers.afisha import (
    add_event_date,
    add_event_end_date,
    add_event_end_time,
    add_event_link,
    add_event_place,
    add_event_time,
    add_event_title,
    configure_afisha_handlers,
    edit_afisha_date,
    edit_afisha_time,
)
from bot.handlers.backlog import add_backlog_description, add_backlog_title, configure_backlog_handlers
from bot.handlers.calendar import (
    add_calendar_event_comment,
    add_calendar_event_date,
    add_calendar_event_end_time,
    add_calendar_event_start_time,
    add_calendar_event_title,
    configure_calendar_handlers,
    edit_calendar_date,
    edit_calendar_time,
)
from bot.handlers.common import back_to_main, cancel, configure_common_handlers, noop, quick_return_to_main_menu, start, whoami
from bot.handlers.films import (
    add_film_comment,
    add_film_title,
    configure_films_handlers,
    film_metadata_callback_router,
)
from bot.handlers.film_enrichment import (
    configure_film_enrichment_handlers,
    film_enrichment_callback_router,
    film_enrichment_manual_query,
)
from bot.handlers.film_filters import configure_film_filter_handlers, film_filter_callback_router
from bot.handlers.film_recommendations import configure_film_recommendations, film_recommendation_callback_router
from bot.handlers.leisure import add_leisure_comment, add_leisure_title, configure_leisure_handlers
from bot.handlers.purchases import (
    add_purchase_comment,
    add_purchase_link,
    add_purchase_price,
    add_purchase_title,
    edit_purchase_field,
    configure_purchases_handlers,
    purchases_callback_router,
)
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
from bot.handlers.tickets import (
    add_ticket_attachment,
    add_ticket_comment,
    add_ticket_date,
    add_ticket_place_route,
    add_ticket_time,
    add_ticket_title,
    configure_tickets_handlers,
    tickets_callback_router,
)
from bot.handlers.event_attachments import (configure_event_attachment_handlers, discard_ticket_enrichment, event_attachment_router,
                                            receive_attachment_metadata, receive_file)
from bot.handlers.nl_event_attachments import (attachment_event_title_handler, collect_attachment_handler, nl_attachment_callback_router,
                                                orphan_attachment_handler)
from bot.handlers.nl_attachment_retrieval import attachment_query_callback_router
from bot.handlers.nl_attachment_mutations import attachment_mutation_callback_router
from bot.handlers.wishlist import (
    add_wishlist_comment,
    add_wishlist_link,
    add_wishlist_title,
    configure_wishlist_handlers,
)
from bot.states import (
    ADDING_BACKLOG_DESCRIPTION,
    ADDING_BACKLOG_TITLE,
    ADDING_PURCHASE_COMMENT,
    ADDING_PURCHASE_LINK,
    ADDING_PURCHASE_PRICE,
    ADDING_PURCHASE_PRIORITY,
    ADDING_PURCHASE_TITLE,
    ADDING_CALENDAR_EVENT_COMMENT,
    ADDING_CALENDAR_EVENT_DATE,
    ADDING_CALENDAR_EVENT_END_TIME,
    ADDING_CALENDAR_EVENT_START_TIME,
    ADDING_CALENDAR_EVENT_TITLE,
    EDITING_CALENDAR_DATE,
    EDITING_CALENDAR_TIME,
    ADDING_EVENT_DATE,
    ADDING_EVENT_END_DATE,
    ADDING_EVENT_END_TIME,
    ADDING_EVENT_LINK,
    ADDING_EVENT_PLACE,
    ADDING_EVENT_TIME,
    ADDING_EVENT_TITLE,
    EDITING_AFISHA_DATE,
    EDITING_AFISHA_TIME,
    ADDING_FILM_COMMENT,
    ADDING_FILM_TITLE,
    ADDING_LEISURE_COMMENT,
    ADDING_LEISURE_TITLE,
    ADDING_SPARK_DESCRIPTION,
    ADDING_SPARK_TITLE,
    ADDING_TICKET_ATTACHMENTS,
    ADDING_TICKET_COMMENT,
    ADDING_TICKET_DATE,
    ADDING_TICKET_PLACE_ROUTE,
    ADDING_TICKET_TIME,
    ADDING_TICKET_TITLE,
    EDITING_PURCHASE_FIELD,
    ADDING_EVENT_ATTACHMENT_FILE,
    SELECTING_EVENT_ATTACHMENT_TYPE,
    SELECTING_EVENT_ATTACHMENT_TRANSPORT,
    ENRICHING_EVENT_ATTACHMENT,
    EDITING_EVENT_ATTACHMENT_METADATA,
    CONFIRMING_TICKET_ENRICHMENT,
    SELECTING_NL_ATTACHMENT_QUERY,
    WAITING_FOR_NL_ATTACHMENTS,
    SELECTING_NL_ATTACHMENT_EVENT,
    CONFIRMING_NL_ATTACHMENT,
    ENTERING_NL_ATTACHMENT_EVENT_TITLE,
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
    AI_CLARIFYING,
    MENU,
    SECTION,
    SELECTING_FILM_METADATA,
    CONFIRMING_FILM_ADD,
    FILM_ENRICHMENT_CONFIRMING,
    FILM_ENRICHMENT_MANUAL_QUERY,
    FILM_ENRICHMENT_REVIEW,
    FILM_ENRICHMENT_SELECTING,
)

from bot.keyboards.common import item_keyboard, main_menu_keyboard
from bot.runtime import (
    check_afisha_notifications,
    configure_notification_enrichment,
    menu_router,
    notify_other_user_about_calendar_item,
    notify_other_user_about_wishlist_item,
    safe_edit_message,
    section_router,
)
from bot.ui.common import build_item_text
from bot.services.tmdb_movie_metadata import TmdbMovieMetadataProvider
from bot.services.tmdb_candidate_provider import TmdbCandidateProvider
from bot.services.movie_recommendation_service import MovieRecommendationService
from bot.services.weather import OpenMeteoWeatherProvider

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
    tmdb_token = os.getenv("TMDB_API_READ_ACCESS_TOKEN", "").strip()
    metadata_provider = TmdbMovieMetadataProvider(tmdb_token) if tmdb_token else None
    configure_films_handlers(
        safe_edit_message=safe_edit_message,
        build_item_text=build_item_text,
        item_keyboard=item_keyboard,
        main_menu_keyboard=main_menu_keyboard,
        metadata_provider=metadata_provider,
    )
    configure_film_enrichment_handlers(safe_edit_message=safe_edit_message, metadata_provider=metadata_provider)
    configure_film_filter_handlers(safe_edit_message=safe_edit_message, build_item_text=build_item_text)
    recommendation_provider = TmdbCandidateProvider(TMDB_API_TOKEN) if TMDB_API_TOKEN else None
    configure_film_recommendations(service=MovieRecommendationService(recommendation_provider),
        safe_edit_message=safe_edit_message, build_item_text=build_item_text, item_keyboard=item_keyboard)
    configure_leisure_handlers(build_item_text=build_item_text, item_keyboard=item_keyboard)
    configure_spark_handlers(safe_edit_message=safe_edit_message)
    configure_wishlist_handlers(
        build_item_text=build_item_text,
        item_keyboard=item_keyboard,
        notify_other_user_about_wishlist_item=notify_other_user_about_wishlist_item,
    )
    configure_afisha_handlers(build_item_text=build_item_text, item_keyboard=item_keyboard)
    configure_places_handlers(safe_edit_message=safe_edit_message)
    configure_purchases_handlers(safe_edit_message=safe_edit_message)

    configure_calendar_handlers(
        safe_edit_message=safe_edit_message,
        main_menu_keyboard=main_menu_keyboard,
        notify_other_user_about_calendar_item=notify_other_user_about_calendar_item,
    )
    configure_text_commands(
        menu_router=menu_router,
        section_router=section_router,
        places_callback_router=places_callback_router,
    )
    configure_tickets_handlers(safe_edit_message=safe_edit_message)
    polza_key = os.getenv("POLZA_AI_API_KEY", "").strip()
    polza_model = os.getenv("POLZA_AI_MODEL", "").strip()
    attachment_model = os.getenv("POLZA_ATTACHMENT_MODEL", "").strip()
    ticket_enricher = (PolzaTicketEnricher(api_key=polza_key, model=attachment_model,
        timeout_seconds=AI_ATTACHMENT_TIMEOUT_SECONDS) if polza_key and attachment_model else None)
    configure_event_attachment_handlers(safe_edit_message=safe_edit_message, ticket_enricher=ticket_enricher,
                                        attachment_max_bytes=AI_ATTACHMENT_MAX_BYTES)
    if ticket_enricher is None:
        logger.info("Ticket enrichment disabled: POLZA_AI_API_KEY or POLZA_ATTACHMENT_MODEL is missing")
    else:
        logger.info("Ticket enrichment enabled with configured attachment model")
    nl_enabled = bool(polza_key and polza_model)
    weather_provider = OpenMeteoWeatherProvider(timeout_seconds=WEATHER_TIMEOUT_SECONDS,
                                                cache_ttl_seconds=WEATHER_CACHE_TTL_SECONDS)
    configure_notification_enrichment(weather_provider)
    if nl_enabled:
        configure_nl_assistant(
            parser=PolzaIntentParser(api_key=polza_key, model=polza_model, timeout_seconds=AI_INTENT_TIMEOUT_SECONDS),
            notify_calendar=notify_other_user_about_calendar_item,
            weather_provider=weather_provider,
        )
        logger.info("AI/NL assistant enabled with configured Polza model")
    else:
        logger.info("AI/NL assistant disabled: POLZA_AI_API_KEY or POLZA_AI_MODEL is missing")

    if app.job_queue is not None:
        app.job_queue.run_repeating(check_afisha_notifications, interval=NOTIFICATION_CHECK_INTERVAL, first=30, name="afisha_notifications")
    else:
        logger.warning("JobQueue недоступна. Для уведомлений за день до события нужен APScheduler в requirements.")

    quick_commands_filter = quick_text_command_filter()
    attachment_callback_handlers = [CallbackQueryHandler(nl_attachment_callback_router, pattern=r"^nla:"),
                                    CallbackQueryHandler(attachment_query_callback_router, pattern=r"^nlar:"),
                                    CallbackQueryHandler(attachment_mutation_callback_router, pattern=r"^nlam:")]
    ai_callback_handlers = ([
        CallbackQueryHandler(nl_query_callback_router, pattern=r"^aiq:[A-Za-z0-9_-]{8,16}:(?:r|p:\d+)$"),
        CallbackQueryHandler(nl_callback_router, pattern=r"^ai:(?:[cex]:[A-Za-z0-9_-]{8,16}|r:[A-Za-z0-9_-]{8,16}:\d+)$"),
    ] if nl_enabled else [])
    ai_text_handlers = ([
        MessageHandler((filters.PHOTO | filters.Document.ALL) & filters.CaptionRegex(r"(?i)^\s*прикреп"), nl_text_handler),
        MessageHandler(filters.PHOTO | filters.Document.ALL, orphan_attachment_handler),
        MessageHandler(filters.TEXT & ~filters.COMMAND, nl_text_handler),
    ] if nl_enabled else [MessageHandler(filters.PHOTO | filters.Document.ALL, orphan_attachment_handler)])

    def text_state(handler):
        return [
            MessageHandler(quick_commands_filter, quick_text_command_router),
            MessageHandler(filters.Regex(rf"^{MAIN_MENU_TEXT}$"), quick_return_to_main_menu),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handler),
        ]

    async def ticket_enrichment_home(update, context):
        discard_ticket_enrichment(context)
        return await back_to_main(update, context)

    async def ticket_enrichment_quick_home(update, context):
        discard_ticket_enrichment(context)
        return await quick_return_to_main_menu(update, context)

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MENU: [
                MessageHandler(quick_commands_filter, quick_text_command_router),
                *attachment_callback_handlers,
                *ai_callback_handlers,
                CallbackQueryHandler(film_recommendation_callback_router, pattern=r"^filmrec:"),
                CallbackQueryHandler(back_to_main, pattern=r"^(main|menu:main)$"),
                CallbackQueryHandler(menu_router, pattern=r"^menu\|(films|wishlist|leisure|afisha|backlog)$"),
                CallbackQueryHandler(places_callback_router, pattern=r"^places:"),
                CallbackQueryHandler(spark_callback_router, pattern=r"^spark:"),
                CallbackQueryHandler(tickets_callback_router, pattern=r"^tickets:"),
                CallbackQueryHandler(purchases_callback_router, pattern=r"^purchases:"),
                CallbackQueryHandler(section_router),
                *ai_text_handlers,
            ],
            SECTION: [
                MessageHandler(quick_commands_filter, quick_text_command_router),
                *attachment_callback_handlers,
                *ai_callback_handlers,
                CallbackQueryHandler(film_recommendation_callback_router, pattern=r"^filmrec:"),
                CallbackQueryHandler(noop, pattern=r"^noop$"),
                CallbackQueryHandler(film_metadata_callback_router, pattern=r"^filmmeta:"),
                CallbackQueryHandler(film_enrichment_callback_router, pattern=r"^filmenrich:"),
                CallbackQueryHandler(film_filter_callback_router, pattern=r"^filmfilter:"),
                CallbackQueryHandler(back_to_main, pattern=r"^(main|menu:main)$"),
                CallbackQueryHandler(menu_router, pattern=r"^menu\|(films|wishlist|leisure|afisha|backlog)$"),
                CallbackQueryHandler(places_callback_router, pattern=r"^places:"),
                CallbackQueryHandler(spark_callback_router, pattern=r"^spark:"),
                CallbackQueryHandler(tickets_callback_router, pattern=r"^tickets:"),
                CallbackQueryHandler(purchases_callback_router, pattern=r"^purchases:"),
                CallbackQueryHandler(event_attachment_router, pattern=r"^att\|"),
                CallbackQueryHandler(section_router),
                *ai_text_handlers,
            ],
            AI_CLARIFYING: [
                MessageHandler(quick_commands_filter, quick_text_command_router),
                MessageHandler(filters.Regex(rf"^{MAIN_MENU_TEXT}$"), quick_return_to_main_menu),
                *ai_callback_handlers,
                MessageHandler(filters.TEXT & ~filters.COMMAND, nl_clarification_handler),
            ],
            ADDING_FILM_TITLE: text_state(add_film_title),
            ADDING_FILM_COMMENT: text_state(add_film_comment),
            SELECTING_FILM_METADATA: [
                CallbackQueryHandler(back_to_main, pattern=r"^(main|menu:main)$"),
                CallbackQueryHandler(film_metadata_callback_router, pattern=r"^filmmeta:"),
            ],
            CONFIRMING_FILM_ADD: [
                CallbackQueryHandler(back_to_main, pattern=r"^(main|menu:main)$"),
                CallbackQueryHandler(menu_router, pattern=r"^menu\|films$"),
                CallbackQueryHandler(film_metadata_callback_router, pattern=r"^filmmeta:"),
            ],
            FILM_ENRICHMENT_REVIEW: [
                CallbackQueryHandler(back_to_main, pattern=r"^(main|menu:main)$"),
                CallbackQueryHandler(menu_router, pattern=r"^menu\|films$"),
                CallbackQueryHandler(film_enrichment_callback_router, pattern=r"^filmenrich:"),
            ],
            FILM_ENRICHMENT_MANUAL_QUERY: text_state(film_enrichment_manual_query),
            FILM_ENRICHMENT_SELECTING: [
                CallbackQueryHandler(back_to_main, pattern=r"^(main|menu:main)$"),
                CallbackQueryHandler(menu_router, pattern=r"^menu\|films$"),
                CallbackQueryHandler(film_enrichment_callback_router, pattern=r"^filmenrich:"),
            ],
            FILM_ENRICHMENT_CONFIRMING: [
                CallbackQueryHandler(back_to_main, pattern=r"^(main|menu:main)$"),
                CallbackQueryHandler(menu_router, pattern=r"^menu\|films$"),
                CallbackQueryHandler(film_enrichment_callback_router, pattern=r"^filmenrich:"),
            ],
            ADDING_CALENDAR_EVENT_TITLE: text_state(add_calendar_event_title),
            ADDING_CALENDAR_EVENT_DATE: text_state(add_calendar_event_date),
            ADDING_CALENDAR_EVENT_START_TIME: text_state(add_calendar_event_start_time),
            ADDING_CALENDAR_EVENT_END_TIME: text_state(add_calendar_event_end_time),
            ADDING_CALENDAR_EVENT_COMMENT: text_state(add_calendar_event_comment),
            EDITING_CALENDAR_DATE: text_state(edit_calendar_date),
            EDITING_CALENDAR_TIME: text_state(edit_calendar_time),
            ADDING_BACKLOG_TITLE: text_state(add_backlog_title),
            ADDING_BACKLOG_DESCRIPTION: text_state(add_backlog_description),
            ADDING_WISHLIST_TITLE: text_state(add_wishlist_title),
            ADDING_WISHLIST_LINK: text_state(add_wishlist_link),
            ADDING_WISHLIST_COMMENT: text_state(add_wishlist_comment),
            ADDING_LEISURE_TITLE: text_state(add_leisure_title),
            ADDING_LEISURE_COMMENT: text_state(add_leisure_comment),
            ADDING_PURCHASE_TITLE: text_state(add_purchase_title),
            ADDING_PURCHASE_LINK: text_state(add_purchase_link),
            ADDING_PURCHASE_PRICE: text_state(add_purchase_price),
            ADDING_PURCHASE_PRIORITY: [
                MessageHandler(quick_commands_filter, quick_text_command_router),
                MessageHandler(filters.Regex(rf"^{MAIN_MENU_TEXT}$"), quick_return_to_main_menu),
                CallbackQueryHandler(back_to_main, pattern=r"^(main|menu:main)$"),
                CallbackQueryHandler(purchases_callback_router, pattern=r"^purchases:add_priority:"),
            ],
            ADDING_PURCHASE_COMMENT: text_state(add_purchase_comment),
            EDITING_PURCHASE_FIELD: text_state(edit_purchase_field),
            ADDING_SPARK_TITLE: text_state(add_spark_title),
            ADDING_SPARK_DESCRIPTION: text_state(add_spark_description),
            ADDING_TICKET_TITLE: text_state(add_ticket_title),
            ADDING_TICKET_DATE: text_state(add_ticket_date),
            ADDING_TICKET_TIME: text_state(add_ticket_time),
            ADDING_TICKET_PLACE_ROUTE: text_state(add_ticket_place_route),
            ADDING_TICKET_COMMENT: text_state(add_ticket_comment),
            ADDING_TICKET_ATTACHMENTS: [
                CallbackQueryHandler(tickets_callback_router, pattern=r"^tickets:"),
                MessageHandler(quick_commands_filter, quick_text_command_router),
                MessageHandler(filters.Regex(rf"^{MAIN_MENU_TEXT}$"), quick_return_to_main_menu),
                MessageHandler(filters.PHOTO | filters.Document.ALL, add_ticket_attachment),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_ticket_attachment),
            ],
            ADDING_EVENT_ATTACHMENT_FILE: [
                CallbackQueryHandler(event_attachment_router, pattern=r"^att\|"),
                MessageHandler(filters.PHOTO | filters.Document.ALL, receive_file),
                MessageHandler(filters.ALL, receive_file),
            ],
            SELECTING_EVENT_ATTACHMENT_TYPE: [CallbackQueryHandler(event_attachment_router, pattern=r"^att\|")],
            SELECTING_EVENT_ATTACHMENT_TRANSPORT: [CallbackQueryHandler(event_attachment_router, pattern=r"^att\|")],
            ENRICHING_EVENT_ATTACHMENT: [
                MessageHandler(filters.Regex(rf"^{MAIN_MENU_TEXT}$"), quick_return_to_main_menu),
                CallbackQueryHandler(back_to_main, pattern=r"^(main|menu:main)$"),
                CallbackQueryHandler(event_attachment_router, pattern=r"^att\|"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_attachment_metadata),
            ],
            EDITING_EVENT_ATTACHMENT_METADATA: [
                CallbackQueryHandler(nl_attachment_callback_router, pattern=r"^nla:"),
                MessageHandler(filters.Regex(rf"^{MAIN_MENU_TEXT}$"), quick_return_to_main_menu),
                CallbackQueryHandler(back_to_main, pattern=r"^(main|menu:main)$"),
                CallbackQueryHandler(event_attachment_router, pattern=r"^att\|"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_attachment_metadata),
            ],
            CONFIRMING_TICKET_ENRICHMENT: [
                CallbackQueryHandler(nl_attachment_callback_router, pattern=r"^nla:"),
                MessageHandler(filters.Regex(rf"^{MAIN_MENU_TEXT}$"), ticket_enrichment_quick_home),
                CallbackQueryHandler(ticket_enrichment_home, pattern=r"^(main|menu:main)$"),
                CallbackQueryHandler(event_attachment_router, pattern=r"^att\|"),
            ],
            WAITING_FOR_NL_ATTACHMENTS: [
                MessageHandler(filters.Regex(rf"^{MAIN_MENU_TEXT}$"), quick_return_to_main_menu),
                CallbackQueryHandler(back_to_main, pattern=r"^(main|menu:main)$"),
                CallbackQueryHandler(nl_attachment_callback_router, pattern=r"^nla:"),
                MessageHandler(filters.PHOTO | filters.Document.ALL, collect_attachment_handler),
                MessageHandler(filters.ALL, collect_attachment_handler),
            ],
            SELECTING_NL_ATTACHMENT_EVENT: [
                CallbackQueryHandler(back_to_main, pattern=r"^(main|menu:main)$"),
                CallbackQueryHandler(nl_attachment_callback_router, pattern=r"^nla:"),
            ],
            CONFIRMING_NL_ATTACHMENT: [
                CallbackQueryHandler(back_to_main, pattern=r"^(main|menu:main)$"),
                CallbackQueryHandler(nl_attachment_callback_router, pattern=r"^nla:"),
            ],
            ENTERING_NL_ATTACHMENT_EVENT_TITLE: [
                MessageHandler(filters.Regex(rf"^{MAIN_MENU_TEXT}$"), quick_return_to_main_menu),
                CallbackQueryHandler(back_to_main, pattern=r"^(main|menu:main)$"),
                CallbackQueryHandler(nl_attachment_callback_router, pattern=r"^nla:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, attachment_event_title_handler),
            ],
            SELECTING_NL_ATTACHMENT_QUERY: [
                CallbackQueryHandler(back_to_main, pattern=r"^(main|menu:main)$"),
                CallbackQueryHandler(attachment_query_callback_router, pattern=r"^nlar:"),
                CallbackQueryHandler(attachment_mutation_callback_router, pattern=r"^nlam:"),
            ],
            ADDING_EVENT_TITLE: text_state(add_event_title),
            ADDING_EVENT_PLACE: text_state(add_event_place),
            ADDING_EVENT_DATE: text_state(add_event_date),
            ADDING_EVENT_TIME: text_state(add_event_time),
            ADDING_EVENT_END_DATE: text_state(add_event_end_date),
            ADDING_EVENT_END_TIME: text_state(add_event_end_time),
            ADDING_EVENT_LINK: text_state(add_event_link),
            EDITING_AFISHA_DATE: text_state(edit_afisha_date),
            EDITING_AFISHA_TIME: text_state(edit_afisha_time),
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
