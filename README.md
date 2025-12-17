# enviar_video

API FastAPI para autorizar via Meta Graph e publicar videos (Reels/Video/Stories) no Instagram Business. Pensada para deploy simples na Railway.

## Variaveis de ambiente
- `META_APP_ID` e `META_APP_SECRET`: dados do app na Meta.
- `PUBLIC_BASE_URL`: URL publica do deploy (ex: `https://seuapp.up.railway.app`).
- `META_REDIRECT_URI` (opcional): se nao definir, usa `PUBLIC_BASE_URL/oauth/callback`.
- `META_SCOPES` (opcional): escopos solicitados; valor padrao cobre publish.

## Rodar local
```bash
python -m venv .venv
.venv\\Scripts\\activate  # PowerShell
pip install -r requirements.txt
set PUBLIC_BASE_URL=http://localhost:8000
python main.py
# depois abra http://localhost:8000/login
```

## Deploy na Railway
1) Suba o repo para o GitHub (veja comandos abaixo) e crie um novo projeto a partir dele.  
2) Em **Variables**, defina `META_APP_ID`, `META_APP_SECRET`, `PUBLIC_BASE_URL` (seu dominio Railway) e, se preferir, `META_REDIRECT_URI`.  
3) A Railway usa o `Procfile` com `web: uvicorn main:app --host 0.0.0.0 --port ${PORT}`.  
4) Deploy e teste `https://SEU-DOMINIO/health`, depois `https://SEU-DOMINIO/login`.

## Fluxo basico
1) Acesse `/login` para autorizar e salvar tokens em memoria.  
2) Consulte `/status` para ver page_id/ig_user_id.  
3) Publique via `POST /publish` com JSON:
```json
{
  "media_type": "REELS",
  "video_url": "https://.../video.mp4",
  "caption": "opcional",
  "share_to_feed": true,
  "wait": true
}
```

## Comandos git sugeridos
```bash
git init
git add .
git commit -m "first commit"
git branch -M main
git remote add origin https://github.com/Esdrasdw/enviar_video.git
git push -u origin main
```
