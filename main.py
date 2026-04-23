from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
import hashlib
import hmac
import time
import uuid
import os
import json
from datetime import datetime, timedelta, timezone

app = FastAPI(title="Castle Pay Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Cashfree Config (set these in Railway environment variables) ──
CF_APP_ID     = os.getenv("CF_APP_ID", "")       # Your Cashfree App ID
CF_SECRET_KEY = os.getenv("CF_SECRET_KEY", "")   # Your Cashfree Secret Key
CF_ENV        = os.getenv("CF_ENV", "TEST")       # TEST or PROD

CF_BASE = "https://sandbox.cashfree.com/pg" if CF_ENV == "TEST" else "https://api.cashfree.com/pg"

MERCHANT_NAME = "Audiva Fm Private Limited"
MERCHANT_VPA  = "paytm.s1h4uwq@pty"

# In-memory order store (use Redis/DB in production)
orders: dict = {}

# ── Models ────────────────────────────────────────────────────────
class CreateOrderRequest(BaseModel):
    amount: float
    customer_name: str = "Customer"
    customer_email: str = "customer@example.com"
    customer_phone: str = "9999999999"
    order_note: str = ""

class VerifyRequest(BaseModel):
    order_id: str
    cf_payment_id: str = ""

# ── Cashfree helpers ──────────────────────────────────────────────
def cf_headers():
    return {
        "x-client-id": CF_APP_ID,
        "x-client-secret": CF_SECRET_KEY,
        "x-api-version": "2023-08-01",
        "Content-Type": "application/json",
    }

# ── Routes ────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root():
    # Serve the checkout HTML (see checkout.html)
    try:
        with open("checkout.html", "r") as f:
            return HTMLResponse(f.read())
    except:
        return HTMLResponse("<h2>Castle Pay API is running. Frontend not found.</h2>")

@app.get("/pay/{order_id}", response_class=HTMLResponse)
async def pay_page(order_id: str):
    try:
        with open("checkout.html", "r") as f:
            html = f.read()
            # Inject the order_id so the page auto-loads
            html = html.replace("__AUTO_ORDER_ID__", order_id)
            return HTMLResponse(html)
    except:
        raise HTTPException(status_code=404, detail="Checkout page not found")

@app.post("/api/create-order")
async def create_order(req: CreateOrderRequest):
    if not CF_APP_ID or not CF_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Cashfree credentials not configured. Set CF_APP_ID and CF_SECRET_KEY environment variables.")

    order_id = "CF_" + uuid.uuid4().hex[:12].upper()
    
    payload = {
        "order_id": order_id,
        "order_amount": round(req.amount, 2),
        "order_currency": "INR",
        "order_note": req.order_note or f"Payment to {MERCHANT_NAME}",
        "customer_details": {
            "customer_id": "cust_" + uuid.uuid4().hex[:8],
            "customer_name": req.customer_name,
            "customer_email": req.customer_email,
            "customer_phone": req.customer_phone,
        },
        "order_meta": {
            "return_url": f"https://your-domain.com/pay/{order_id}?status=done",
            "notify_url": f"https://your-domain.com/api/webhook",
        },
        # Cashfree requires expiry > 15 min and < 30 days from now, in IST
        IST = timezone(timedelta(hours=5, minutes=30))
        expiry = datetime.now(IST) + timedelta(minutes=30)
        "order_expiry_time": expiry.strftime("%Y-%m-%dT%H:%M:%S+05:30"),
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{CF_BASE}/orders",
            headers=cf_headers(),
            json=payload,
            timeout=15,
        )

    if resp.status_code not in (200, 201):
        raise HTTPException(status_code=resp.status_code, detail=f"Cashfree error: {resp.text}")

    data = resp.json()
    payment_session_id = data.get("payment_session_id", "")
    cf_order_id = data.get("cf_order_id", "")

    # Store locally
    orders[order_id] = {
        "order_id": order_id,
        "cf_order_id": cf_order_id,
        "payment_session_id": payment_session_id,
        "amount": req.amount,
        "status": "ACTIVE",
        "created_at": time.time(),
        "customer_name": req.customer_name,
        "customer_phone": req.customer_phone,
    }

    return {
        "success": True,
        "order_id": order_id,
        "payment_session_id": payment_session_id,
        "cf_order_id": cf_order_id,
        "amount": req.amount,
        "upi_intent": build_upi_intent(req.amount, order_id),
    }

@app.get("/api/order-status/{order_id}")
async def order_status(order_id: str):
    if order_id not in orders:
        raise HTTPException(status_code=404, detail="Order not found")

    local = orders[order_id]

    # Poll Cashfree for real status
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{CF_BASE}/orders/{order_id}",
            headers=cf_headers(),
            timeout=10,
        )

    if resp.status_code != 200:
        return {"order_id": order_id, "status": local["status"], "amount": local["amount"]}

    cf_data = resp.json()
    cf_status = cf_data.get("order_status", "ACTIVE")

    # Map Cashfree status → our status
    status_map = {
        "PAID": "SUCCESS",
        "ACTIVE": "PENDING",
        "EXPIRED": "EXPIRED",
        "CANCELLED": "CANCELLED",
    }
    mapped = status_map.get(cf_status, "PENDING")
    orders[order_id]["status"] = mapped

    # Get payment details if paid
    payment_info = {}
    if mapped == "SUCCESS":
        async with httpx.AsyncClient() as client:
            pay_resp = await client.get(
                f"{CF_BASE}/orders/{order_id}/payments",
                headers=cf_headers(),
                timeout=10,
            )
        if pay_resp.status_code == 200:
            payments = pay_resp.json()
            if payments:
                p = payments[0]
                payment_info = {
                    "utr": p.get("bank_reference", ""),
                    "payment_method": p.get("payment_method", {}).get("upi", {}).get("upi_id", "UPI"),
                    "cf_payment_id": p.get("cf_payment_id", ""),
                }
                orders[order_id].update(payment_info)

    return {
        "order_id": order_id,
        "status": mapped,
        "amount": local["amount"],
        "customer_name": local.get("customer_name", ""),
        **payment_info,
    }

