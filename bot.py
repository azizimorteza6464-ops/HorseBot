try:
                await page.evaluate("""
                    let tabs = Array.from(document.querySelectorAll('span, a, div, button'));
                    let oddsTab = tabs.find(el => el.textContent.trim() === 'آد اسب');
                    if (oddsTab) oddsTab.click();
                """)
                await page.wait_for_timeout(6000)
            except Exception:
                pass

            final_report = "🧠 تحلیل نهایی ربات (آد اسب)\n\n"
            pareh_3 = []
            pareh_5 = []
            pareh_6 = []

            # حلقه تحلیل تمام ۷ دور
            for r in range(1, 8):
                results = await analyze_round(page, r)
                if results:
                    best = results[0]
                    final_report += f"🏁 دور R{r}:\n"
                    final_report += f"🥇 پیش‌بر: {best['name']} (شانس {best['prob']}%)\n"
                    
                    if len(results) > 1:
                        final_report += f"👥 دوقلو: {results[0]['name']} + {results[1]['name']}\n"
                    
                    # جمع‌آوری اسب‌های برتر برای پره‌ها
                    if r <= 6: pareh_6.append(best['name'])
                    if r <= 5: pareh_5.append(best['name'])
                    if r in [1, 2, 3]: pareh_3.append(best['name'])
                    
                    final_report += "\n"
                else:
                    final_report += f"🏁 دور R{r}: اطلاعاتی یافت نشد.\n\n"

            # اضافه کردن پره‌ها به گزارش
            final_report += "--- پیشنهادهای ترکیبی (پره) ---\n"
            if pareh_6: final_report += f"🎯 ۶ پره: {', '.join(pareh_6[:6])}\n"
            if pareh_5: final_report += f"🎯 ۵ پره: {', '.join(pareh_5[:5])}\n"
            if pareh_3: final_report += f"🎯 ۳ پره: {', '.join(pareh_3[:3])}\n"

            await context.bot.send_message(chat_id=update.effective_chat.id, text=final_report, parse_mode='Markdown')
            await browser.close()
            
    except Exception as e:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ خطا: {str(e)}")

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler('start', start))
app.add_handler(CommandHandler('check_odds', check_odds))
app.run_polling(bootstrap_retries=-1)
