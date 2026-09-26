# Bot de Alertas de Passagens Aéreas

Bot de Telegram que monitora preços de passagens aéreas (via Amadeus for
Developers) e avisa o usuário quando o preço cai abaixo de um alvo configurado
ou sofre uma queda percentual em relação ao menor preço já visto.

## Stack

- Python 3.12, Django 5.x
- `python-telegram-bot` v21 (polling, sem webhook)
- Celery + Redis + `django-celery-beat` para a checagem periódica de preços
- Amadeus Flight Offers Search API
- SQLite em desenvolvimento, Postgres em produção
- pytest + pytest-django para os testes

## Setup local (sem Docker, SQLite)

Pré-requisitos: Python 3.12.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements-dev.txt

cp .env.example .env
# edite .env e preencha TELEGRAM_BOT_TOKEN, AMADEUS_CLIENT_ID e AMADEUS_CLIENT_SECRET
```

`DJANGO_ENV` não precisa ser definido para desenvolvimento — o padrão já é
`dev`, que usa SQLite (`dev.db` na raiz do projeto) e cache em memória, sem
precisar de Postgres ou Redis rodando.

Aplique as migrations e crie um superusuário para acessar o `/admin`:

```bash
python manage.py migrate
python manage.py createsuperuser
```

Rode o bot (polling):

```bash
python manage.py runbot
```

Em outro terminal, se quiser testar o admin do Django:

```bash
python manage.py runserver
```

Sem Redis configurado, as tasks do Celery não têm como ser despachadas em
background — para desenvolvimento, use o comando `/precoatual <id>` do bot
para forçar uma checagem imediata sem depender do Celery. Se quiser rodar o
Celery localmente também, suba um Redis (`docker run -p 6379:6379 redis:7-alpine`)
e então, em terminais separados:

```bash
celery -A config worker --loglevel=info
celery -A config beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

## Rodando os testes

```bash
pytest
```

Os testes usam SQLite em memória (`config/settings/test.py`) e nunca fazem
chamadas de rede reais — todas as respostas da API da Amadeus são mockadas
com `respx`.

## Comandos do bot

- `/start` — apresentação e lista de comandos
- `/novaalerta` — cria um alerta (origem, destino, período, preço-alvo ou % de queda)
- `/listaralertas` — lista alertas ativos e o último preço registrado
- `/pararalerta <id>` — desativa um alerta
- `/precoatual <id>` — força uma checagem imediata de preço (limitada a 1 a cada 5 min por alerta)

## Deploy com Docker Compose (produção, Postgres)

Pré-requisitos: Docker e Docker Compose.

```bash
cp .env.example .env
# preencha TELEGRAM_BOT_TOKEN, AMADEUS_CLIENT_ID, AMADEUS_CLIENT_SECRET,
# DJANGO_SECRET_KEY e DJANGO_ALLOWED_HOSTS
```

```bash
docker compose up --build -d
```

Isso sobe `postgres`, `redis`, roda as migrations (`migrate`, um serviço que
termina após aplicar as migrations) e então inicia `bot`, `worker` e `beat`.

Para criar um superusuário no banco em produção:

```bash
docker compose exec bot python manage.py createsuperuser
```

O intervalo da checagem periódica de preços é ajustável depois do deploy
direto pelo `/admin` (django-celery-beat → Periodic tasks), sem precisar de
redeploy — o valor de `PRICE_CHECK_INTERVAL_MINUTES` no `.env` só define o
intervalo inicial, semeado na primeira migration.

Um `docker-compose.override.yml` já é carregado automaticamente pelo
`docker compose up` e monta o código-fonte como volume nos serviços `bot`,
`worker` e `beat`, para facilitar iteração sem rebuild de imagem (ainda assim
é preciso reiniciar o serviço afetado — não há autoreload configurado).

## Estrutura do projeto

```
config/            projeto Django (settings/, celery.py, urls.py)
alerts/            app principal
  models.py        TelegramUser, Alert, PriceHistory
  admin.py         registro dos models no Django admin
  services/        amadeus_client.py, alert_engine.py
  bot/             comandos e ConversationHandler do bot
  management/commands/runbot.py   sobe o bot via polling
  tasks.py         tasks Celery (check_prices / check_single_alert)
tests/             suite pytest (mocka toda chamada à Amadeus)
```

## Cuidados com SQLite vs Postgres

- Nenhum `ArrayField` é usado (só existe em Postgres); listas usam `JSONField`,
  que funciona nos dois bancos.
- As migrations foram testadas em SQLite (dev/test); antes de qualquer mudança
  de schema, rode `migrate` localmente e confirme que também aplica limpo
  contra Postgres (`docker compose run --rm migrate`).
