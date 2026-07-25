# Steam Auto Follow

Automação **local** para seguir curadores/publishers, entrar em grupos e adicionar apps à wishlist + follow na Steam.

## O que faz

Você cola URLs da Steam no dashboard. O sistema detecta o tipo pela URL e executa no Chromium (visível):

| URL | Ação |
|---|---|
| `/curator/` ou `/publisher/` | Seguir |
| `/app/` | Lista de desejos + seguir |
| `/groups/` | Entrar no grupo |

Usa cookies **separados** da Store e da Community (`steamLoginSecure` + `sessionid` em cada uma), criptografados em `data/app.db`. Roda só em `127.0.0.1`.

A chave `COOKIE_ENCRYPTION_KEY` fica no `.env` e também em `data/.cookie_key`, para os cookies sobreviverem a reinícios.

## Como rodar

```bash
python -m venv .venv
```

Windows: `.venv\Scripts\activate`  
Linux/macOS: `source .venv/bin/activate`

```bash
pip install -r requirements.txt
playwright install chromium
copy .env.example .env
```

Defina `COOKIE_ENCRYPTION_KEY` no `.env`, depois:

```bash
python run.py
```

Abra: http://127.0.0.1:8000

1. Salve os cookies da **Store** e da **Community** (pares diferentes)  
2. Cole as URLs  
3. Acompanhe a fila no dashboard (atualiza a cada ~1s)  

## Aviso

Uso pessoal/local. Não contorna CAPTCHA, Steam Guard nem bloqueios da Steam.

Se aparecer **Ops! / solicitações demais**, a fila pausa sozinha e entra em cooldown. Aguarde no Chromium e clique em **Retomar fila**.

Prevenção incluída: intervalo + jitter, pausas humanas, limite/hora, backoff adaptativo, cookies/auth em cache.