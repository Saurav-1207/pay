# Castle Pay — Self-Hosted UPI Checkout

Exact same architecture as antqpay.com — Python backend + Cashfree + mobile checkout page.

---

## Step 1 — Get Cashfree API Keys (Free, 5 mins)

1. Go to https://merchant.cashfree.com/merchants/signup
2. Sign up with your business details (Audiva Fm Private Limited)
3. Complete KYC (PAN + bank account)
4. Go to **Dashboard → Developers → API Keys**
5. Copy your **App ID** and **Secret Key**
6. For testing first, use the **Test environment** keys from https://test.cashfree.com

---

## Step 2 — Deploy to Railway (Free tier available)

1. Push this folder to a GitHub repo:
   ```
   git init
   git add .
   git commit -m "Castle Pay backend"
   git remote add origin https://github.com/YOUR_USERNAME/castle-pay.git
   git push -u origin main
   ```

2. Go to https://railway.app → New Project → Deploy from GitHub repo

3. Select your repo → Railway auto-detects Python + Procfile

4. Add Environment Variables in Railway dashboard:
   ```
   CF_APP_ID     = your_cashfree_app_id
   CF_SECRET_KEY = your_cashfree_secret_key
   CF_ENV        = TEST    (change to PROD when ready)
   ```

5. Your app gets a URL like: `https://castle-pay-production.up.railway.app`

---

## Step 3 — Create a Payment Link

Call the API to create an order:

```bash
curl -X POST https://your-railway-url.up.railway.app/api/create-order \
  -H "Content-Type: application/json" \
  -d '{
    "amount": 99.00,
    "customer_name": "Rahul Sharma",
    "customer_phone": "9876543210",
    "customer_email": "rahul@example.com",
    "order_note": "Premium subscription"
  }'
```

Response:
```json
{
  "success": true,
  "order_id": "CF_A1B2C3D4E5F6",
  "payment_session_id": "...",
  "amount": 99.0
}
```

Share the payment link:
```
https://your-railway-url.up.railway.app/pay/CF_A1B2C3D4E5F6
```

That's it — same as your friend's antqpay link!

---

## Step 4 — Update return URL in main.py

In `main.py`, line ~60, replace `your-domain.com` with your Railway URL:
```python
"return_url": f"https://castle-pay-production.up.railway.app/pay/{order_id}?status=done",
"notify_url": f"https://castle-pay-production.up.railway.app/api/webhook",
```

---

## How Payment Confirmation Works

1. **Auto-polling** — The checkout page polls `/api/order-status` every 5 seconds
2. **Webhook** — Cashfree calls your `/api/webhook` when payment is confirmed
3. **UTR entry** — If customer returns from app, they can enter their 12-digit UTR manually
4. **All three methods** update the order status to SUCCESS

---

## Switching to Production

1. Complete Cashfree KYC fully (takes 1–2 business days)
2. Change `CF_ENV = PROD` in Railway env vars
3. Replace App ID + Secret with Production keys
4. Done — real money flows in

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/create-order` | Create a new payment order |
| GET | `/api/order-status/{order_id}` | Check payment status |
| GET | `/api/orders` | List all orders |
| POST | `/api/webhook` | Cashfree webhook (auto) |
| GET | `/pay/{order_id}` | Mobile checkout page |

---

## Files

```
castle-pay-backend/
├── main.py          ← FastAPI backend
├── checkout.html    ← Mobile checkout page (auto-served)
├── requirements.txt ← Python dependencies
├── Procfile         ← Railway/Render start command
└── README.md        ← This file
```
