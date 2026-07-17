# UNO Live - Flask Multiplayer UNO

UNO Live is a complete browser-based multiplayer UNO game built with Flask, Flask-SocketIO, SQLite, HTML, CSS, and vanilla JavaScript. The Python server owns the deck, hands, turn order, validation, penalties, and winner state; browsers receive only the information their player is allowed to see.

## Features

- 2 to 6 active players per room and up to 12 spectators
- Multiple simultaneous invite-code rooms
- Live lobby, gameplay, turn updates, chat, joins, and disconnect status
- Refresh-safe reconnect tokens stored in browser session storage
- Standard 108-card UNO deck and seven-card opening hands
- Number, Skip, Reverse, Draw Two, Wild, and Wild Draw Four cards
- Legal Wild Draw Four validation
- Draw-then-play or draw-then-pass flow
- Two-player Reverse behavior, draw penalties, and skip behavior
- UNO declaration with a two-card missed-UNO penalty
- Server-side winner detection, table leaderboard, and match history
- SQLite room snapshots, match records, and player statistics
- Player avatars, sound toggle, dark/light themes, responsive mobile UI
- Server-side input validation, message limits, duplicate-name prevention, and private hand projection

## Project Structure

```text
flask-uno-live/
|-- app.py                  # Flask routes and Socket.IO events
|-- game_engine.py          # Deck, rules, turns, penalties, public state
|-- rooms.py                # Thread-safe rooms, identities, reconnects, chat
|-- models.py               # SQLite snapshots, matches, player statistics
|-- requirements.txt
|-- Procfile
|-- render.yaml
|-- .env.example
|-- .python-version
|-- templates/
|   |-- index.html          # App shell and entry screen
|   |-- room.html           # Lobby partial
|   `-- game.html           # Game table partial
|-- static/
|   |-- style.css
|   |-- script.js
|   `-- sounds/
|       `-- README.md       # Web Audio implementation note
|-- database/
|   `-- README.md
|-- tests/
|   |-- test_game_engine.py
|   `-- test_socketio.py
`-- README.md
```

## Architecture

```text
Browser HTML/CSS/JS
        |
        | Socket.IO events and viewer-safe state
        v
Flask-SocketIO event layer
        |
        +-- RoomManager: active rooms and reconnect identities
        +-- game_engine.py: authoritative UNO rules
        `-- SQLite: room snapshots, matches, player stats
```

Each game update is rendered separately for every connected player. Opponent hands never leave the server. A player action is accepted only when the socket owns that seat, the card belongs to that seat, the turn matches, and the move is legal.

## Local Setup

### 1. Install Python

Use Python 3.11 or newer. Python 3.12 is recommended.

### 2. Create a virtual environment

```bash
python -m venv .venv
```

Activate it:

```bash
# macOS or Linux
source .venv/bin/activate

# Windows PowerShell
.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure local environment variables

```bash
cp .env.example .env
```

Generate a secret key and put it in `.env`:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Flask reads shell environment variables directly. To load `.env` automatically during local development, either use `flask` commands or export the values in your shell.

### 5. Start the game

```bash
python app.py
```

