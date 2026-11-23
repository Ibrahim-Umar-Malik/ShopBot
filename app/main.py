"""FastAPI app: pages + streaming chat API. Identity comes from the signed session cookie, never from the chat."""
import json
import logging
import secrets
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from app import config, db
from app.agent.context import ShopContext
from app.agent.graph import build_graph

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("shopbot")

TOOL_STATUS = {
    "search_products": "Searching the catalog…",
    "get_product": "Looking at the details…",
    "check_stock": "Checking stock…",
    "suggest_addons": "Finding something to go with it…",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    config.CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(config.CHECKPOINT_DB)) as saver:
        app.state.graph = build_graph(checkpointer=saver)
        yield


app = FastAPI(title="ShopBot", lifespan=lifespan)
if not config.SESSION_SECRET:
    log.warning("SESSION_SECRET is not set; using a random one (sessions reset on restart)")
app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET or secrets.token_hex(32),
                   same_site="lax", max_age=60 * 60 * 24 * 30)
app.mount("/static", StaticFiles(directory=config.ROOT / "app" / "static"), name="static")
templates = Jinja2Templates(directory=config.ROOT / "app" / "templates")
templates.env.filters["money"] = lambda n: f"Rs {n:,}"
SWATCHES = {"black": "#2b2b2b", "white": "#efe9e1", "ivory": "#efe6d2", "gold": "#c9a24a", "silver": "#b9bcc2",
            "turquoise": "#3fb3a8", "peach": "#f2b49b", "mint": "#9fd8bf", "pink": "#e9a1b5", "maroon": "#7b2335",
            "emerald": "#1f7a5a", "mustard": "#d4a52c", "navy": "#26375f", "beige": "#d8c7a8", "tan": "#b98b5e",
            "grey": "#8d9096", "green": "#3f8f5a", "olive": "#6f7444", "blue": "#3b6fc2", "brown": "#7a5236"}
templates.env.globals["swatch"] = lambda colors: SWATCHES.get((colors or ["grey"])[0], "#a9a29a")


# ---------- identity ----------

def customer_id(request: Request) -> str:
    if "uid" not in request.session:
        request.session["uid"] = uuid.uuid4().hex
    return request.session["uid"]


def thread_id(request: Request, store: dict, new: bool = False) -> str:
    """Namespaced by store and customer, and built only from the session, so nobody can resume another's thread."""
    uid = customer_id(request)
    convs = dict(request.session.get("convs", {}))
    if new or store["slug"] not in convs:
        convs[store["slug"]] = uuid.uuid4().hex[:12]
        request.session["convs"] = convs
    return f"{store['id']}:{uid}:{convs[store['slug']]}"


def load_store(slug: str) -> dict:
    with db.connect() as conn:
        store = db.get_store_by_slug(conn, slug)
    if not store:
        raise HTTPException(404, "Store not found")
    return store


# ---------- pages ----------

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    with db.connect() as conn:
        stores = db.list_stores(conn)
    return templates.TemplateResponse(request, "index.html", {"stores": stores})


@app.get("/s/{slug}", response_class=HTMLResponse)
def store_page(request: Request, slug: str):
    store = load_store(slug)
    with db.connect() as conn:
        products = db.list_products(conn, store["id"])
    customer_id(request)
    return templates.TemplateResponse(request, "store.html", {"store": store, "products": products})


@app.get("/healthz")
def health():
    return {"ok": True}


# ---------- chat API ----------

class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=config.MAX_MESSAGE_CHARS)


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.get("/api/s/{slug}/history")
async def history(request: Request, slug: str):
    store = load_store(slug)
    cfg = {"configurable": {"thread_id": thread_id(request, store)}}
    snapshot = await request.app.state.graph.aget_state(cfg)
    out = []
    for m in snapshot.values.get("messages", []) if snapshot else []:
        if isinstance(m, HumanMessage):
            out.append({"role": "user", "text": m.text})
        elif isinstance(m, AIMessage) and not m.tool_calls and m.text:
            out.append({"role": "assistant", "text": m.text})
    return {"messages": out}


@app.post("/api/s/{slug}/new")
def new_chat(request: Request, slug: str):
    thread_id(request, load_store(slug), new=True)
    return {"ok": True}


@app.post("/api/s/{slug}/chat")
async def chat(request: Request, slug: str, body: ChatIn):
    store = load_store(slug)
    ctx = ShopContext(store_id=store["id"], store_name=store["name"], user_id=customer_id(request),
                      currency=store["currency"])
    cfg = {"configurable": {"thread_id": thread_id(request, store)}, "recursion_limit": 25}
    graph = request.app.state.graph

    async def events():
        try:
            async for ns, mode, chunk in graph.astream(
                    {"messages": [HumanMessage(body.message.strip())]}, cfg, context=ctx,
                    stream_mode=["messages", "updates"], subgraphs=True):
                in_sales = bool(ns) and ns[0].startswith("sales_agent")
                if mode == "messages":
                    msg, meta = chunk
                    if in_sales and meta.get("langgraph_node") == "model" and isinstance(msg, AIMessageChunk):
                        if msg.text:
                            yield sse("token", {"text": msg.text})
                    continue
                for node, update in chunk.items():
                    msgs = (update or {}).get("messages", []) if isinstance(update, dict) else []
                    if not ns and node == "guard" and msgs:
                        yield sse("token", {"text": msgs[-1].text})
                        yield sse("blocked", {"reason": update.get("guard_verdict")})
                    elif in_sales and node == "model":
                        for m in msgs:
                            for call in getattr(m, "tool_calls", []):
                                yield sse("status", {"text": TOOL_STATUS.get(call["name"], "Working…")})
                    elif in_sales and node == "tools":
                        for m in msgs:
                            if isinstance(m, ToolMessage) and m.artifact:
                                yield sse("products", {"items": m.artifact})
        except Exception as e:   # e.g. rate limit or spend limit reached
            log.exception("chat failed")
            text = "Sorry, I'm having trouble right now. Please try again in a moment."
            if "credit" in str(e).lower() or "limit" in str(e).lower():
                text = "Sorry, the shop assistant is resting right now (usage limit reached). Please try later."
            yield sse("error", {"text": text})
        yield sse("done", {})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
