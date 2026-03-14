# -*- coding: utf-8 -*-
import os,json,logging,requests,sqlite3,html as hl
from uuid import uuid4
from datetime import datetime,timedelta
from telegram import Update,InlineKeyboardMarkup,InlineKeyboardButton
from telegram.ext import ApplicationBuilder,CommandHandler,CallbackQueryHandler,MessageHandler,ContextTypes,filters

TOKEN=os.getenv("TOKEN"); ADMIN_ID=7773622161; CHANNEL_ID=-1003833257976
LTC_ADDR="ltc1qv4u6vr0gzp9g4lq0g3qev939vdnwxghn5gtnfc"; DB_NAME="shop.db"
STARS={1:"⭐",2:"⭐⭐",3:"⭐⭐⭐",4:"⭐⭐⭐⭐",5:"⭐⭐⭐⭐⭐"}
SHIP={"tracked24":{"label":"📦 Tracked24","price":5.0,"needs_ltc":True},
      "drop":{"label":"📍 Local Drop","price":0.0,"needs_ltc":False}}
DEFAULT_TIERS=[{"qty":1,"price":10.0},{"qty":3.5,"price":5.0},{"qty":7,"price":4.0},
               {"qty":14,"price":3.0},{"qty":28,"price":2.0},{"qty":56,"price":1.0}]
RPP=5
logging.basicConfig(level=logging.INFO)

def db():
    c=sqlite3.connect(DB_NAME); c.row_factory=sqlite3.Row; return c
def q1(s,p=()):
    c=db(); r=c.execute(s,p).fetchone(); c.close(); return dict(r) if r else None
def qa(s,p=()):
    c=db(); r=c.execute(s,p).fetchall(); c.close(); return [dict(x) for x in r]
def qx(s,p=()):
    c=db(); c.execute(s,p); c.commit(); c.close()
def qxi(s,p=()):
    c=db(); cur=c.execute(s,p); r=cur.lastrowid; c.commit(); c.close(); return r
def gs(k,d=""): r=q1("SELECT value FROM settings WHERE key=?",(k,)); return r["value"] if r else d
def ss(k,v): qx("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",(k,v))
def is_admin(uid): return uid==ADMIN_ID or bool(q1("SELECT 1 FROM admins WHERE user_id=?",(uid,)))
def gdisc(code): r=q1("SELECT pct FROM discount_codes WHERE code=? AND active=1",(code.upper(),)); return r["pct"] if r else None
def get_ref(uid):
    r=q1("SELECT ref_code FROM referrals WHERE owner_id=?",(uid,))
    if r: return r["ref_code"]
    c=str(uid)[-4:]+str(uuid4())[:4].upper(); qx("INSERT OR IGNORE INTO referrals(ref_code,owner_id) VALUES(?,?)",(c,uid)); return c

def credit_ref(ref_code,new_uid):
    r=q1("SELECT owner_id,count FROM referrals WHERE ref_code=? AND owner_id!=?",(ref_code,new_uid))
    if not r: return
    n=r["count"]+1; qx("UPDATE referrals SET count=? WHERE ref_code=?",(n,ref_code)); return r["owner_id"],n

def purge():
    c=(datetime.now()-timedelta(days=30)).isoformat()
    qx("DELETE FROM drop_chats WHERE created_at<?",(c,)); qx("DELETE FROM cart WHERE created_at<?",(c,))

