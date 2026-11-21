async def main():
    app = AB().token(T).build()
    app.add_handler(CH('start', cmd_start))
    app.add_handler(CallbackQueryHandler(button_callback)) 
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    await asyncio.sleep(21500)  # <--- این خط ربات را حدود ۶ ساعت روشن نگه می‌دارد
    await app.updater.stop()
    await app.stop()
    await app.shutdown()

if __name__ == "__main__":
    try: asyncio.run(main())
    except (KeyboardInterrupt, SystemExit): pass
