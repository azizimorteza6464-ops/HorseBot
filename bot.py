# اضافه کردن پیشنهاد پره‌ها به گزارش
            final_report += "--- پیشنهادهای ترکیبی (پره) ---\n"
            final_report += f"🎯 ۶ پره: {', '.join(pareh_6[:6])}\n"
            final_report += f"🎯 ۵ پره: {', '.join(pareh_5[:5])}\n"
            final_report += f"🎯 ۳ پره: {', '.join(pareh_3[:3])}\n"

            await context.bot.send_message(chat_id=update.effective_chat.id, text=final_report, parse_mode='Markdown')
            await browser.close()
            
    except Exception as e:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ خطا: {str(e)}")

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler('start', start))
app.add_handler(CommandHandler('check_odds', check_odds))
app.run_polling(bootstrap_retries=-1)
