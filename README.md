# 🎬 Telegram Video Downloader Bot

Бот автоматично завантажує відео з **YouTube, Instagram, TikTok та X (Twitter)** коли хтось в групі скидає посилання.

---

## ✨ Можливості

- ✅ YouTube (звичайні відео + Shorts)
- ✅ Instagram (Reels, пости, IGTV)
- ✅ TikTok
- ✅ X / Twitter
- ✅ Працює в групах та особистих повідомленнях
- ✅ Показує назву відео як підпис

---

## 🚀 Деплой на Railway (покроково)

### 1. Створи бота в Telegram

1. Напиши [@BotFather](https://t.me/BotFather) → `/newbot`
2. Дай ім'я та username боту
3. Скопіюй **токен** (виглядає як `123456:ABC-DEF...`)

### 2. Додай бота в групу

1. Додай свого бота в потрібну групу
2. Зроби його **адміністратором** (або хоча б дай право читати повідомлення)
3. Якщо група приватна — увімкни **Privacy Mode** вимкненим (через BotFather → Bot Settings → Group Privacy → Turn off)

### 3. Завантаж код на GitHub

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/tg-video-bot.git
git push -u origin main
```

### 4. Деплой на Railway

1. Зайди на [railway.app](https://railway.app) → **New Project**
2. Вибери **Deploy from GitHub repo** → вибери свій репозиторій
3. Railway автоматично визначить Python проект
4. Перейди в **Variables** та додай:

| Змінна | Значення |
|--------|----------|
| `BOT_TOKEN` | Токен від BotFather |

5. Натисни **Deploy** — готово! 🎉

---

## 🍪 Instagram / приватний контент (опційно)

Якщо Instagram або TikTok не завантажуються — потрібні cookies:

1. Встанови розширення [Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)
2. Зайди в Instagram/TikTok у браузері
3. Експортуй cookies → збережи як `cookies.txt`
4. В Railway Variables додай:

| Змінна | Значення |
|--------|----------|
| `COOKIES_FILE` | `/app/cookies.txt` |

5. Завантаж `cookies.txt` через Railway → Files або використай Volume

---

## ⚙️ Локальний запуск

```bash
pip install -r requirements.txt
export BOT_TOKEN="ваш_токен"
python bot.py
```

---

## 📋 Обмеження

- Максимальний розмір відео — **50 МБ** (ліміт Telegram для ботів)
- YouTube відео довше ~10 хв можуть перевищувати ліміт

---

## 🛠 Технології

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [Railway](https://railway.app) для хостингу
