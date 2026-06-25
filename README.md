# The System — Telegram Payment Bot (Manual Confirmation)

A Telegram bot that walks buyers through your product, shows them your bank or
crypto details, collects their payment proof, and pings you to approve before
automatically delivering access. No Stripe, no automated payment processing —
you stay in control of every confirmation.

## How it works

1. Someone clicks a link on your website like `https://t.me/YourBotName?start=core`
2. The bot shows them the product + price, then asks: Bank Transfer or Crypto?
3. They see your account number / wallet address, and tap **"I've Paid"**
4. They send a screenshot or transaction reference
5. **You** get a message with their proof + Approve/Reject buttons
6. Tap Approve → the bot automatically sends them their course link / Discord invite
7. Tap Reject → the bot tells them to double check and try again

You can also see all pending orders anytime with `/orders` (admin-only).

---

## Setup

### 1. Create your bot
- Open Telegram, message **@BotFather**
- Send `/newbot`, follow the prompts, choose a name and a username ending in `bot`
  (e.g. `TheSystemPaymentsBot`)
- BotFather gives you a **token** — looks like `123456789:AAabc...` — save it

### 2. Get your own numeric Telegram ID (so the bot knows who the admin is)
- Message **@userinfobot** on Telegram — it replies with your numeric ID instantly

### 3. Fill in the config
Open `bot.py` and either:
- Replace `PUT_YOUR_BOT_TOKEN_HERE` and `ADMIN_CHAT_ID` directly in the file, **or**
- Set them as environment variables `BOT_TOKEN` and `ADMIN_CHAT_ID` (recommended —
  copy `.env.example` to `.env` and fill it in, or set these in your hosting
  platform's environment variable settings)

### 4. Add your real payment details and delivery content
Still in `bot.py`, edit:
- `PAYMENT_METHODS["bank"]["details"]` — your real bank account info
- `PAYMENT_METHODS["crypto"]["details"]` — your real wallet address(es)
- `PRODUCTS["core"]["delivery_text"]` and `PRODUCTS["full"]["delivery_text"]` —
  what gets sent automatically once you approve an order (course link, Discord
  invite, etc.)

### 5. Install dependencies
```bash
pip install -r requirements.txt
```

### 6. Run it
```bash
python bot.py
```
If it starts without errors, your bot is live and listening.

---

## Connecting it to your website

On your site, point the "Get the Core Course" and "Join the Full System"
buttons to:
```
https://t.me/YourBotUsername?start=core
https://t.me/YourBotUsername?start=full
```
(Replace `YourBotUsername` with your actual bot's @username, no @ symbol in the URL.)

The `?start=core` / `?start=full` part is a **deep link** — it tells the bot
exactly which product the person wants the moment they open the chat, so they
don't have to pick from a menu first.

---

## Hosting it 24/7

This script needs to run continuously to respond to messages. Since you didn't
have a strong preference, here's the simplest free option:

### Railway (recommended — free tier, minimal setup)
1. Push this folder to a GitHub repo (or use Railway's CLI to deploy directly)
2. Go to railway.app → New Project → Deploy from GitHub repo
3. In Railway's project settings, add environment variables `BOT_TOKEN` and
   `ADMIN_CHAT_ID`
4. Railway auto-detects Python and runs `python bot.py` — done

### Render (also free-tier friendly)
1. Push to GitHub
2. New → Background Worker (not Web Service, since this bot doesn't need a
   public URL — it uses polling, not webhooks)
3. Build command: `pip install -r requirements.txt`
4. Start command: `python bot.py`
5. Add the same environment variables

### Your own VPS
```bash
pip install -r requirements.txt
nohup python bot.py &
```
(Or better: run it under `systemd` or `pm2` so it restarts automatically if it crashes.)

---

## Data storage

Orders are saved to `orders.json` in the same folder — a simple running log of
every order, its status (pending/approved/rejected), and the buyer's Telegram
info. No database needed for this volume of orders, but back this file up
occasionally so you don't lose your order history if the server resets.

## A note on compliance

This bot never touches Telegram's native in-chat "Pay" button or payment API —
it just sends messages with your payment details and forwards proof, which is
why it can use real currency (USD bank transfer) freely. Telegram's rule that
digital goods must be sold in "Telegram Stars" only applies to their built-in
payment/invoice system, which this bot doesn't use at all.
