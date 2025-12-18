import os
import time
import secrets
from typing import Optional, Dict, Any

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

APP_VERSION = "v24.0"
GRAPH = f"https://graph.facebook.com/{APP_VERSION}"

META_APP_ID = os.getenv("META_APP_ID", "").strip()
META_APP_SECRET = os.getenv("META_APP_SECRET", "").strip()
META_REDIRECT_URI = os.getenv("META_REDIRECT_URI", "").strip()

# Pode ajustar, mas isso costuma ser o minimo para publicar no IG via Graph
META_SCOPES = os.getenv(
    "META_SCOPES",
    "pages_show_list,pages_read_engagement,instagram_basic,instagram_content_publish",
).strip()

# Base publica do app (Railway). Ex: https://seuapp.up.railway.app
PUBLIC_BASE_URL = (
    os.getenv("PUBLIC_BASE_URL", "https://enviarvideo-production.up.railway.app")
    .strip()
    .rstrip("/")
)

# Armazenamento em memoria (para producao real, prefira Postgres/Redis)
STATE_NONCE = secrets.token_urlsafe(24)
TOKENS: Dict[str, Any] = {
    "user_access_token": None,
    "page_access_token": None,
    "ig_user_id": None,
    "page_id": None,
    "token_obtained_at": None,
}

app = FastAPI(title="IG Publisher (Railway)", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ajuste se quiser restringir
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _get_redirect_uri() -> str:
    """
    Usa META_REDIRECT_URI quando setada; senao monta PUBLIC_BASE_URL + /oauth/callback.
    """
    if META_REDIRECT_URI:
        return META_REDIRECT_URI
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}/oauth/callback"
    return ""


def _require_env():
    missing = []
    if not META_APP_ID:
        missing.append("META_APP_ID")
    if not META_APP_SECRET:
        missing.append("META_APP_SECRET")
    if not _get_redirect_uri():
        missing.append("META_REDIRECT_URI ou PUBLIC_BASE_URL")
    if not PUBLIC_BASE_URL:
        missing.append("PUBLIC_BASE_URL")
    if missing:
        raise HTTPException(
            status_code=500,
            detail=f"Faltando variaveis de ambiente: {', '.join(missing)}",
        )


def _req(method: str, url: str, params=None, data=None, timeout=60):
    r = requests.request(method, url, params=params, data=data, timeout=timeout)
    try:
        js = r.json()
    except Exception:
        js = {"_raw": r.text}

    if r.status_code >= 400:
        raise HTTPException(status_code=400, detail={"url": url, "resp": js})
    return js


def _exchange_code_for_user_token(code: str) -> str:
    url = f"{GRAPH}/oauth/access_token"
    params = {
        "client_id": META_APP_ID,
        "client_secret": META_APP_SECRET,
        "redirect_uri": _get_redirect_uri(),
        "code": code,
    }
    js = _req("GET", url, params=params, timeout=60)
    token = js.get("access_token")
    if not token:
        raise HTTPException(status_code=400, detail={"error": "Sem access_token", "resp": js})
    return token


def _get_pages_and_ig(user_token: str):
    url = f"{GRAPH}/me/accounts"
    params = {
        "fields": "name,access_token,tasks,instagram_business_account",
        "access_token": user_token,
    }
    js = _req("GET", url, params=params, timeout=60)
    data = js.get("data", [])
    if not data:
        raise HTTPException(
            status_code=400,
            detail="Nao achei paginas. Verifique permissoes/roles e conexao Pagina<->IG.",
        )
    return data


def _pick_first_valid_page(pages: list):
    """
    Estrategia simples: pega a primeira pagina com instagram_business_account.
    Caso tenha varias, melhore selecionando por nome ou page_id.
    """
    for p in pages:
        ig = (p.get("instagram_business_account") or {}).get("id")
        if ig and p.get("access_token"):
            return p
    raise HTTPException(
        status_code=400,
        detail="Nenhuma pagina retornou instagram_business_account + access_token.",
    )


def _create_container(
    ig_user_id: str,
    page_token: str,
    media_type: str,
    video_url: str,
    caption: str = "",
    share_to_feed: bool = True,
) -> str:
    url = f"{GRAPH}/{ig_user_id}/media"
    data = {
        "media_type": media_type,  # "VIDEO" | "REELS" | "STORIES"
        "video_url": video_url,
        "access_token": page_token,
    }
    if caption:
        data["caption"] = caption

    if media_type == "REELS":
        data["share_to_feed"] = "true" if share_to_feed else "false"

    js = _req("POST", url, data=data, timeout=60)
    cid = js.get("id")
    if not cid:
        raise HTTPException(status_code=400, detail={"error": "Sem container id", "resp": js})
    return cid


def _wait_container(container_id: str, page_token: str, timeout_sec: int = 20 * 60, poll_sec: int = 5):
    url = f"{GRAPH}/{container_id}"
    params = {"fields": "status_code,status", "access_token": page_token}
    t0 = time.time()
    while True:
        js = _req("GET", url, params=params, timeout=60)
        status_code = js.get("status_code")
        if status_code == "FINISHED":
            return
        if status_code in ("ERROR", "EXPIRED"):
            raise HTTPException(status_code=400, detail={"error": "Processamento falhou", "resp": js})
        if time.time() - t0 > timeout_sec:
            raise HTTPException(status_code=408, detail="Timeout esperando processamento do video.")
        time.sleep(poll_sec)


