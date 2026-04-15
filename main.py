from bot.app import build_app

if __name__ == "__main__":
    application = build_app()
    application.run_polling(drop_pending_updates=True)