Open [http://localhost:5000](http://localhost:5000). The health endpoint is [http://localhost:5000/health](http://localhost:5000/health).

### 6. Test multiplayer locally

1. Open the game in a normal browser window.
2. Create a room and copy its six-character code.
3. Open an incognito window or a different browser.
4. Join with a different display name and the same code.
5. Start the match from the host window.

To test from phones or computers on the same Wi-Fi network, use the host computer's LAN address, such as `http://192.168.1.20:5000`, and allow port `5000` through the local firewall.

## How To Play

1. Enter a display name, choose an avatar, and create or join a room.
2. The host starts after at least two connected players are ready.
3. On your turn, play a highlighted legal card or click the draw pile.
4. After drawing a playable card, play that card or choose **Keep card and pass**.
5. A Wild asks you to choose the next color.
6. Click **UNO** when your hand reaches one card before the next player acts.
7. The first player to empty their hand wins. The host can start the next match.

## Run Tests

```bash
pytest -q
```

The tests cover the 108-card deck, legal play projection, action-card penalties, Wild Draw Four restrictions, missed UNO penalties, private hands, and a two-browser Socket.IO room flow.

## Push To GitHub

1. Create an empty repository on GitHub.
2. From this project directory, run:

```bash
git init
git add .
git commit -m "Build Flask Socket.IO multiplayer UNO"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/flask-uno-live.git
git push -u origin main
```

Do not commit `.env`, `.venv`, SQLite database files, or Python cache files; `.gitignore` already excludes them.

## Deploy To Render

### Blueprint method

1. Push the repository to GitHub.
2. In Render, choose **New > Blueprint**.
3. Select the repository. Render reads `render.yaml`.
4. Deploy the `uno-live-flask` web service.
5. Open `https://YOUR-SERVICE.onrender.com/health` and confirm that `status` is `ok`.
6. Set `ALLOWED_ORIGINS` to the final service origin, for example `https://uno-live-flask.onrender.com`, then redeploy.

### Manual method

Use these service settings:

| Setting | Value |
| --- | --- |
| Runtime | Python |
| Build command | `pip install -r requirements.txt` |
| Start command | `gunicorn --worker-class gthread --threads 100 --workers 1 --timeout 120 --bind 0.0.0.0:$PORT app:app` |
| Health check | `/health` |

Add these environment variables:

| Variable | Value |
| --- | --- |
| `SECRET_KEY` | A generated 64-character random value |
| `ALLOWED_ORIGINS` | Your Render URL |
| `PYTHON_VERSION` | `3.12.4` |

Render's free filesystem is ephemeral. The game remains fully playable, but SQLite history can reset during a redeploy or restart. For durable history, attach a persistent disk at `/var/data` and set `DATABASE_URL=sqlite:////var/data/uno.sqlite3`.

## Deploy To Railway

1. Push the repository to GitHub.
2. In Railway, choose **New Project > Deploy from GitHub repo**.
3. Select the repository. Railway detects Python and the `Procfile`.
4. Add `SECRET_KEY` with a long random value.
5. Add `ALLOWED_ORIGINS=https://YOUR-APP.up.railway.app` after generating a domain.
6. Generate a public domain in **Settings > Networking**.
7. Open `/health` on that domain, then open the root URL and create a room.

For durable SQLite data, create a Railway volume, mount it at `/data`, and add `DATABASE_URL=sqlite:////data/uno.sqlite3`.

## Production Configuration

- Keep `--workers 1` while active rooms are managed in the process. The server still supports many simultaneous rooms through threaded Socket.IO connections.
- SQLite snapshots restore rooms after a restart when the database is on persistent storage.
- For horizontal multi-instance scaling, move active room state to Redis, configure the Flask-SocketIO Redis message queue, and use sticky sessions at the proxy.
- Set `ALLOWED_ORIGINS` to exact HTTPS origins in production. The default `*` exists only to make first deployment straightforward.
- Run behind HTTPS in production. Render and Railway provide TLS on their public domains.
- Invite codes identify rooms; reconnect tokens identify seats. Reconnect tokens are random, long, and never shown in the UI.

## Socket Events

| Client event | Purpose |
| --- | --- |
| `createRoom` / `joinRoom` / `rejoinRoom` | Seat and room lifecycle |
| `startGame` / `playAgain` | Host-only match lifecycle |
| `playCard` / `drawCard` / `passTurn` | Turn actions validated by the server |
| `declareUno` | One-card declaration |
| `chatMessage` | Sanitized room chat |
| `leaveRoom` | Explicit room exit |

The server emits `lobbyState`, private `gameState`, `chatMessage`, `notification`, and `errorMessage` updates.