def init_db():
    c=db(); cur=c.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT,description TEXT,photo TEXT,stock INTEGER DEFAULT 9999,hidden INTEGER DEFAULT 0,tiers TEXT DEFAULT '[]',category_id INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS categories(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT,emoji TEXT DEFAULT '🌿');
    CREATE TABLE IF NOT EXISTS cart(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,product_id INTEGER,chosen_qty REAL,chosen_price REAL,created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY,user_id INTEGER,name TEXT,address TEXT,items_summary TEXT DEFAULT '',total_gbp REAL,total_ltc REAL,status TEXT,shipping_type TEXT DEFAULT 'tracked24');
    CREATE TABLE IF NOT EXISTS reviews(order_id TEXT PRIMARY KEY,user_id INTEGER,stars INTEGER DEFAULT 0,text TEXT,created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,username TEXT,message TEXT,reply TEXT,created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS announcements(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT,body TEXT,photo TEXT DEFAULT '',created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY,username TEXT);
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE IF NOT EXISTS drop_chats(id INTEGER PRIMARY KEY AUTOINCREMENT,order_id TEXT,user_id INTEGER,sender TEXT,message TEXT,created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS discount_codes(code TEXT PRIMARY KEY,pct REAL,active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS referrals(ref_code TEXT PRIMARY KEY,owner_id INTEGER,count INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS order_notes(order_id TEXT PRIMARY KEY,note TEXT,updated_at DATETIME DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS admins(user_id INTEGER PRIMARY KEY,username TEXT,added_at DATETIME DEFAULT CURRENT_TIMESTAMP);
    """)
    cur.execute("INSERT OR IGNORE INTO discount_codes(code,pct) VALUES('SAVE10',0.10)")
    cur.execute("INSERT OR IGNORE INTO admins(user_id,username) VALUES(?,'owner')",(ADMIN_ID,))
    for s in ["ALTER TABLE products ADD COLUMN category_id INTEGER DEFAULT 0","ALTER TABLE products ADD COLUMN hidden INTEGER DEFAULT 0","ALTER TABLE announcements ADD COLUMN photo TEXT DEFAULT ''","ALTER TABLE admins ADD COLUMN added_at DATETIME DEFAULT CURRENT_TIMESTAMP"]:
        try: cur.execute(s)
        except: pass
    c.commit(); c.close()

def fq(q): return f"{int(q)}g" if q==int(q) else f"{q}g"
def ft(t): return f"⚖️ {fq(t['qty'])} — £{t['price']:.2f}"
def IB(t,c): return InlineKeyboardButton(t,callback_data=c)
def KM(*rows): return InlineKeyboardMarkup(list(rows))
def ltc_rate():
    try: return requests.get("https://api.coingecko.com/api/v3/simple/price?ids=litecoin&vs_currencies=gbp",timeout=10).json()["litecoin"]["gbp"]
    except: return 55

def is_open():
    n=datetime.now(); return n.weekday()<6 and n.hour<11
def open_status():
    return "🟢 <b>Open now</b> · Orders close 11am daily" if is_open() else "🔴 <b>Closed</b> · Orders fulfilled next working day"

async def safe_edit(q,text,**kw):
    try: await q.edit_message_text(text,**kw)
    except:
        try: await q.message.delete()
        except: pass
        await q.message.reply_text(text,**kw)

def menu(): return KM(
    [IB("🛍️  Shop Now","products")],
    [IB("🧺  Basket","basket"),IB("📦  My Orders","orders")],
    [IB("⭐  Reviews","reviews_0"),IB("📢  News","announcements")],
    [IB("🔗  My Referral Link","my_ref"),IB("💬  Contact Us","contact_vendor")])

def back_kb(): return KM([IB("⬅️ Back","menu")])
def cancel_kb(): return KM([IB("❌ Cancel","menu")])

def co_kb(ud):
    n,a,s,dp=ud.get("co_name"),ud.get("co_addr"),ud.get("co_ship"),ud.get("co_disc_pct",0)
    addr_lbl=("✅ Address set" if a else ("📍 Local — no address needed" if s=="drop" else "🏠 Delivery Address"))
    rows=[[IB(f"✅ {hl.escape(n)}" if n else "👤 Your Name","co_name")],
          [IB(addr_lbl,"co_addr")],
          [IB(("✅ " if s=="tracked24" else "")+"📦 Tracked24 (+£5)","co_ship_tracked24"),
           IB(("✅ " if s=="drop" else "")+"📍 Local Drop","co_ship_drop")],
          [IB(f"🏷️ {ud.get('co_disc_code')} ({int(dp*100)}% off) ✅" if dp else "🏷️ Discount Code","co_disc")]]
    ready=n and s and (a or s=="drop")
    if ready: rows.append([IB("✅ Confirm & Place Order","co_confirm")])
    rows.append([IB("❌ Cancel","menu")]); return InlineKeyboardMarkup(rows)

def co_text(ud):
    s=ud.get("co_ship"); dp=ud.get("co_disc_pct",0); sub=ud.get("co_sub",0)
    sp=SHIP[s]["price"] if s else 0; sl=SHIP[s]["label"] if s else "—"
    disc=round(sub*dp,2); total=round(sub-disc+sp,2)
    addr=ud.get("co_addr") or ("Not required" if s=="drop" else "—")
    t=(f"🛒 <b>Checkout</b>\n━━━━━━━━━━━━━━━━━━\n"
       f"👤 {hl.escape(ud.get('co_name') or '—')}\n"
       f"🏠 {hl.escape(addr)}\n🚚 {sl}\n")
    if dp: t+=f"🏷️ {ud.get('co_disc_code')} (-£{disc:.2f})\n"
    t+=f"━━━━━━━━━━━━━━━━━━\n💰 <b>Total: £{total:.2f}</b>\n\n"
    if s=="drop": t+="📍 <i>Local drop — no address needed. Vendor will contact you to arrange pickup. Only select this if you are local — orders will be refunded otherwise.</i>"
    elif s=="tracked24": t+="📦 <i>Enter your full delivery address above.</i>"
    else: t+="<i>Select a delivery method above to continue.</i>"
    return t,total

def fmt_chat(oid):
    msgs=qa("SELECT sender,message,created_at FROM drop_chats WHERE order_id=? ORDER BY created_at",(oid,))
    if not msgs: return "💬 <i>No messages yet.</i>"
    return "\n\n".join(f"<b>{'👤 You' if m['sender']=='user' else '🏪 Vendor'}</b> · <i>{str(m['created_at'])[:16]}</i>\n{hl.escape(m['message'])}" for m in msgs)

def dc_user_kb(oid,closed=False):
    if closed: return KM([IB("🔓 Reopen Chat",f"dco_{oid}")])
    return KM([IB("✉️ Send Message",f"dcm_{oid}")],[IB("🔒 Close Chat",f"dcc_{oid}")])
def dc_admin_kb(oid): return KM([IB("↩️ Reply",f"dcr_{oid}"),IB("🔒 Close",f"dcac_{oid}"),IB("📋 History",f"dch_{oid}")])

async def start(u:Update,ctx:ContextTypes.DEFAULT_TYPE):
    purge(); uid=u.effective_user.id
    is_new=not q1("SELECT 1 FROM users WHERE user_id=?",(uid,))
    qx("INSERT OR IGNORE INTO users(user_id,username) VALUES(?,?)",(uid,u.effective_user.username or ""))
    if is_new and ctx.args:
        result=credit_ref(ctx.args[0],uid)
        if result:
            owner_id,count=result
            try:
                if count%15==0: await ctx.bot.send_message(owner_id,f"🎉 {count} referrals! FREE 3.5g LCG incoming 🌿",parse_mode="HTML"); await ctx.bot.send_message(ADMIN_ID,f"🎁 {count} referrals from <code>{owner_id}</code> — send 3.5g LCG.",parse_mode="HTML")
                else: await ctx.bot.send_message(owner_id,f"🔗 New referral! {count} total · {15-(count%15) if count%15 else 15} more = FREE 3.5g 🌿",parse_mode="HTML")
            except: pass
    name=hl.escape(u.effective_user.first_name or "there")
    await u.message.reply_text(
        f"🌿 <b>Welcome to Donny's Shop, {name}.</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n{open_status()}\n🕙 <b>Mon–Sat · Orders close 11am</b>\n\n"
        "📦 Tracked · 📍 Local Drop · 🔒 Trusted · ⭐ 5-Star\n\n👇 <b>Tap Shop Now</b>",
        reply_markup=menu(),parse_mode="HTML")

async def show_products(u,ctx):
    q=u.callback_query; cats=qa("SELECT * FROM categories ORDER BY id")
    if cats:
        kb=[[IB(f"{c['emoji']} {c['name']}",f"cat_{c['id']}")] for c in cats]
        uncatted=qa("SELECT id,name FROM products WHERE hidden=0 AND (category_id=0 OR category_id IS NULL) ORDER BY id")
        if uncatted: kb+=[[IB(f"🌿 {r['name']}",f"prod_{r['id']}")] for r in uncatted]
    else:
        rows=qa("SELECT id,name FROM products WHERE hidden=0 ORDER BY id"); kb=[[IB(f"🌿 {r['name']}",f"prod_{r['id']}")] for r in rows]
    kb+=[[IB("⬅️ Back","menu")]]; await safe_edit(q,"🛍️ <b>Shop</b>\n\nChoose a product:",parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def show_category(u,ctx):
    q=u.callback_query; cid=int(q.data.split("_")[1])
    cat=q1("SELECT * FROM categories WHERE id=?",(cid,))
    if not cat: return
    rows=qa("SELECT id,name FROM products WHERE hidden=0 AND category_id=? ORDER BY id",(cid,))
    kb=[[IB(f"🌿 {r['name']}",f"prod_{r['id']}")] for r in rows]+[[IB("⬅️ Back","products")]]
    txt=f"{cat['emoji']} <b>{cat['name']}</b>" if rows else "😔 No products in this category."
    await safe_edit(q,txt,parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def show_product(u,ctx):
    q=u.callback_query; pid=int(q.data.split("_")[1]); row=q1("SELECT * FROM products WHERE id=?",(pid,))
    if not row: return
    tiers=json.loads(row["tiers"]) if row["tiers"] else DEFAULT_TIERS[:]
    btns=[IB(ft(t),f"pick_{pid}_{t['qty']}_{t['price']}") for t in tiers]
    kb=[btns[i:i+2] for i in range(0,len(btns),2)]+[[IB("🧺 Basket","basket"),IB("⬅️ Back","products")]]
    try: await q.message.delete()
    except: pass
    await ctx.bot.send_photo(q.message.chat_id,row["photo"],caption=f"🌿 <b>{hl.escape(row['name'])}</b>\n\n{row['description']}\n\n"+"\n".join(ft(t) for t in tiers),reply_markup=InlineKeyboardMarkup(kb),parse_mode="HTML")

async def pick_weight(u,ctx):
    q=u.callback_query; p=q.data.split("_"); pid,qty,price=int(p[1]),float(p[2]),float(p[3])
    row=q1("SELECT name FROM products WHERE id=? AND hidden=0",(pid,))
    if not row: await q.answer("❌ Not available.",show_alert=True); return
    qx("INSERT INTO cart(user_id,product_id,chosen_qty,chosen_price) VALUES(?,?,?,?)",(q.from_user.id,pid,qty,price))
    await q.answer(f"✅ Added {fq(qty)} of {row['name']} to basket!",show_alert=True)

async def view_basket(u,ctx):
    q=u.callback_query
    items=qa("SELECT cart.id,products.name,cart.chosen_qty,cart.chosen_price FROM cart JOIN products ON cart.product_id=products.id WHERE cart.user_id=? ORDER BY cart.id",(q.from_user.id,))
    if not items: await safe_edit(q,"🧺 Your basket is empty.",reply_markup=KM([IB("🛍️ Shop","products")],[IB("⬅️ Back","menu")])); return
    txt="🧺 <b>Your Basket</b>\n\n"+"".join(f"• {hl.escape(r['name'])} {fq(r['chosen_qty'])} — £{r['chosen_price']:.2f}\n" for r in items)
    txt+=f"\n💰 <b>Total: £{sum(r['chosen_price'] for r in items):.2f}</b>"
    rm=[[IB(f"🗑️ {r['name']} {fq(r['chosen_qty'])}",f"remove_{r['id']}")] for r in items]
    await safe_edit(q,txt,parse_mode="HTML",reply_markup=InlineKeyboardMarkup(rm+[[IB("💳 Checkout","checkout")],[IB("⬅️ Back","menu")]]))

async def remove_item(u,ctx):
    q=u.callback_query; qx("DELETE FROM cart WHERE id=? AND user_id=?",(int(q.data.split("_")[1]),q.from_user.id)); await view_basket(u,ctx)

async def view_orders(u,ctx):
    q=u.callback_query; uid=q.from_user.id
    rows=qa("SELECT id,total_gbp,status,shipping_type,items_summary FROM orders WHERE user_id=? ORDER BY rowid DESC",(uid,))
    if not rows: await safe_edit(q,"📭 <b>No orders yet</b>\n\nHead to the shop and place your first order! 🛍️",parse_mode="HTML",reply_markup=KM([IB("🛍️ Shop Now","products")],[IB("⬅️ Back","menu")])); return
    smap={"Awaiting Payment":("🕐","Payment Pending"),"Paid":("✅","Confirmed"),"Dispatched":("🚚","On Its Way"),"Rejected":("❌","Rejected")}; txt="📦 <b>Your Orders</b>\n━━━━━━━━━━━━━━━━━━\n\n"; kb=[]
    for o in rows:
        icon,label=smap.get(o["status"],("📋",o["status"]))
        ship="📍 Drop" if o["shipping_type"]=="drop" else "📦 Tracked"
        txt+=f"{icon} <b>Order {o['id']}</b> · {label} · {ship} · 💷 £{o['total_gbp']:.2f}\n🛍️ {hl.escape(o['items_summary'])}\n\n"
        if o["shipping_type"]=="drop" and o["status"] in ("Awaiting Payment","Paid","Dispatched"):
            kb.append([IB(("🔒 Chat Closed" if gs(f"cc_{o['id']}","0")=="1" else "💬 Drop Chat")+f" — Order {o['id']}",f"dcv_{o['id']}")])
    if not kb: txt+="<i>Use Contact Us to reach your vendor.</i>"
    await safe_edit(q,txt[:4000],parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb+[[IB("⬅️ Back","menu")]]))

async def show_reviews(u,ctx):
    q=u.callback_query; page=int(q.data.split("_")[1])
    ms=datetime.now().replace(day=1,hour=0,minute=0,second=0,microsecond=0).isoformat()
    total=q1("SELECT COUNT(*) as c FROM reviews WHERE created_at>=?",(ms,))["c"]
    rows=qa("SELECT stars,text FROM reviews WHERE created_at>=? ORDER BY created_at DESC LIMIT ? OFFSET ?",(ms,RPP,page*RPP))
    if not rows and page==0: await safe_edit(q,"💬 No reviews this month yet.",reply_markup=back_kb()); return
    txt=f"⭐ <b>Reviews of the Month</b> ({total})\n\n"+"".join(f"{STARS.get(r['stars'],'')}\n{hl.escape(r['text'])}\n\n" for r in rows)
    pages=(total-1)//RPP if total else 0; nav=[]
    if page>0: nav.append(IB("◀️ Prev",f"reviews_{page-1}"))
    if page<pages: nav.append(IB("Next ▶️",f"reviews_{page+1}"))
    await safe_edit(q,txt[:4000],parse_mode="HTML",reply_markup=InlineKeyboardMarkup(([nav] if nav else [])+[[IB("⬅️ Back","menu")]]))

async def review_start(u,ctx):
    q=u.callback_query; ctx.user_data["rev_order"]=q.data[7:]; await safe_edit(q,"⭐ Rate your order:",reply_markup=KM([IB("⭐ 1","stars_1"),IB("⭐⭐ 2","stars_2"),IB("⭐⭐⭐ 3","stars_3")],[IB("⭐⭐⭐⭐ 4","stars_4"),IB("⭐⭐⭐⭐⭐ 5","stars_5")]))

async def pick_stars(u,ctx):
    q=u.callback_query; s=int(q.data.split("_")[1]); ctx.user_data.update({"rev_stars":s,"wf":"review_text"}); await safe_edit(q,f"✨ {STARS[s]}\n\n✏️ Write your review:",reply_markup=cancel_kb())

async def show_announcements(u,ctx):
    q=u.callback_query; rows=qa("SELECT id,title,body,photo,created_at FROM announcements ORDER BY id DESC LIMIT 5")
    if not rows: await safe_edit(q,"📢 No announcements.",reply_markup=back_kb()); return
    first=rows[0]; txt=f"📢 <b>{hl.escape(first['title'])}</b>\n\n{hl.escape(first['body'])}\n<i>{str(first['created_at'])[:10]}</i>"
    if len(rows)>1: txt+="\n\n<b>Previous:</b>\n"+"".join(f"• {hl.escape(r['title'])} <i>{str(r['created_at'])[:10]}</i>\n" for r in rows[1:])
    kb=[[IB("⬅️ Back","menu")]]
    if first.get("photo"):
        try: await q.message.delete()
        except: pass
        await ctx.bot.send_photo(q.message.chat_id,first["photo"],caption=txt,parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))
    else: await safe_edit(q,txt[:4000],parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def ann_start(u,ctx):
    q=u.callback_query; ctx.user_data.update({"wf":"ann_title"}); ctx.user_data.pop("ann_photo",""); await safe_edit(q,"📢 Enter announcement title:",reply_markup=cancel_kb())

async def my_ref(u,ctx):
    q=u.callback_query; uid=q.from_user.id; rc=get_ref(uid)
    cnt=(q1("SELECT count FROM referrals WHERE owner_id=?",(uid,)) or {}).get("count",0); nxt=15-(cnt%15) if cnt%15 else 15
    uname=(await ctx.bot.get_me()).username
    await safe_edit(q,f"🔗 <b>Your Referral Link</b>\n\n<code>https://t.me/{uname}?start={rc}</code>\n\n👥 <b>{cnt}</b> joined · <b>{nxt} more</b> = FREE 3.5g Lemon Cherry Gelato 🌿\n<i>Milestones: 15, 30, 45...</i>",parse_mode="HTML",reply_markup=back_kb())

async def contact_start(u,ctx):
    q=u.callback_query; ctx.user_data["wf"]="contact"; await safe_edit(q,"💬 <b>Contact Us</b>\n\nType your message and we'll get back to you:",parse_mode="HTML",reply_markup=cancel_kb())

async def admin_reply_cmd(u,ctx):
    if not is_admin(u.effective_user.id): return
    if not ctx.args or len(ctx.args)<2: await u.message.reply_text("Usage: /reply <id> <text>"); return
    try: mid=int(ctx.args[0])
    except: await u.message.reply_text("⚠️ Invalid ID."); return
    txt=" ".join(ctx.args[1:]); row=q1("SELECT user_id,username,message FROM messages WHERE id=?",(mid,))
    if not row: await u.message.reply_text("❌ Not found."); return
    qx("UPDATE messages SET reply=? WHERE id=?",(txt,mid))
    try: await ctx.bot.send_message(row["user_id"],f"💬 <b>Reply from Donny's Shop</b>\n\n<i>Your msg: {hl.escape(row['message'])}</i>\n\n✉️ {hl.escape(txt)}",parse_mode="HTML",reply_markup=menu()); await u.message.reply_text(f"✅ Replied to @{row['username']}.")
    except Exception as e: await u.message.reply_text(f"❌ {e}")

async def order_lookup_cmd(u,ctx):
    if not is_admin(u.effective_user.id): return
    if not ctx.args: await u.message.reply_text("Usage: /order <id>"); return
    oid=ctx.args[0]; row=q1("SELECT * FROM orders WHERE id=?",(oid,))
    if not row: await u.message.reply_text("❌ Not found."); return
    sl=SHIP.get(row["shipping_type"],{}).get("label",row["shipping_type"])
    emap={"Awaiting Payment":"⏳","Paid":"✅","Dispatched":"🚚","Rejected":"❌"}
    note=q1("SELECT note FROM order_notes WHERE order_id=?",(oid,))
    note_txt=f"\n📝 {hl.escape(note['note'])}" if note else ""
    ltc=f" | {row['total_ltc']} LTC" if row["total_ltc"] else ""
    txt=f"🔖 <b>{oid}</b> {emap.get(row['status'],'')}\n👤 {row['name']}\n🏠 {row['address']}\n📦 {row['items_summary']}\n🚚 {sl} · 💷 £{row['total_gbp']:.2f}{ltc}{note_txt}"
    kb=([[IB("✅ Confirm",f"adm_ok_{oid}"),IB("❌ Reject",f"adm_no_{oid}")]] if row["status"]=="Awaiting Payment" else [])+([[IB("🚚 Dispatch",f"adm_go_{oid}")]] if row["status"]=="Paid" and row["shipping_type"]!="drop" else [])+([[IB("💬 Drop Chat",f"dch_{oid}")]] if row["shipping_type"]=="drop" else [])+[[IB("📝 Note",f"adm_note_{oid}")]]
    await u.message.reply_text(txt,parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def checkout_start(u,ctx):
    q=u.callback_query; uid=q.from_user.id; prices=qa("SELECT chosen_price FROM cart WHERE user_id=?",(uid,))
    if not prices: await safe_edit(q,"🧺 Basket empty.",reply_markup=menu()); return
    ctx.user_data.update({"co_name":None,"co_addr":None,"co_ship":None,"co_disc_code":None,"co_disc_pct":0,"co_sub":round(sum(r["chosen_price"] for r in prices),2),"wf":None})
    txt,_=co_text(ctx.user_data); await safe_edit(q,txt,parse_mode="HTML",reply_markup=co_kb(ctx.user_data))

async def co_name_start(u,ctx):
    q=u.callback_query; ctx.user_data["wf"]="co_name"; await safe_edit(q,"👤 Enter your full name:",reply_markup=KM([IB("❌ Cancel","co_refresh")]))
async def co_addr_skip(u,ctx):
    q=u.callback_query; ctx.user_data["co_addr"]=""; ctx.user_data["wf"]=None
    txt,_=co_text(ctx.user_data); await safe_edit(q,txt,parse_mode="HTML",reply_markup=co_kb(ctx.user_data))

async def co_addr_start(u,ctx):
    q=u.callback_query
    if ctx.user_data.get("co_ship")=="drop":
        await safe_edit(q,"📍 <b>Local Drop</b>\n\nNo address needed — tap Skip, or type a rough area so the vendor can plan the meet.",parse_mode="HTML",reply_markup=KM([IB("⏭️ Skip (no address)","co_addr_skip")],[IB("❌ Cancel","co_refresh")]))
    else:
        ctx.user_data["wf"]="co_addr"
        await safe_edit(q,"🏠 Enter your full delivery address:",reply_markup=KM([IB("❌ Cancel","co_refresh")]))
async def co_disc_start(u,ctx):
    q=u.callback_query; ctx.user_data["wf"]="co_disc"; codes=qa("SELECT code,pct FROM discount_codes WHERE active=1")
    hint=", ".join(f"{r['code']} ({int(r['pct']*100)}% off)" for r in codes)
    await safe_edit(q,f"🏷️ Enter discount code:\n<i>{hint}</i>",parse_mode="HTML",reply_markup=KM([IB("❌ Cancel","co_refresh")]))
async def co_ship(u,ctx):
    q=u.callback_query; ctx.user_data["co_ship"]=q.data.split("co_ship_")[1]; txt,_=co_text(ctx.user_data); await safe_edit(q,txt,parse_mode="HTML",reply_markup=co_kb(ctx.user_data))
async def co_refresh(u,ctx):
    q=u.callback_query; ctx.user_data["wf"]=None; txt,_=co_text(ctx.user_data); await safe_edit(q,txt,parse_mode="HTML",reply_markup=co_kb(ctx.user_data))

async def co_confirm(u,ctx):
    q=u.callback_query; uid=q.from_user.id; ud=ctx.user_data
    name,addr,sk=ud.get("co_name"),ud.get("co_addr") or "",ud.get("co_ship")
    if not(name and sk): await q.answer("⚠️ Please enter your name and select delivery.",show_alert=True); return
    if sk=="tracked24" and not addr: await q.answer("⚠️ Delivery address required for Tracked24.",show_alert=True); return
    items=qa("SELECT products.name,cart.chosen_qty,cart.chosen_price FROM cart JOIN products ON cart.product_id=products.id WHERE cart.user_id=?",(uid,))
    if not items: await safe_edit(q,"🧺 Basket empty.",reply_markup=menu()); return
    lines='\n'.join(f"  • {r['name']} {fq(r['chosen_qty'])} — £{r['chosen_price']:.2f}" for r in items); summary=", ".join(f"{r['name']} {fq(r['chosen_qty'])}" for r in items)
    sub=round(sum(r["chosen_price"] for r in items),2); dp=ud.get("co_disc_pct",0); sp=SHIP[sk]["price"]; sl=SHIP[sk]["label"]; needs_ltc=SHIP[sk]["needs_ltc"]
    disc=round(sub*dp,2); gbp=round(sub-disc+sp,2); ltc=round(gbp/ltc_rate(),6) if needs_ltc else 0
    oid=str(uuid4())[:8]; addr_disp=addr or "Local drop — no address"
    qx("INSERT INTO orders(id,user_id,name,address,items_summary,total_gbp,total_ltc,status,shipping_type) VALUES(?,?,?,?,?,?,?,?,?)",(oid,uid,name,addr_disp,summary,gbp,ltc,"Awaiting Payment",sk))
    qx("DELETE FROM cart WHERE user_id=?",(uid,))
    if sk=="drop": qx("INSERT INTO drop_chats(order_id,user_id,sender,message) VALUES(?,?,?,?)",(oid,uid,"vendor",f"👋 Hi {name}! Order received — reply here to arrange pickup."))
    uname=q.from_user.username or str(uid); ltcpart=f" | {ltc} LTC" if needs_ltc else ""
    notif=f"🛒 <b>Order {oid}</b> · 👤 {hl.escape(name)} (@{uname}) · 📦 {summary} · 🚚 {sl} · 💷 £{gbp}{ltcpart}"
    await ctx.bot.send_message(CHANNEL_ID,notif,parse_mode="HTML")
    adm_rows=[[IB("✅ Confirm",f"adm_ok_{oid}"),IB("❌ Reject",f"adm_no_{oid}")]]+([[IB("💬 Chat",f"dch_{oid}")]] if sk=="drop" else [])
    try: await ctx.bot.send_message(ADMIN_ID,notif,parse_mode="HTML",reply_markup=InlineKeyboardMarkup(adm_rows))
    except: pass
    s2="━━━━━━━━━━━━━━━━━━━━"; now=datetime.now().strftime("%d/%m/%Y %H:%M")
    disc_line=f"🏷️ {ud.get('co_disc_code','')} -£{disc:.2f}\n" if dp else ""; ship_line=f"📦 +£{sp:.2f}\n" if sp else ""
    invoice=(f"🧾 <b>INVOICE — Donny's Shop</b>\n{s2}\n📋 <b>Order {oid}</b> · {now}\n{s2}\n"
             f"👤 {hl.escape(name)} · 🏠 {hl.escape(addr_disp)} · 🚚 {sl}\n{s2}\n<b>Items:</b>\n{lines}\n{s2}\n💷 £{sub:.2f}\n{disc_line}{ship_line}\n💰 <b>TOTAL: £{gbp:.2f}</b>\n{s2}\n")
    if needs_ltc:
        invoice+=f"\n📤 Send <b>{ltc} LTC</b> to:\n<code>{LTC_ADDR}</code>\n\n⚠️ <i>Exact amount. Tap 'I Have Paid' once sent.</i>"
        kb=KM([IB("✅ I Have Paid",f"paid_{oid}")],[IB("📦 My Orders","orders")])
    else:
        invoice+="\n📍 Open chat below to arrange pickup with Donny.\n⚠️ <i>Local only — non-local orders refunded.</i>"
        kb=KM([IB("💬 Chat — Arrange Pickup",f"dcv_{oid}")],[IB("📦 My Orders","orders")])
    try: await q.message.delete()
    except: pass
    await q.message.reply_text(invoice,parse_mode="HTML",reply_markup=kb)
    for k in [k for k in list(ud) if k.startswith("co_")]: ud.pop(k)

async def dropchat_view(u,ctx):
    q=u.callback_query; oid=q.data[4:]; purge(); closed=gs(f"cc_{oid}","0")=="1"
    o=q1("SELECT items_summary,total_gbp,shipping_type FROM orders WHERE id=?",(oid,))
    ship="📍 Local Drop" if (o and o["shipping_type"]=="drop") else "📦 Tracked24"
    hdr=f"💬 <b>Drop Chat — Order {oid}</b>\n━━━━━━━━━━━━━━━━━━\n"
    if o: hdr+=f"🛍️ {hl.escape(o['items_summary'])} · {ship} · 💷 £{o['total_gbp']:.2f}\n"
    if closed: hdr+="🔒 <i>Closed by vendor.</i>\n"
    try: await q.message.delete()
    except: pass
    await q.message.reply_text(f"{hdr}━━━━━━━━━━━━━━━━━━\n\n{fmt_chat(oid)}"[:4000],parse_mode="HTML",reply_markup=dc_user_kb(oid,closed))

async def dropchat_msg_start(u,ctx):
    q=u.callback_query; oid=q.data[4:]; ctx.user_data.update({"dc_oid":oid,"wf":"drop_msg_user"})
    try: await q.message.delete()
    except: pass
    await q.message.reply_text(f"💬 <b>Order {oid}</b>\n\n✉️ Type your message:",parse_mode="HTML",reply_markup=KM([IB("❌ Cancel",f"dcv_{oid}")]))

async def dropchat_reply_start(u,ctx):
    q=u.callback_query
    if not is_admin(u.effective_user.id): return
    oid=q.data[4:]; ctx.user_data.update({"dc_oid":oid,"wf":"drop_msg_admin"})
    o=q1("SELECT name,items_summary FROM orders WHERE id=?",(oid,))
    hdr=f"↩️ <b>Reply {oid}</b>"+(f" | {hl.escape(o['name'])} | {o['items_summary']}" if o else "")
    await safe_edit(q,f"{hdr}\n\n{fmt_chat(oid)}\n\n✏️ Type reply:",parse_mode="HTML",reply_markup=KM([IB("❌ Cancel","menu")]))

async def dropchat_history(u,ctx):
    q=u.callback_query
    if not is_admin(u.effective_user.id): return
    oid=q.data[4:]; closed=gs(f"cc_{oid}","0")=="1"
    o=q1("SELECT name,items_summary,total_gbp FROM orders WHERE id=?",(oid,))
    hdr=f"📋 <b>Chat {oid}</b>"+(f"\n👤 {hl.escape(o['name'])} | {o['items_summary']} | 💷 £{o['total_gbp']:.2f}" if o else "")
    note=q1("SELECT note FROM order_notes WHERE order_id=?",(oid,))
    if note: hdr+=f"\n📝 {hl.escape(note['note'])}"
    kb=dc_admin_kb(oid) if not closed else KM([IB("🔓 Reopen",f"dco_{oid}")])
    await safe_edit(q,f"{hdr}\n\n{fmt_chat(oid)}"[:4000],parse_mode="HTML",reply_markup=kb)

async def dropchat_close(u,ctx):
    q=u.callback_query; oid=q.data.split("_",1)[1]; ss(f"cc_{oid}","1"); r=q1("SELECT user_id FROM orders WHERE id=?",(oid,))
    if r and is_admin(u.effective_user.id):
        try: await ctx.bot.send_message(r["user_id"],f"🔒 Drop Chat <code>{oid}</code> closed.",parse_mode="HTML",reply_markup=menu())
        except: pass
    await safe_edit(q,f"🔒 Chat <code>{oid}</code> closed.",parse_mode="HTML",reply_markup=KM([IB("🔓 Reopen",f"dco_{oid}")]))

async def dropchat_open(u,ctx):
    q=u.callback_query; oid=q.data[4:]; ss(f"cc_{oid}","0"); await safe_edit(q,f"💬 <b>Drop Chat — Order {oid}</b>\n\n{fmt_chat(oid)}",parse_mode="HTML",reply_markup=dc_user_kb(oid,False))

async def adm_drop_overview(u,ctx):
    q=u.callback_query
    if not is_admin(u.effective_user.id): return
    rows=qa("SELECT o.id,o.name,o.status,(SELECT COUNT(*) FROM drop_chats dc WHERE dc.order_id=o.id) as msgs FROM orders o WHERE o.shipping_type='drop' ORDER BY o.rowid DESC LIMIT 20")
    if not rows: await safe_edit(q,"📍 No drop orders yet.",reply_markup=back_kb()); return
    emap={"Awaiting Payment":"⏳","Paid":"✅","Dispatched":"🚚","Rejected":"❌"}
    kb=[[IB(("🔒 " if gs("cc_"+o["id"],"0")=="1" else "💬 ")+f"{o['id']} {o['name']} {emap.get(o['status'],'')} ({o['msgs']})",f"dch_{o['id']}")] for o in rows]+[[IB("⬅️ Back","menu")]]
    await safe_edit(q,"📍 <b>Drop Orders</b>",parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def adm_note_start(u,ctx):
    q=u.callback_query
    if not is_admin(u.effective_user.id): return
    oid=q.data[9:]; ctx.user_data.update({"note_oid":oid,"wf":"order_note"}); note=q1("SELECT note FROM order_notes WHERE order_id=?",(oid,))
    cur=f"\nCurrent: <i>{hl.escape(note['note'])}</i>" if note else ""
    await q.message.reply_text(f"📝 <b>Note for {oid}</b>{cur}\n\nType new note:",parse_mode="HTML")

async def adm_admins(u,ctx):
    q=u.callback_query; caller=u.effective_user.id
    if not is_admin(caller): return
    rows=qa("SELECT user_id,username FROM admins ORDER BY added_at")
    txt="👥 <b>Admin Team</b>\n━━━━━━━━━━━━━━━━━━\n\n"+"".join(f"{'👑' if r['user_id']==ADMIN_ID else '🔑'} <code>{r['user_id']}</code> @{r['username'] or '?'}\n" for r in rows)
    if caller==ADMIN_ID:
        kb=[[IB("➕ Add Admin","adm_addadmin")]]+[[IB(f"🗑️ Remove {r['username'] or r['user_id']}",f"adm_rmadmin_{r['user_id']}")] for r in rows if r["user_id"]!=ADMIN_ID]+[[IB("⬅️ Back","menu")]]
        txt+="\n<i>Tap ➕ to add · 🗑️ to remove.</i>"
    else:
        kb=[[IB("⬅️ Back","menu")]]; txt+="\n<i>Only the owner can add/remove admins.</i>"
    await safe_edit(q,txt,parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def adm_addadmin_start(u,ctx):
    q=u.callback_query
    if not is_admin(u.effective_user.id): return
    ctx.user_data["wf"]="add_admin"; await safe_edit(q,"➕ <b>Add Admin</b>\n\nSend their numeric Telegram <b>user_id</b>.\n<i>They can find it by messaging @userinfobot</i>",parse_mode="HTML",reply_markup=cancel_kb())

async def adm_rmadmin(u,ctx):
    q=u.callback_query
    if not is_admin(u.effective_user.id): return
    uid=int(q.data.split("adm_rmadmin_")[1])
    if uid==ADMIN_ID: await q.answer("❌ Cannot remove the owner.",show_alert=True); return
    r=q1("SELECT username FROM admins WHERE user_id=?",(uid,)); qx("DELETE FROM admins WHERE user_id=?",(uid,))
    await q.answer(f"✅ Removed {r['username'] if r else uid}",show_alert=True); await adm_admins(u,ctx)

async def admin_panel(u:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if not is_admin(u.effective_user.id): return
    orders=qa("SELECT id,status,shipping_type FROM orders ORDER BY rowid DESC LIMIT 30")
    unread=q1("SELECT COUNT(*) as c FROM messages WHERE reply IS NULL")["c"]
    drops=len([o for o in orders if o["shipping_type"]=="drop" and o["status"] in ("Awaiting Payment","Paid")])
    kb=[]
    for o in orders:
        if o["status"]=="Awaiting Payment":
            kb.append([IB(f"✅ {o['id']}",f"adm_ok_{o['id']}"),IB(f"❌ {o['id']}",f"adm_no_{o['id']}")])
        elif o["status"]=="Paid" and o["shipping_type"]!="drop":
            kb.append([IB(f"🚚 Dispatch {o['id']}",f"adm_go_{o['id']}")])
    kb+=[[IB("➕ Add Product","adm_addprod"),IB("🗑️ Remove Product","adm_rmprod")],
         [IB("✏️ Edit Description","adm_editdesc"),IB("✏️ Edit Tiers","adm_tiers")],
         [IB("👁️ Hide/Show","adm_hideprod"),IB("📂 Categories","adm_cats")],
         [IB(f"💬 Messages{f' ({unread})' if unread else ''}","adm_msgs"),
          IB(f"📍 Drops{f' ({drops})' if drops else ''}","adm_drops")],
         [IB("🏷️ Discounts","adm_discounts"),IB("📢 Announcement","adm_announce")],
         [IB("👥 Manage Admins","adm_admins")]]
    await u.message.reply_text("🔧 <b>Admin Panel</b>",parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def adm_confirm(u,ctx):
    q=u.callback_query; oid=q.data[7:]; qx("UPDATE orders SET status='Paid' WHERE id=?",(oid,))
    r=q1("SELECT user_id,shipping_type,items_summary FROM orders WHERE id=?",(oid,))
    if r:
        if r["shipping_type"]=="drop":
            try: await ctx.bot.send_message(r["user_id"],f"✅ <b>Order {oid} confirmed!</b> Open Drop Chat to arrange pickup.",parse_mode="HTML",reply_markup=KM([IB("💬 Drop Chat",f"dcv_{oid}")]))
            except: pass
        else:
            try: await ctx.bot.send_message(r["user_id"],f"✅ Payment confirmed — <code>{oid}</code>! 🌟",parse_mode="HTML",reply_markup=KM([IB("⭐ Leave Review",f"review_{oid}")]))
            except: pass
    await safe_edit(q,f"✅ Confirmed {oid}")

async def adm_reject(u,ctx):
    q=u.callback_query; oid=q.data[7:]
    qx("UPDATE orders SET status='Rejected' WHERE id=?",(oid,))
    row=q1("SELECT user_id FROM orders WHERE id=?",(oid,))
    if row:
        try: await ctx.bot.send_message(row["user_id"],f"❌ Order <code>{oid}</code> rejected. Contact us if you have questions.",parse_mode="HTML")
        except: pass
    await safe_edit(q,f"❌ Rejected {oid}")

async def adm_dispatch(u,ctx):
    q=u.callback_query; oid=q.data[7:]; qx("UPDATE orders SET status='Dispatched' WHERE id=?",(oid,))
    r=q1("SELECT user_id,items_summary FROM orders WHERE id=?",(oid,))
    if r:
        try: await ctx.bot.send_message(r["user_id"],f"🚚 <b>Order {oid} dispatched!</b> 📬\n{r['items_summary']}",parse_mode="HTML")
        except: pass
    await safe_edit(q,f"🚚 Dispatched {oid}")

async def adm_msgs(u,ctx):
    q=u.callback_query
    if not is_admin(u.effective_user.id): return
    rows=qa("SELECT id,username,message,reply FROM messages ORDER BY id DESC LIMIT 15")
    if not rows: await safe_edit(q,"📭 No messages.",reply_markup=back_kb()); return
    txt="💬 <b>Messages</b>\n\n"+"".join(f"{'✅' if r['reply'] else '⏳'} <code>{r['id']}</code> @{r['username']}\n{hl.escape(r['message'][:80])}\n/reply {r['id']} &lt;text&gt;\n\n" for r in rows)
    await safe_edit(q,txt[:4000],parse_mode="HTML",reply_markup=back_kb())
async def user_paid(u,ctx):
    q=u.callback_query; oid=q.data[5:]
    row=q1("SELECT shipping_type FROM orders WHERE id=?",(oid,))
    if row and row["shipping_type"]=="drop":
        await safe_edit(q,"✅ Order confirmed! Vendor will be in touch.",reply_markup=KM([IB("💬 Open Drop Chat",f"dcv_{oid}")]))
    else:
        await ctx.bot.send_message(ADMIN_ID,f"💰 Payment claim for <code>{oid}</code> from {q.from_user.id}",parse_mode="HTML")
        await safe_edit(q,"⏳ Submitted — awaiting admin confirmation.")

async def adm_cats(u,ctx):
    q=u.callback_query
    if not is_admin(u.effective_user.id): return
    cats=qa("SELECT * FROM categories ORDER BY id"); txt="📂 <b>Categories</b>\n\n"+("\n".join(f"{c['emoji']} {c['name']}" for c in cats) if cats else "None yet.")
    kb=[[IB(f"✏️ {c['emoji']} {c['name']}",f"cat_assign_{c['id']}")] for c in cats]+[[IB("➕ New","adm_newcat"),IB("🗑️ Delete","adm_delcat")],[IB("⬅️ Back","menu")]]
    await safe_edit(q,txt,parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def adm_newcat(u,ctx):
    q=u.callback_query; ctx.user_data["wf"]="new_cat"
    await safe_edit(q,"📂 Enter category name and emoji:\n<code>Emoji Name</code>\ne.g. <code>🍃 Indoor</code>",parse_mode="HTML",reply_markup=cancel_kb())

async def adm_delcat_list(u,ctx):
    q=u.callback_query; cats=qa("SELECT * FROM categories ORDER BY id")
    if not cats: await safe_edit(q,"No categories.",reply_markup=back_kb()); return
    await safe_edit(q,"🗑️ Delete which?",reply_markup=InlineKeyboardMarkup([[IB(f"🗑️ {c['emoji']} {c['name']}",f"delcat_{c['id']}")] for c in cats]+[[IB("⬅️ Back","adm_cats")]]))

async def adm_delcat_do(u,ctx):
    q=u.callback_query; cid=int(q.data.split("_")[1]); qx("UPDATE products SET category_id=0 WHERE category_id=?",(cid,)); qx("DELETE FROM categories WHERE id=?",(cid,)); await adm_cats(u,ctx)

async def adm_cat_assign(u,ctx):
    q=u.callback_query; cid=int(q.data.split("_")[2]); ctx.user_data["assign_cid"]=cid
    cat=q1("SELECT name,emoji FROM categories WHERE id=?",(cid,))
    rows=qa("SELECT id,name,category_id FROM products ORDER BY id")
    kb=[[IB(("✅ " if r["category_id"]==cid else "○ ")+r["name"],f"togglecat_{r['id']}_{cid}")] for r in rows]
    kb+=[[IB("✅ Done","adm_cats")]]
    await safe_edit(q,f"📂 Assign products to <b>{cat['emoji']} {cat['name']}</b>\nTap to toggle:",parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def adm_togglecat(u,ctx):
    q=u.callback_query; parts=q.data.split("_"); pid,cid=int(parts[1]),int(parts[2])
    row=q1("SELECT category_id FROM products WHERE id=?",(pid,))
    if row: qx("UPDATE products SET category_id=? WHERE id=?",(0 if row["category_id"]==cid else cid,pid))
    # Refresh the assign view
    u.callback_query.data=f"cat_assign_{cid}"; await adm_cat_assign(u,ctx)

async def adm_rmprod_list(u,ctx):
    q=u.callback_query; rows=qa("SELECT id,name FROM products ORDER BY id")
    if not rows: await safe_edit(q,"No products.",reply_markup=back_kb()); return
    await safe_edit(q,"🗑️ Remove which?",reply_markup=InlineKeyboardMarkup([[IB(f"🗑️ {r['name']}",f"rmprod_{r['id']}")] for r in rows]+[[IB("⬅️ Back","menu")]]))

async def adm_rmprod_confirm(u,ctx):
    q=u.callback_query; pid=int(q.data.split("_")[1]); r=q1("SELECT name FROM products WHERE id=?",(pid,))
    if r: await safe_edit(q,f"🗑️ Delete <b>{hl.escape(r['name'])}</b>?",parse_mode="HTML",reply_markup=KM([IB("✅ Yes",f"rmprod_yes_{pid}"),IB("❌ No","menu")]))

async def adm_rmprod_do(u,ctx):
    q=u.callback_query; qx("DELETE FROM products WHERE id=?",(int(q.data.split("_")[2]),)); await safe_edit(q,"✅ Product deleted.")

async def adm_hideprod_list(u,ctx):
    q=u.callback_query; rows=qa("SELECT id,name,hidden FROM products ORDER BY id")
    if not rows: await safe_edit(q,"No products.",reply_markup=back_kb()); return
    await safe_edit(q,"👁️ <b>Hide/Show Products</b>",parse_mode="HTML",reply_markup=InlineKeyboardMarkup([[IB(f"{'👁️ Show' if r['hidden'] else '🙈 Hide'} {r['name']}",f"togglehide_{r['id']}")] for r in rows]+[[IB("⬅️ Back","menu")]]))

async def adm_togglehide(u,ctx):
    q=u.callback_query; pid=int(q.data.split("_")[1]); r=q1("SELECT name,hidden FROM products WHERE id=?",(pid,))
    if not r: await q.answer(); return
    qx("UPDATE products SET hidden=? WHERE id=?",(0 if r["hidden"] else 1,pid))
    await q.answer(f"{'Shown' if r['hidden'] else 'Hidden'}: {r['name']}",show_alert=True); await adm_hideprod_list(u,ctx)

async def adm_editdesc_list(u,ctx):
    q=u.callback_query; rows=qa("SELECT id,name FROM products ORDER BY id")
    if not rows: await safe_edit(q,"No products.",reply_markup=back_kb()); return
    await safe_edit(q,"✏️ Edit which?",reply_markup=InlineKeyboardMarkup([[IB(f"✏️ {r['name']}",f"editdesc_{r['id']}")] for r in rows]+[[IB("⬅️ Back","menu")]]))

async def adm_editdesc_start(u,ctx):
    q=u.callback_query; pid=int(q.data.split("_")[1]); ctx.user_data.update({"edit_pid":pid,"wf":"edit_desc"})
    r=q1("SELECT name,description FROM products WHERE id=?",(pid,))
    await safe_edit(q,f"✏️ <b>{hl.escape(r['name'])}</b>\n\n{r['description']}\n\nSend new description:",parse_mode="HTML",reply_markup=cancel_kb())

async def adm_list_tiers(u,ctx):
    q=u.callback_query; rows=qa("SELECT id,name FROM products ORDER BY id")
    if not rows: await safe_edit(q,"No products.",reply_markup=back_kb()); return
    await safe_edit(q,"✏️ Pick product:",reply_markup=InlineKeyboardMarkup([[IB(f"🌿 {r['name']}",f"edtier_{r['id']}")] for r in rows]+[[IB("⬅️ Back","menu")]]))

async def adm_show_tiers(u,ctx):
    q=u.callback_query; pid=int(q.data.split("_")[1]); ctx.user_data["tpid"]=pid
    row=q1("SELECT name,tiers FROM products WHERE id=?",(pid,)); tiers=json.loads(row["tiers"]); ctx.user_data["wf"]="edit_tiers"
    await q.message.reply_text(f"✏️ <b>{hl.escape(row['name'])}</b>\n\n"+"\n".join(ft(t) for t in tiers)+"\n\n<code>qty,price</code> per line e.g.\n<code>3.5,20\n7,35</code>\n\n/cancel to stop",parse_mode="HTML")

async def adm_discounts(u,ctx):
    q=u.callback_query
    if not is_admin(u.effective_user.id): return
    rows=qa("SELECT code,pct,active FROM discount_codes ORDER BY code")
    txt="🏷️ <b>Discount Codes</b>\n\n"+"".join(f"<code>{r['code']}</code> {int(r['pct']*100)}% {'✅' if r['active'] else '❌'}\n" for r in rows)
    kb=[[IB(f"{'Disable' if r['active'] else 'Enable'} {r['code']}",f"toggledisc_{r['code']}")] for r in rows]
    kb+=[[IB("➕ Add Code","adm_adddisc")],[IB("⬅️ Back","menu")]]
    await safe_edit(q,txt or "No codes.",parse_mode="HTML",reply_markup=InlineKeyboardMarkup(kb))

async def adm_toggledisc(u,ctx):
    q=u.callback_query; c=q.data.split("toggledisc_")[1]; r=q1("SELECT active FROM discount_codes WHERE code=?",(c,))
    if r: qx("UPDATE discount_codes SET active=? WHERE code=?",(0 if r["active"] else 1,c))
    await adm_discounts(u,ctx)

async def adm_adddisc_start(u,ctx):
    q=u.callback_query; ctx.user_data["wf"]="disc_code"
    await safe_edit(q,"🏷️ Send as <code>CODE,10</code> (e.g. SAVE20,20 for 20% off):",parse_mode="HTML",reply_markup=cancel_kb())

async def on_message(u:Update,ctx:ContextTypes.DEFAULT_TYPE):
    wf=ctx.user_data.get("wf"); uid=u.effective_user.id; txt=u.message.text or ""
    if wf=="co_name":
        ctx.user_data["co_name"]=txt.strip(); ctx.user_data["wf"]=None
        t,_=co_text(ctx.user_data); await u.message.reply_text(t,parse_mode="HTML",reply_markup=co_kb(ctx.user_data))
    elif wf=="co_addr":
        ctx.user_data["co_addr"]=txt.strip(); ctx.user_data["wf"]=None
        t,_=co_text(ctx.user_data); await u.message.reply_text(t,parse_mode="HTML",reply_markup=co_kb(ctx.user_data))
    elif wf=="co_disc":
        code=txt.strip().upper(); pct=gdisc(code)
        if pct: ctx.user_data.update({"co_disc_code":code,"co_disc_pct":pct}); await u.message.reply_text(f"✅ {code} applied — {int(pct*100)}% off!")
        else: ctx.user_data.update({"co_disc_code":None,"co_disc_pct":0}); await u.message.reply_text("❌ Invalid code.")
        ctx.user_data["wf"]=None; t,_=co_text(ctx.user_data)
        await u.message.reply_text(t,parse_mode="HTML",reply_markup=co_kb(ctx.user_data))
    elif wf=="contact":
        uname=u.effective_user.username or str(uid)
        mid=qxi("INSERT INTO messages(user_id,username,message) VALUES(?,?,?)",(uid,uname,txt))
        await ctx.bot.send_message(ADMIN_ID,f"💬 <b>@{uname}</b>\nMsg <code>{mid}</code>\n\n{hl.escape(txt)}\n\n/reply {mid} &lt;text&gt;",parse_mode="HTML")
        await u.message.reply_text("✅ Message sent! We'll get back to you soon.",reply_markup=menu()); ctx.user_data["wf"]=None
    elif wf=="ann_title":
        ctx.user_data["ann_title"]=txt.strip(); ctx.user_data["wf"]="ann_photo"
        await u.message.reply_text("📸 Send a photo for this announcement (or type <b>skip</b> to post without photo):",parse_mode="HTML")
    elif wf=="ann_photo":
        # text received when expecting photo — check for skip
        if txt.strip().lower()=="skip":
            ctx.user_data["wf"]="ann_body"; await u.message.reply_text("✏️ Enter announcement body:")
        else:
            await u.message.reply_text("📸 Send a photo or type <b>skip</b>:",parse_mode="HTML")
    elif wf=="ann_body":
        title=ctx.user_data.pop("ann_title",""); body=txt; photo=ctx.user_data.pop("ann_photo","")
        qx("INSERT INTO announcements(title,body,photo) VALUES(?,?,?)",(title,body,photo))
        uids=qa("SELECT user_id FROM users"); sent=0
        for r in uids:
            try:
                if photo: await ctx.bot.send_photo(r["user_id"],photo,caption=f"📢 <b>{hl.escape(title)}</b>\n\n{hl.escape(body)}",parse_mode="HTML")
                else: await ctx.bot.send_message(r["user_id"],f"📢 <b>{hl.escape(title)}</b>\n\n{hl.escape(body)}",parse_mode="HTML")
                sent+=1
            except: pass
        await u.message.reply_text(f"✅ Broadcast to {sent} users!"); ctx.user_data["wf"]=None
    elif wf=="review_text":
        oid=ctx.user_data.get("rev_order"); s=ctx.user_data.get("rev_stars",0)
        if not q1("SELECT id FROM orders WHERE id=? AND user_id=? AND status IN ('Paid','Dispatched')",(oid,uid)):
            await u.message.reply_text("⚠️ Not eligible.",reply_markup=menu()); ctx.user_data["wf"]=None; return
        qx("INSERT OR REPLACE INTO reviews(order_id,user_id,stars,text) VALUES(?,?,?,?)",(oid,uid,s,txt))
        await u.message.reply_text(f"✅ Review saved! {STARS.get(s,'')} Thank you 🙏",reply_markup=menu()); ctx.user_data["wf"]=None
    elif wf=="add_title":
        ctx.user_data["nm"]=txt.strip(); ctx.user_data["wf"]="add_desc"
        await u.message.reply_text("📄 Enter product description:")
    elif wf=="add_desc":
        d=ctx.user_data; d["wf"]=None
        qx("INSERT INTO products(name,description,photo,stock,tiers) VALUES(?,?,?,?,?)",(d["nm"],txt.strip(),d["ph"],9999,json.dumps(DEFAULT_TIERS)))
        await ctx.bot.send_photo(u.effective_chat.id,d["ph"],caption=f"✅ <b>{hl.escape(d['nm'])}</b> added!",parse_mode="HTML")
    elif wf=="edit_desc":
        qx("UPDATE products SET description=? WHERE id=?",(txt.strip(),ctx.user_data.get("edit_pid")))
        await u.message.reply_text("✅ Description updated!"); ctx.user_data["wf"]=None
    elif wf=="edit_tiers":
        pid=ctx.user_data.get("tpid"); new=[]; errs=[]
        for i,line in enumerate(txt.strip().splitlines(),1):
            p=line.strip().split(",")
            if len(p)!=2: errs.append(f"Line {i}: need qty,price"); continue
            try: q2,pr=float(p[0]),float(p[1]); assert q2>0 and pr>0; new.append({"qty":q2,"price":pr})
            except: errs.append(f"Line {i}: invalid")
        if errs or not new: await u.message.reply_text("❌ "+"\n".join(errs or ["No valid tiers."])+"\n\nRetry or /cancel."); return
        new.sort(key=lambda t:t["qty"]); qx("UPDATE products SET tiers=? WHERE id=?",(json.dumps(new),pid))
        await u.message.reply_text("✅ <b>Tiers updated!</b>\n\n"+"\n".join(ft(t) for t in new),parse_mode="HTML"); ctx.user_data["wf"]=None
    elif wf=="drop_msg_user":
        oid=ctx.user_data.get("dc_oid"); uname=u.effective_user.username or u.effective_user.first_name or str(uid)
        qx("INSERT INTO drop_chats(order_id,user_id,sender,message) VALUES(?,?,?,?)",(oid,uid,"user",txt))
        o=q1("SELECT items_summary,total_gbp FROM orders WHERE id=?",(oid,))
        info=f"\n📦 {o['items_summary']} | 💷 £{o['total_gbp']:.2f}" if o else ""
        await ctx.bot.send_message(ADMIN_ID,f"💬 <b>Drop Chat <code>{oid}</code></b>{info}\n👤 @{uname}: {hl.escape(txt)}",parse_mode="HTML",reply_markup=dc_admin_kb(oid))
        closed=gs(f"cc_{oid}","0")=="1"
        await u.message.reply_text(f"✅ Sent!\n\n💬 <b>Drop Chat — Order {oid}</b>\n━━━━━━━━━━━━━━━━━━\n\n{fmt_chat(oid)}",parse_mode="HTML",reply_markup=dc_user_kb(oid,closed)); ctx.user_data["wf"]=None
    elif wf=="drop_msg_admin":
        oid=ctx.user_data.get("dc_oid"); row=q1("SELECT user_id FROM orders WHERE id=?",(oid,))
        if not row: await u.message.reply_text("❌ Not found."); ctx.user_data["wf"]=None; return
        qx("INSERT INTO drop_chats(order_id,user_id,sender,message) VALUES(?,?,?,?)",(oid,row["user_id"],"admin",txt))
        closed=gs(f"cc_{oid}","0")=="1"
        try: await ctx.bot.send_message(row["user_id"],f"🏪 <b>Donny's Shop</b>\n━━━━━━━━━━━━━━━━━━\n\n{fmt_chat(oid)}",parse_mode="HTML",reply_markup=dc_user_kb(oid,closed))
        except: pass
        await u.message.reply_text(f"✅ Reply sent for <code>{oid}</code>.",parse_mode="HTML"); ctx.user_data["wf"]=None
    elif wf=="disc_code":
        parts=txt.strip().upper().split(",")
        if len(parts)!=2: await u.message.reply_text("⚠️ Format: CODE,PERCENT e.g. SAVE20,20"); return
        try: code=parts[0].strip(); pct=float(parts[1].strip())/100; assert 0<pct<=1
        except: await u.message.reply_text("⚠️ Invalid. Try again."); return
        qx("INSERT OR REPLACE INTO discount_codes(code,pct,active) VALUES(?,?,1)",(code,pct))
        await u.message.reply_text(f"✅ <code>{code}</code> — {int(pct*100)}% off added!",parse_mode="HTML"); ctx.user_data["wf"]=None
    elif wf=="new_cat":
        parts=txt.strip().split(None,1)
        if len(parts)==2 and len(parts[0])<=2: emoji,name=parts[0],parts[1]
        elif len(parts)==1: emoji,name="🌿",parts[0]
        else: await u.message.reply_text("⚠️ Format: Emoji Name e.g. 🍃 Indoor"); return
        qxi("INSERT INTO categories(name,emoji) VALUES(?,?)",(name,emoji))
        await u.message.reply_text(f"✅ Category {emoji} <b>{hl.escape(name)}</b> created!",parse_mode="HTML"); ctx.user_data["wf"]=None
    elif wf=="order_note":
        oid=ctx.user_data.get("note_oid")
        qx("INSERT OR REPLACE INTO order_notes(order_id,note,updated_at) VALUES(?,?,CURRENT_TIMESTAMP)",(oid,txt.strip()))
        await u.message.reply_text(f"✅ Note saved for <code>{oid}</code>: <i>{hl.escape(txt.strip())}</i>",parse_mode="HTML"); ctx.user_data["wf"]=None
    elif wf=="add_admin":
        if not is_admin(uid): ctx.user_data["wf"]=None; return
        try: new_id=int(txt.strip())
        except: await u.message.reply_text("⚠️ Send a numeric user_id only (e.g. <code>123456789</code>). Use @userinfobot to find it.",parse_mode="HTML"); return
        if q1("SELECT 1 FROM admins WHERE user_id=?",(new_id,)):
            await u.message.reply_text(f"⚠️ <code>{new_id}</code> is already an admin.",parse_mode="HTML"); ctx.user_data["wf"]=None; return
        qx("INSERT OR IGNORE INTO admins(user_id,username) VALUES(?,?)",(new_id,str(new_id)))
        try: info=await ctx.bot.get_chat(new_id); uname=info.username or info.first_name or str(new_id); qx("UPDATE admins SET username=? WHERE user_id=?",(uname,new_id))
        except: uname=str(new_id)
        await u.message.reply_text(f"✅ <b>{hl.escape(uname)}</b> (<code>{new_id}</code>) added as admin.",parse_mode="HTML",reply_markup=menu()); ctx.user_data["wf"]=None
    else:
        await u.message.reply_text("Tap /start to open the menu. 👇",reply_markup=menu())

async def on_photo(u:Update,ctx:ContextTypes.DEFAULT_TYPE):
    wf=ctx.user_data.get("wf"); ph=u.message.photo[-1].file_id
    if wf=="add_photo": ctx.user_data.update({"ph":ph,"wf":"add_title"}); await u.message.reply_text("📝 Enter product title:")
    elif wf=="ann_photo": ctx.user_data.update({"ann_photo":ph,"wf":"ann_body"}); await u.message.reply_text("✏️ Enter announcement body:")

async def cancel_cmd(u:Update,ctx:ContextTypes.DEFAULT_TYPE):
    ctx.user_data["wf"]=None; await u.message.reply_text("🚫 Cancelled.",reply_markup=menu())

async def router(u:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=u.callback_query; d=q.data
    if d.startswith("pick_"):        await pick_weight(u,ctx); return
    if d.startswith("togglehide_"):  await adm_togglehide(u,ctx); return
    await q.answer()
    if   d=="menu":
        txt=f"🌿 <b>Donny's Shop</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n{open_status()}\n🕙 <b>Mon–Sat · Orders close 11am</b>\n\n📦 Tracked · 📍 Local Drop · 🔒 Trusted\n\n👇 <b>What are you looking for?</b>"
        await safe_edit(q,txt,parse_mode="HTML",reply_markup=menu())
    elif d=="products":               await show_products(u,ctx)
    elif d.startswith("cat_assign_"): await adm_cat_assign(u,ctx)
    elif d.startswith("togglecat_"):  await adm_togglecat(u,ctx)
    elif d.startswith("cat_"):        await show_category(u,ctx)
    elif d.startswith("prod_"):       await show_product(u,ctx)
    elif d=="basket":                 await view_basket(u,ctx)
    elif d=="orders":                 await view_orders(u,ctx)
    elif d.startswith("reviews_"):    await show_reviews(u,ctx)
    elif d=="announcements":          await show_announcements(u,ctx)
    elif d=="contact_vendor":         await contact_start(u,ctx)
    elif d.startswith("remove_"):     await remove_item(u,ctx)
    elif d.startswith("paid_"):       await user_paid(u,ctx)
    elif d.startswith("review_"):     await review_start(u,ctx)
    elif d.startswith("stars_"):      await pick_stars(u,ctx)
    elif d=="checkout":               await checkout_start(u,ctx)
    elif d=="co_name":                await co_name_start(u,ctx)
    elif d=="co_addr":                await co_addr_start(u,ctx)
    elif d=="co_addr_skip":           await co_addr_skip(u,ctx)
    elif d=="co_disc":                await co_disc_start(u,ctx)
    elif d.startswith("co_ship_"):    await co_ship(u,ctx)
    elif d=="co_refresh":             await co_refresh(u,ctx)
    elif d=="co_confirm":             await co_confirm(u,ctx)
    elif d.startswith("adm_ok_"):     await adm_confirm(u,ctx)
    elif d.startswith("adm_no_"):     await adm_reject(u,ctx)
    elif d.startswith("adm_go_"):     await adm_dispatch(u,ctx)
    elif d=="adm_msgs":               await adm_msgs(u,ctx)
    elif d=="adm_tiers":              await adm_list_tiers(u,ctx)
    elif d=="adm_rmprod":             await adm_rmprod_list(u,ctx)
    elif d.startswith("rmprod_yes_"): await adm_rmprod_do(u,ctx)
    elif d.startswith("rmprod_"):     await adm_rmprod_confirm(u,ctx)
    elif d=="adm_editdesc":           await adm_editdesc_list(u,ctx)
    elif d.startswith("editdesc_"):   await adm_editdesc_start(u,ctx)
    elif d=="adm_hideprod":           await adm_hideprod_list(u,ctx)
    elif d=="adm_cats":               await adm_cats(u,ctx)
    elif d=="adm_newcat":             await adm_newcat(u,ctx)
    elif d=="adm_delcat":             await adm_delcat_list(u,ctx)
    elif d.startswith("delcat_"):     await adm_delcat_do(u,ctx)
    elif d=="adm_drops":              await adm_drop_overview(u,ctx)
    elif d=="adm_discounts":          await adm_discounts(u,ctx)
    elif d.startswith("toggledisc_"): await adm_toggledisc(u,ctx)
    elif d=="adm_adddisc":            await adm_adddisc_start(u,ctx)
    elif d=="adm_announce":           await ann_start(u,ctx)
    elif d=="adm_addprod":
        if is_admin(u.effective_user.id):
            ctx.user_data["wf"]="add_photo"; await q.message.reply_text("📸 Send the product photo:")
    elif d.startswith("edtier_"):     await adm_show_tiers(u,ctx)
    elif d.startswith("dcv_"):        await dropchat_view(u,ctx)
    elif d.startswith("dch_"):        await dropchat_history(u,ctx)
    elif d.startswith("dcc_"):        await dropchat_close(u,ctx)
    elif d.startswith("dcac_"):       await dropchat_close(u,ctx)
    elif d.startswith("dco_"):        await dropchat_open(u,ctx)
    elif d=="my_ref":                 await my_ref(u,ctx)
    elif d.startswith("adm_note_"):   await adm_note_start(u,ctx)
    elif d=="adm_admins":              await adm_admins(u,ctx)
    elif d=="adm_addadmin":            await adm_addadmin_start(u,ctx)
    elif d.startswith("adm_rmadmin_"): await adm_rmadmin(u,ctx)
    elif d.startswith("dcm_"):        await dropchat_msg_start(u,ctx)
    elif d.startswith("dcr_"):        await dropchat_reply_start(u,ctx)

async def addprod_cmd(u,ctx):
    if not is_admin(u.effective_user.id): return
    ctx.user_data["wf"]="add_photo"; await u.message.reply_text("📸 Send product photo:")

def main():
    init_db(); app=ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler(["start","Start"],start))
    app.add_handler(CommandHandler("admin",admin_panel))
    app.add_handler(CommandHandler("reply",admin_reply_cmd))
    app.add_handler(CommandHandler("order",order_lookup_cmd))
    app.add_handler(CommandHandler("addproduct",addprod_cmd))
    app.add_handler(CommandHandler("cancel",cancel_cmd))
    app.add_handler(CallbackQueryHandler(router))
    app.add_handler(MessageHandler(filters.PHOTO,on_photo))
    app.add_handler(MessageHandler(filters.TEXT&~filters.COMMAND,on_message))
    print("🚀 Bot running..."); app.run_polling()

if __name__=="__main__":
    main()