def _publish_container(ig_user_id: str, page_token: str, container_id: str) -> str:
    url = f"{GRAPH}/{ig_user_id}/media_publish"
    data = {"creation_id": container_id, "access_token": page_token}
    js = _req("POST", url, data=data, timeout=60)
    mid = js.get("id")
    if not mid:
        raise HTTPException(status_code=400, detail={"error": "Sem media id", "resp": js})
    return mid


@app.get("/health")
def health():
    return {"ok": True, "has_token": bool(TOKENS["user_access_token"])}


@app.get("/")
def home():
    _require_env()
    html = f"""
    <h2>IG Publisher (Railway)</h2>
    <ul>
      <li><a href="/login">/login</a> (autorizar e salvar token)</li>
      <li><a href="/status">/status</a> (ver tokens/ids)</li>
    </ul>
    <p>Depois de logar, use POST /publish com JSON.</p>
    """
    return HTMLResponse(html)


@app.get("/login")
def login():
    _require_env()
    redirect_uri = _get_redirect_uri()
    auth_url = (
        "https://www.facebook.com/dialog/oauth"
        f"?client_id={META_APP_ID}"
        f"&redirect_uri={requests.utils.quote(redirect_uri, safe='')}"
        f"&state={STATE_NONCE}"
        f"&scope={requests.utils.quote(META_SCOPES, safe='')}"
        f"&response_type=code"
    )
    return RedirectResponse(auth_url)


@app.get("/oauth/callback")
def oauth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
):
    _require_env()

    if error:
        return JSONResponse({"error": error, "description": error_description}, status_code=400)

    if not code:
        raise HTTPException(status_code=400, detail="Callback sem 'code'.")

    if state != STATE_NONCE:
        raise HTTPException(status_code=400, detail="State invalido (possivel CSRF).")

    user_token = _exchange_code_for_user_token(code)

    pages = _get_pages_and_ig(user_token)
    chosen = _pick_first_valid_page(pages)

    TOKENS["user_access_token"] = user_token
    TOKENS["page_access_token"] = chosen.get("access_token")
    TOKENS["page_id"] = chosen.get("id")
    TOKENS["ig_user_id"] = (chosen.get("instagram_business_account") or {}).get("id")
    TOKENS["token_obtained_at"] = int(time.time())

    html = f"""
    <h3>Foi. Autorizado!</h3>
    <p>Page ID: {TOKENS["page_id"]}</p>
    <p>IG User ID: {TOKENS["ig_user_id"]}</p>
    <p><a href="/status">Ver status</a></p>
    """
    return HTMLResponse(html)


@app.get("/status")
def status():
    return {
        "has_user_token": bool(TOKENS["user_access_token"]),
        "has_page_token": bool(TOKENS["page_access_token"]),
        "page_id": TOKENS["page_id"],
        "ig_user_id": TOKENS["ig_user_id"],
        "token_obtained_at": TOKENS["token_obtained_at"],
        "scopes": META_SCOPES,
        "redirect_uri": _get_redirect_uri(),
        "public_base_url": PUBLIC_BASE_URL,
    }


@app.post("/publish")
async def publish(payload: Dict[str, Any]):
    """
    JSON esperado:
    {
      "media_type": "REELS" | "VIDEO" | "STORIES",
      "video_url": "https://.../video.mp4",
      "caption": "opcional",
      "share_to_feed": true/false (so para REELS),
      "wait": true/false (default true)
    }
    """
    if not TOKENS["page_access_token"] or not TOKENS["ig_user_id"]:
        raise HTTPException(status_code=401, detail="Sem tokens. Acesse /login primeiro.")

    media_type = (payload.get("media_type") or "REELS").upper().strip()
    video_url = (payload.get("video_url") or "").strip()
    caption = (payload.get("caption") or "").strip()
    share_to_feed = bool(payload.get("share_to_feed", True))
    do_wait = bool(payload.get("wait", True))

    if media_type not in ("REELS", "VIDEO", "STORIES"):
        raise HTTPException(status_code=400, detail="media_type invalido. Use REELS, VIDEO ou STORIES.")
    if not video_url.startswith("https://"):
        raise HTTPException(status_code=400, detail="video_url precisa ser https e publico (Meta tem que acessar).")

    container_id = _create_container(
        ig_user_id=TOKENS["ig_user_id"],
        page_token=TOKENS["page_access_token"],
        media_type=media_type,
        video_url=video_url,
        caption=caption,
        share_to_feed=share_to_feed,
    )

    if do_wait:
        _wait_container(container_id, TOKENS["page_access_token"])

    media_id = _publish_container(TOKENS["ig_user_id"], TOKENS["page_access_token"], container_id)

    return {
        "ok": True,
        "media_type": media_type,
        "container_id": container_id,
        "media_id": media_id,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=False,
    )
