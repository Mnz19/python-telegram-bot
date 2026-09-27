# Bot de Alertas de Passagens Aéreas

Bot de Telegram que monitora preços de passagens aéreas (via Sky Scrapper,
dados do Skyscanner acessados pela RapidAPI) e avisa o usuário quando o preço
cai abaixo de um alvo configurado ou sofre uma queda percentual em relação ao
menor preço já visto.

## Stack

- Python 3.12, Django 5.x
- `python-telegram-bot` v21 (polling, sem webhook)
- Celery + Redis + `django-celery-beat` para a checagem periódica de preços
- Sky Scrapper (Skyscanner data via RapidAPI) por padrão como fonte de preços — trocável para Amadeus Flight Offers Search via `FLIGHT_PROVIDER=amadeus`
- SQLite em desenvolvimento, Postgres em produção
- pytest + pytest-django para os testes

## Setup local (sem Docker, SQLite)

Pré-requisitos: Python 3.12.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements-dev.txt

cp .env.example .env
# edite .env e preencha TELEGRAM_BOT_TOKEN e RAPIDAPI_KEY
```

`TELEGRAM_BOT_TOKEN` vem do @BotFather no Telegram (`/newbot`). `RAPIDAPI_KEY`
vem de uma conta gratuita em rapidapi.com (login com Google/GitHub) com o app
"Sky Scrapper" assinado no plano free — sem revisão manual, ao contrário do
Amadeus. Veja `FLIGHT_PROVIDER` no `.env.example` para usar Amadeus no lugar,
se preferir (`AMADEUS_CLIENT_ID`/`AMADEUS_CLIENT_SECRET` de developers.amadeus.com).

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
chamadas de rede reais — todas as respostas dos provedores de preço (Sky
Scrapper e Amadeus) são mockadas com `respx`.

## Comandos do bot

- `/start` — apresentação e lista de comandos
- `/novaalerta` — cria um alerta: origem, destino (ou "/pular" para multi-destino
  — vários destinos populares de uma vez), tipo de período (data fixa, faixa de
  datas, próximos N meses, ou dia da semana flexível dentro de um intervalo),
  ida e volta ou só ida, preço-alvo/queda percentual (opcional — sem nenhum
  dos dois, o alerta ainda avisa em novo menor preço histórico ou possível
  erro de tarifa), companhia aérea preferida, bagagem despachada e um nome
  opcional para reutilizar depois
- `/listaralertas` — lista os alertas ativos com status, período, gatilho e último preço
- `/pararalerta <id>` ou `/pausar <id>` — pausa um alerta
- `/retomar <id>` — reativa um alerta pausado
- `/precoatual <id>` — força uma checagem imediata de preço (limitada a 1 a cada 5 min por alerta)
- `/perfis` — lista os alertas nomeados (reutilizáveis)
- `/duplicar <id>` — cria um novo alerta com a mesma configuração de um existente

O bot funciona em grupos do Telegram também: como o dono de um alerta é o
`chat_id` de onde ele foi criado, um alerta criado num grupo pertence ao
grupo — qualquer membro pode listá-lo, pausá-lo ou duplicá-lo, e a notificação
chega para todos.

## Funcionalidades de detecção de preço

Além do preço-alvo e da porcentagem de queda configurados manualmente, todo
alerta automaticamente:

- avisa quando o preço atual é o **menor já registrado** para aquele alerta,
  caso nenhum preço-alvo/queda tenha sido definido;
- avisa (com destaque de "🚨 possível erro de tarifa") quando o preço cai para
  50% ou menos da média histórica do trecho, com pelo menos 3 checagens
  anteriores — independente do threshold configurado;
- envia um gráfico (PNG) da evolução do preço junto da notificação, quando já
  há pelo menos 2 pontos de histórico;
- em alertas **multi-destino** ("me surpreenda") e de **dia da semana
  flexível**, varre várias combinações de data/destino por checagem (limitado
  a 20 combinações) e guarda no histórico qual combinação rendeu o menor preço.

**Companhia aérea e bagagem:** a preferência de companhia aérea é filtrada
com os dois provedores. Já a exigência de bagagem despachada
(`require_checked_bag`) só funciona de fato com `FLIGHT_PROVIDER=amadeus` —
o Sky Scrapper é meta-busca (dados agregados do Skyscanner) e não expõe
regras de bagagem por tarifa, então esse filtro é ignorado (com aviso no log)
quando o provedor é `skyscanner`.

Toda segunda-feira às 8h (horário configurado em `DJANGO_TIME_ZONE`), o bot
envia um resumo com o menor preço da semana de cada alerta ativo — mesmo sem
nenhum gatilho ter disparado (task `send_weekly_summaries`, agendada via
django-celery-beat na migration inicial).

Para poupar chamadas de API, `check_prices` não roda para todos os alertas a
cada execução: alertas com data de embarque em até 30 dias são checados a
cada execução, entre 31 e 90 dias a cada 4h, e além disso 1x por dia
(`alert_engine.should_check_now`).

## Deploy com Docker Compose (produção, Postgres)

Pré-requisitos: Docker e Docker Compose.

```bash
cp .env.example .env
# preencha TELEGRAM_BOT_TOKEN, RAPIDAPI_KEY (ou as credenciais Amadeus, se
# FLIGHT_PROVIDER=amadeus), DJANGO_SECRET_KEY e DJANGO_ALLOWED_HOSTS
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
  services/        skyscanner_client.py, amadeus_client.py, flight_client.py (escolhe o
                   provedor), alert_engine.py, chart.py, constants.py
  bot/             comandos (alerts.py, profiles.py, start.py) e ConversationHandler do bot
  management/commands/runbot.py   sobe o bot via polling
  tasks.py         tasks Celery (check_prices / check_single_alert)
tests/             suite pytest (mocka toda chamada de rede)
```

## Cuidados com SQLite vs Postgres

- Nenhum `ArrayField` é usado (só existe em Postgres); listas usam `JSONField`,
  que funciona nos dois bancos.
- As migrations foram testadas em SQLite (dev/test); antes de qualquer mudança
  de schema, rode `migrate` localmente e confirme que também aplica limpo
  contra Postgres (`docker compose run --rm migrate`).