@app.get("/api/orders")
async def list_orders():
    return {"orders": list(orders.values())}

@app.post("/api/webhook")
async def webhook(request: Request):
    body = await request.body()
    sig  = request.headers.get("x-webhook-signature", "")
    ts   = request.headers.get("x-webhook-timestamp", "")

    # Verify signature
    message = ts + body.decode()
    expected = hmac.new(CF_SECRET_KEY.encode(), message.encode(), hashlib.sha256).hexdigest()
    if sig and sig != expected:
        raise HTTPException(status_code=401, detail="Invalid signature")

    data = json.loads(body)
    event_type = data.get("type", "")
    order_data = data.get("data", {}).get("order", {})
    payment_data = data.get("data", {}).get("payment", {})

    oid = order_data.get("order_id", "")
    if oid in orders:
        if event_type == "PAYMENT_SUCCESS_WEBHOOK":
            orders[oid]["status"] = "SUCCESS"
            orders[oid]["utr"] = payment_data.get("bank_reference", "")
        elif event_type in ("PAYMENT_FAILED_WEBHOOK", "PAYMENT_USER_DROPPED_WEBHOOK"):
            orders[oid]["status"] = "FAILED"

    return {"status": "ok"}

# ── UPI Intent builder (Juspay bypass params) ─────────────────────
def build_upi_intent(amount: float, order_id: str) -> dict:
    from urllib.parse import quote
    amt = f"{amount:.2f}"
    tr  = f"PYTM{int(time.time())}{order_id[-8:].upper()}"
    tid = f"PYTM{int(time.time())}"

    def q(s): return quote(str(s), safe='')

    base = (
        f"upi://pay?pa={q(MERCHANT_VPA)}"
        f"&pn={q(MERCHANT_NAME)}"
        f"&mc=5815&mode=02&orgid=159761"
        f"&tr={q(tr)}&tid={q(tid)}"
        f"&am={amt}&cu=INR"
        f"&tn={q('Order ' + order_id)}"
    )
    paytm   = base.replace("upi://pay", "paytmmp://pay")
    phonepe = (
        f"phonepe://pay?pa={q(MERCHANT_VPA)}"
        f"&pn={q(MERCHANT_NAME)}"
        f"&mc=5815&mode=02"
        f"&tr={q(tr)}&am={amt}&cu=INR"
        f"&tn={q('Order ' + order_id)}"
    )
    gpay = (
        f"tez://upi/pay?pa={q(MERCHANT_VPA)}"
        f"&pn={q(MERCHANT_NAME)}"
        f"&mc=5815&mode=02"
        f"&tr={q(tr)}&am={amt}&cu=INR"
        f"&tn={q('Order ' + order_id)}"
    )
    return {"base": base, "paytm": paytm, "phonepe": phonepe, "gpay": gpay}
