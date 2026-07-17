"use strict";

const byId = (id) => document.getElementById(id);

const views = {
  welcome: byId("welcomeView"),
  lobby: byId("lobbyView"),
  game: byId("gameView"),
};

const state = {
  socket: null,
  roomCode: sessionStorage.getItem("uno.roomCode") || "",
  playerId: sessionStorage.getItem("uno.playerId") || "",
  username: sessionStorage.getItem("uno.username") || "",
  avatar: sessionStorage.getItem("uno.avatar") || "ember",
  isHost: false,
  game: null,
  selectedWildCardId: null,
  previousTurnId: null,
  messageIds: new Set(),
  soundEnabled: localStorage.getItem("uno.sound") !== "off",
  busy: false,
  actionPending: false,
  countdownTimer: null,
  previousWinnerId: null,
};

const invitedRoomCode = document.body.dataset.initialRoom || "";
if (invitedRoomCode && state.roomCode && invitedRoomCode !== state.roomCode) {
  sessionStorage.removeItem("uno.roomCode");
  sessionStorage.removeItem("uno.playerId");
  state.roomCode = "";
  state.playerId = "";
}

const CARD_LABELS = {
  skip: "SKIP",
  reverse: "REV",
  draw2: "+2",
  wild: "WILD",
  wild4: "+4",
};

const COLOR_HEX = {
  red: "#ea3348",
  yellow: "#f5c842",
  green: "#18a866",
  blue: "#2879e9",
};

function showView(name) {
  Object.values(views).forEach((view) => view.classList.add("hidden"));
  views[name].classList.remove("hidden");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function persistSession() {
  sessionStorage.setItem("uno.roomCode", state.roomCode);
  sessionStorage.setItem("uno.playerId", state.playerId);
  sessionStorage.setItem("uno.username", state.username);
  sessionStorage.setItem("uno.avatar", state.avatar);
}

function clearSession() {
  ["uno.roomCode", "uno.playerId", "uno.username", "uno.avatar"].forEach((key) => {
    sessionStorage.removeItem(key);
  });
  state.roomCode = "";
  state.playerId = "";
  state.game = null;
  state.isHost = false;
  state.messageIds.clear();
}

function cleanDisplayName(value) {
  return String(value || "")
    .replace(/[^A-Za-z0-9 _-]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 18);
}

function setBusy(busy) {
  state.busy = busy;
  byId("createRoomBtn").disabled = busy;
  byId("joinRoomBtn").disabled = busy;
  byId("welcomeStatus").textContent = busy
    ? "Connecting to the table..."
    : "Your hand stays private. Every move is checked by the server.";
}

let toastTimer;
function toast(message, error = false) {
  const element = byId("toast");
  element.textContent = message;
  element.classList.toggle("error", error);
  element.classList.remove("hidden");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => element.classList.add("hidden"), 3000);
  playSound(error ? "error" : "notice");
}

function playSound(type) {
  if (!state.soundEnabled) return;
  try {
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    const context = new AudioContext();
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.type = "sine";
    oscillator.frequency.value = { turn: 620, play: 440, win: 760, error: 170, notice: 350 }[type] || 350;
    gain.gain.setValueAtTime(0.04, context.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, context.currentTime + 0.12);
    oscillator.connect(gain);
    gain.connect(context.destination);
    oscillator.start();
    oscillator.stop(context.currentTime + 0.12);
    oscillator.addEventListener("ended", () => context.close());
  } catch (_) {
    // Audio is optional and may be blocked before the first user gesture.
  }
}

function initials(username) {
  return String(username || "U")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word[0])
    .join("")
    .toUpperCase();
}

function makeAvatar(player) {
  const avatar = document.createElement("span");
  avatar.className = `avatar ${player.avatar || "ember"}`;
  avatar.textContent = initials(player.username);
  avatar.setAttribute("aria-hidden", "true");
  return avatar;
}

function makeBadge(className, text) {
  const badge = document.createElement("span");
  badge.className = className;
  badge.textContent = text;
  return badge;
}

function renderLobbyPlayer(player) {
  const card = document.createElement("article");
  card.className = `lobby-player${player.connected ? "" : " offline"}`;
  card.appendChild(makeAvatar(player));

  const details = document.createElement("div");
  const name = document.createElement("span");
  name.className = "player-name";
  name.textContent = player.username;
  if (player.isHost) name.appendChild(makeBadge("host-badge", "Host"));
  details.appendChild(name);

  const note = document.createElement("span");
  note.className = "player-note";
  note.textContent = player.spectator ? "Spectator" : player.connected ? "Ready" : "Reconnecting";
  details.appendChild(note);
  card.appendChild(details);
  return card;
}

function renderLobby(room) {
  state.actionPending = false;
  window.clearInterval(state.countdownTimer);
  state.roomCode = room.code;
  state.isHost = room.hostId === state.playerId;
  persistSession();
  showView("lobby");
  document.title = `Room ${room.code} | UNO Live`;

  byId("lobbyRoomCode").textContent = room.code;
  const active = room.players.filter((player) => !player.spectator);
  const connected = active.filter((player) => player.connected);
  byId("lobbyPlayerCount").textContent = String(active.length);
  byId("lobbyCapacity").textContent = `${active.length} / 6`;

  document.querySelectorAll(".mode-option").forEach((button) => {
    button.classList.toggle("selected", button.dataset.mode === room.mode);
    button.disabled = !state.isHost;
  });
  byId("modeLockHint").textContent = state.isHost
    ? "Choose before starting"
    : `${room.mode === "wild" ? "Wild stacking" : "Classic"} selected by host`;

  const list = byId("lobbyPlayers");
  list.replaceChildren(...room.players.map(renderLobbyPlayer));

  const start = byId("startGameBtn");
  start.disabled = !state.isHost || connected.length < 2;
  byId("lobbyMessage").textContent = state.isHost
    ? connected.length >= 2
      ? "Everyone is connected. Start when the table is ready."
      : "At least one more connected player is needed."
    : "Waiting for the host to deal the first hand.";
  renderChat(room.chat || []);
}

function makeGamePlayer(player, currentPlayerId) {
  const row = document.createElement("div");
  row.className = `game-player${player.id === currentPlayerId ? " current" : ""}${player.connected ? "" : " offline"}`;
  row.appendChild(makeAvatar(player));

  const details = document.createElement("div");
  const name = document.createElement("span");
  name.className = "player-name";
  name.textContent = player.username;
  if (player.isHost) name.appendChild(makeBadge("host-badge", "Host"));
  if (player.id === currentPlayerId) name.appendChild(makeBadge("turn-badge", "Turn"));
  if (player.saidUno) name.appendChild(makeBadge("uno-badge", "UNO"));
  details.appendChild(name);

  const note = document.createElement("span");
  note.className = "player-note";
  note.textContent = player.spectator ? "Spectating" : player.connected ? "At table" : "Offline";
  details.appendChild(note);
  row.appendChild(details);

  const count = document.createElement("span");
  count.className = "card-count";
  count.textContent = player.spectator ? "-" : String(player.cardCount);
  count.title = player.spectator ? "Spectator" : `${player.cardCount} cards`;
  row.appendChild(count);
  return row;
}

function makeOpponentChip(player, currentPlayerId) {
  const chip = document.createElement("div");
  chip.className = `opponent-chip${player.id === currentPlayerId ? " current" : ""}`;
  chip.appendChild(makeAvatar(player));
  const detail = document.createElement("div");
  const name = document.createElement("span");
  name.className = "player-name";
  name.textContent = player.username;
  const count = document.createElement("span");
  count.className = "player-note";
  count.textContent = `${player.cardCount} card${player.cardCount === 1 ? "" : "s"}`;
  detail.append(name, count);
  chip.appendChild(detail);
  return chip;
}

function cardLabel(card) {
  return CARD_LABELS[card.value] || card.value;
}

function makeUnoCard(card, playable = false, interactive = false) {
  const element = document.createElement(interactive ? "button" : "div");
  if (interactive) element.type = "button";
  element.className = `uno-card ${card.color === "wild" ? "wild" : card.color}`;
  if (interactive) {
    element.classList.add(playable ? "playable" : "blocked");
    element.disabled = !playable;
    element.dataset.cardId = card.id;
    element.setAttribute("aria-label", `${card.color} ${cardLabel(card)}${playable ? ", playable" : ""}`);
  }

  const top = document.createElement("span");
  top.className = "uno-card-corner";
  top.textContent = cardLabel(card);
  const value = document.createElement("span");
  value.className = "uno-card-value";
  value.textContent = cardLabel(card);
  const bottom = document.createElement("span");
  bottom.className = "uno-card-corner bottom";
  bottom.textContent = cardLabel(card);
  element.append(top, value, bottom);
  return element;
}

function renderLeaderboard(rows) {
  const list = byId("leaderboard");
  if (!rows.length) {
    list.replaceChildren(emptyState("No wins at this table yet."));
    return;
  }
  list.replaceChildren(
    ...rows.map((row, index) => {
      const line = document.createElement("div");
      line.className = "leader-row";
      const rank = document.createElement("span");
      rank.className = "leader-rank";
      rank.textContent = String(index + 1);
      const name = document.createElement("span");
      name.textContent = row.username;
      const score = document.createElement("strong");
      score.textContent = `${row.points || 0} pts · ${row.wins}W`;
      line.append(rank, name, score);
      return line;
    }),
  );
}

function renderMatchHistory(rows) {
  const list = byId("matchHistory");
  if (!rows.length) {
    list.replaceChildren(emptyState("Completed matches appear here."));
    return;
  }
  list.replaceChildren(
    ...rows.map((row) => {
      const line = document.createElement("div");
      line.className = "history-row";
      line.textContent = `${row.winner} earned ${row.points || 0} points in ${row.moves} moves`;
      return line;
    }),
  );
}

function renderEvents(events) {
  const list = byId("eventList");
  const latest = events.slice(-7).reverse();
  if (!latest.length) {
    list.replaceChildren(emptyState("The move log is ready."));
    return;
  }
  list.replaceChildren(
    ...latest.map((message) => {
      const row = document.createElement("div");
      row.className = "event-row";
      row.textContent = message;
      return row;
    }),
  );
}

function emptyState(text) {
  const element = document.createElement("p");
  element.className = "empty-state";
  element.textContent = text;
  return element;
}

function renderRoundStandings(rows) {
  byId("roundStandings").replaceChildren(
    ...rows.map((row, index) => {
      const line = document.createElement("div");
      line.className = "round-standing-row";
      const rank = document.createElement("span");
      rank.textContent = String(index + 1);
      const name = document.createElement("strong");
      name.textContent = row.username;
      const score = document.createElement("span");
      score.textContent = `${row.points || 0} pts`;
      line.append(rank, name, score);
      return line;
    }),
  );
}

function startRematchCountdown(deadline) {
  window.clearInterval(state.countdownTimer);
  const update = () => {
    const seconds = Math.max(0, Math.ceil((Number(deadline || 0) * 1000 - Date.now()) / 1000));
    byId("rematchCountdown").textContent = String(seconds);
    if (seconds === 0) window.clearInterval(state.countdownTimer);
  };
  update();
  state.countdownTimer = window.setInterval(update, 250);
}

function renderGame(game) {
  state.actionPending = false;
  state.game = game;
  state.roomCode = game.code;
  state.isHost = game.hostId === state.playerId;
  persistSession();
  showView("game");

  const me = game.players.find((player) => player.id === state.playerId);
  const current = game.players.find((player) => player.id === game.currentPlayerId);
  const myTurn = game.status === "playing" && game.currentPlayerId === state.playerId && !me?.spectator;
  const winner = game.winner;
  const challenge = game.wild4Challenge;

  if (state.previousTurnId !== game.currentPlayerId && myTurn) {
    toast("Your turn.");
    playSound("turn");
  }
  state.previousTurnId = game.currentPlayerId;

  byId("gameRoomCode").textContent = game.code;
  byId("gameModeLabel").textContent = game.mode === "wild" ? "Wild stacking" : "Classic";
  byId("turnStatus").textContent = winner
    ? `${winner.username} wins`
    : challenge?.canRespond
      ? "Choose whether to challenge +4"
      : myTurn && game.pendingDraw
        ? `Stack a draw card or take ${game.pendingDraw}`
    : myTurn
      ? "Your turn"
      : `${current?.username || "The table"} is playing`;
  byId("turnPill").textContent = winner
    ? "Finished"
    : challenge
      ? "Challenge window"
      : myTurn
        ? game.pendingDraw ? `Draw ${game.pendingDraw} or stack` : "Play a card"
        : "Waiting";
  byId("directionLabel").textContent = game.direction === 1 ? "Clockwise" : "Counterclockwise";
  byId("drawPileCount").textContent = game.pendingDraw ? `+${game.pendingDraw}` : String(game.drawPileCount);
  byId("drawPile").title = game.pendingDraw
    ? `Draw the ${game.pendingDraw}-card penalty (${game.drawPileCount} cards remain)`
    : `Draw one card (${game.drawPileCount} cards remain)`;
  byId("handCount").textContent = String(game.hand.length);
  document.title = myTurn ? "Your turn | UNO Live" : `Room ${game.code} | UNO Live`;

  const colorStatus = byId("colorStatus");
  colorStatus.querySelector("i").style.background = COLOR_HEX[game.currentColor] || "#ffffff";
  colorStatus.querySelector("span").textContent = game.currentColor
    ? `${game.currentColor[0].toUpperCase()}${game.currentColor.slice(1)}`
    : "No color";

  byId("gamePlayers").replaceChildren(
    ...game.players.map((player) => makeGamePlayer(player, game.currentPlayerId)),
  );
  byId("opponentRail").replaceChildren(
    ...game.players
      .filter((player) => player.id !== state.playerId && !player.spectator)
      .map((player) => makeOpponentChip(player, game.currentPlayerId)),
  );

  const discard = byId("discardPile");
  discard.replaceChildren(...(game.topDiscard ? [makeUnoCard(game.topDiscard)] : []));
  const playable = new Set(game.playableCardIds);
  byId("playerHand").replaceChildren(
    ...game.hand.map((card) => makeUnoCard(card, playable.has(card.id), true)),
  );

  const stackBanner = byId("stackBanner");
  stackBanner.classList.toggle("hidden", !game.pendingDraw);
  byId("stackTotal").textContent = String(game.pendingDraw || 0);

  const challengePanel = byId("wild4Panel");
  challengePanel.classList.toggle("hidden", !challenge || Boolean(winner));
  if (challenge) {
    byId("wild4Offender").textContent = challenge.offenderName;
    byId("wild4DecisionCopy").textContent = challenge.canRespond
      ? `They claimed to have no ${challenge.previousColor} card. Accept 4 or challenge.`
      : "The next player is deciding whether to challenge.";
    byId("acceptWild4Btn").disabled = !challenge.canRespond;
    byId("challengeWild4Btn").disabled = !challenge.canRespond;
  }

  byId("drawPile").disabled = !myTurn || game.canPass || Boolean(winner) || Boolean(challenge);
  byId("passTurnBtn").disabled = !myTurn || !game.canPass || Boolean(winner) || Boolean(challenge);
  byId("unoBtn").disabled = !me || me.spectator || game.hand.length !== 1 || me.saidUno || Boolean(winner);
  byId("unoBtn").classList.toggle("attention", Boolean(game.mustDeclareUno));
  const catchButton = byId("catchUnoBtn");
  catchButton.classList.toggle("hidden", !game.catchableUnoPlayer || Boolean(winner));
  catchButton.textContent = game.catchableUnoPlayer
    ? `Catch ${game.catchableUnoPlayer.username}`
    : "Catch UNO";
  byId("spectatorNotice").classList.toggle("hidden", !me?.spectator);

  const winnerPanel = byId("winnerPanel");
  winnerPanel.classList.toggle("hidden", !winner);
  if (winner) {
    byId("winnerText").textContent = `${winner.username} takes the round`;
    byId("winnerPoints").textContent = `${winner.points} points earned · ${winner.totalPoints} total`;
    const countdown = document.createElement("strong");
    countdown.id = "rematchCountdown";
    countdown.textContent = "10";
    const winnerSubtext = byId("winnerSubtext");
    if (game.matchChampion) {
      winnerSubtext.replaceChildren(
        document.createTextNode(`${game.matchChampion.username} reached ${game.scoreTarget} points. Next round starts in `),
        countdown,
        document.createTextNode("s."),
      );
    } else {
      winnerSubtext.replaceChildren(
        document.createTextNode("Next round begins in "),
        countdown,
        document.createTextNode("s. Pending players are queued automatically."),
      );
    }
    renderRoundStandings(game.leaderboard || []);
    byId("playAgainBtn").disabled = game.rematchChoice === "ready";
    byId("playAgainBtn").textContent = game.rematchChoice === "ready" ? "Queued" : "Play again";
    startRematchCountdown(game.rematchDeadline);
    if (state.previousWinnerId !== winner.id) playSound("win");
    state.previousWinnerId = winner.id;
  } else {
    window.clearInterval(state.countdownTimer);
    state.previousWinnerId = null;
  }

  renderLeaderboard(game.leaderboard || []);
  renderMatchHistory(game.matchHistory || []);
  renderEvents(game.events || []);
  renderChat(game.chat || []);
}

function showChallengeReveal(payload) {
  const panel = byId("challengeRevealPanel");
  byId("challengeRevealName").textContent = payload.offenderName || "Player";
  byId("challengeRevealResult").textContent = payload.result || "Challenge resolved.";
  byId("challengeRevealCards").replaceChildren(
    ...(payload.hand || []).map((card) => makeUnoCard(card)),
  );
  panel.classList.remove("hidden");
}

function makeChatMessage(message) {
  const row = document.createElement("div");
  row.className = "chat-message";
  row.dataset.messageId = message.id;
  row.appendChild(makeAvatar(message));
  const bubble = document.createElement("div");
  bubble.className = "chat-bubble";
  const author = document.createElement("strong");
  author.textContent = message.username;
  const text = document.createElement("span");
  text.textContent = message.text;
  bubble.append(author, text);
  row.appendChild(bubble);
  return row;
}

function renderChat(messages) {
  state.messageIds = new Set(messages.map((message) => message.id));
  [byId("lobbyChatMessages"), byId("gameChatMessages")].forEach((container) => {
    if (!container) return;
    if (!messages.length) {
      container.replaceChildren(emptyState("No messages yet. Say hello."));
      return;
    }
    container.replaceChildren(...messages.map(makeChatMessage));
    container.scrollTop = container.scrollHeight;
  });
}

function appendChat(message) {
  if (state.messageIds.has(message.id)) return;
  state.messageIds.add(message.id);
  [byId("lobbyChatMessages"), byId("gameChatMessages")].forEach((container) => {
    if (!container || container.closest(".hidden")) return;
    container.querySelector(".empty-state")?.remove();
    container.appendChild(makeChatMessage(message));
    container.scrollTop = container.scrollHeight;
  });
}

function send(event, payload = {}, lockAction = false) {
  if (!state.socket?.connected) {
    toast("The server is reconnecting. Try again in a moment.", true);
    return;
  }
  if (lockAction && state.actionPending) return;
  if (lockAction) state.actionPending = true;
  state.socket.emit(event, { roomCode: state.roomCode, ...payload });
}

function submitJoin(createRoom) {
  if (state.busy) return;
  if (!state.socket?.connected) {
    toast("The real-time server is still connecting. Refresh and try again.", true);
    return;
  }
  const username = cleanDisplayName(byId("usernameInput").value);
  const avatar = byId("avatarInput").value;
  if (username.length < 2) {
    toast("Enter a display name with at least 2 characters.", true);
    return;
  }
  state.username = username;
  state.avatar = avatar;
  const payload = { username, avatar };
  if (!createRoom) {
    const roomCode = byId("roomCodeInput").value.trim().toUpperCase();
    if (!/^[A-Z0-9]{6}$/.test(roomCode)) {
      toast("Enter the 6-character invite code.", true);
      return;
    }
    payload.roomCode = roomCode;
  }
  setBusy(true);
  state.socket.emit(createRoom ? "createRoom" : "joinRoom", payload);
}

async function copyCode() {
  try {
    await navigator.clipboard.writeText(state.roomCode);
    toast(`Invite code ${state.roomCode} copied.`);
  } catch (_) {
    const input = document.createElement("textarea");
    input.value = state.roomCode;
    document.body.appendChild(input);
    input.select();
    document.execCommand("copy");
    input.remove();
    toast(`Invite code ${state.roomCode} copied.`);
  }
}

function leaveRoom() {
  if (state.socket?.connected) send("leaveRoom");
  else {
    clearSession();
    showView("welcome");
  }
}

function wireChat(formId, inputId) {
  byId(formId).addEventListener("submit", (event) => {
    event.preventDefault();
    const input = byId(inputId);
    const text = input.value.replace(/[<>]/g, "").trim().slice(0, 240);
    if (!text) return;
    send("chatMessage", { text });
    input.value = "";
  });
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("uno.theme", theme);
  byId("themeToggle").textContent = theme === "dark" ? "Light mode" : "Dark mode";
}

function initializeSocket() {
  if (typeof window.io !== "function") {
    byId("welcomeStatus").textContent = "The real-time client could not load. Refresh the page and try again.";
    toast("The Socket.IO client failed to load.", true);
    return;
  }
  state.socket = io({ transports: ["websocket", "polling"], reconnection: true });
  state.socket.on("connect", () => {
    byId("connectionBanner").classList.add("hidden");
    if (state.roomCode && state.playerId) {
      state.socket.emit("rejoinRoom", { roomCode: state.roomCode, playerId: state.playerId });
    }
  });
  state.socket.on("disconnect", () => {
    state.actionPending = false;
    if (state.roomCode) byId("connectionBanner").classList.remove("hidden");
  });
  state.socket.on("roomJoined", (payload) => {
    setBusy(false);
    state.roomCode = payload.roomCode;
    state.playerId = payload.playerId;
    state.isHost = payload.isHost;
    persistSession();
    history.replaceState({}, "", `/room/${state.roomCode}`);
    if (payload.spectator && !payload.rejoined) toast("The match is active. You joined as a spectator.");
  });
  state.socket.on("lobbyState", renderLobby);
  state.socket.on("gameState", renderGame);
  state.socket.on("chatMessage", appendChat);
  state.socket.on("notification", ({ message }) => toast(message));
  state.socket.on("challengeReveal", showChallengeReveal);
  state.socket.on("errorMessage", ({ message }) => {
    state.actionPending = false;
    setBusy(false);
    toast(message, true);
  });
  state.socket.on("sessionExpired", ({ message }) => {
    clearSession();
    setBusy(false);
    showView("welcome");
    history.replaceState({}, "", "/");
    toast(message || "Your saved room is no longer available.", true);
  });
  state.socket.on("leftRoom", () => {
    clearSession();
    showView("welcome");
    history.replaceState({}, "", "/");
    document.title = "UNO Live";
  });
}

byId("usernameInput").value = state.username;
byId("avatarInput").value = state.avatar;
byId("roomCodeInput").value = invitedRoomCode || state.roomCode;

byId("createRoomBtn").addEventListener("click", () => submitJoin(true));
byId("joinForm").addEventListener("submit", (event) => {
  event.preventDefault();
  submitJoin(false);
});
byId("copyCodeBtn").addEventListener("click", copyCode);
byId("copyGameCodeBtn").addEventListener("click", copyCode);
byId("startGameBtn").addEventListener("click", () => send("startGame", {}, true));
byId("leaveLobbyBtn").addEventListener("click", leaveRoom);
byId("leaveGameBtn").addEventListener("click", leaveRoom);
byId("drawPile").addEventListener("click", () => send("drawCard", {}, true));
byId("passTurnBtn").addEventListener("click", () => send("passTurn", {}, true));
byId("unoBtn").addEventListener("click", () => send("declareUno", {}, true));
byId("catchUnoBtn").addEventListener("click", () => send("catchUno", {}, true));
byId("acceptWild4Btn").addEventListener("click", () => send("acceptWild4", {}, true));
byId("challengeWild4Btn").addEventListener("click", () => send("challengeWild4", {}, true));
byId("closeChallengeRevealBtn").addEventListener("click", () => {
  byId("challengeRevealPanel").classList.add("hidden");
});
byId("playAgainBtn").addEventListener("click", () => send("playAgain", {}, true));
byId("winnerLeaveBtn").addEventListener("click", leaveRoom);

document.querySelectorAll(".mode-option").forEach((button) => {
  button.addEventListener("click", () => send("setGameMode", { mode: button.dataset.mode }, true));
});

byId("playerHand").addEventListener("click", (event) => {
  const button = event.target.closest("button.uno-card.playable");
  if (!button || !state.game) return;
  const card = state.game.hand.find((item) => item.id === button.dataset.cardId);
  if (!card) return;
  if (card.color === "wild") {
    state.selectedWildCardId = card.id;
    byId("colorModal").classList.remove("hidden");
    return;
  }
  send("playCard", { cardId: card.id }, true);
  playSound("play");
});

byId("colorModal").addEventListener("click", (event) => {
  const choice = event.target.closest("button[data-color]");
  if (!choice || !state.selectedWildCardId) return;
  send("playCard", { cardId: state.selectedWildCardId, chosenColor: choice.dataset.color }, true);
  state.selectedWildCardId = null;
  byId("colorModal").classList.add("hidden");
  playSound("play");
});

byId("cancelColorBtn").addEventListener("click", () => {
  state.selectedWildCardId = null;
  byId("colorModal").classList.add("hidden");
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    state.selectedWildCardId = null;
    byId("colorModal").classList.add("hidden");
    byId("challengeRevealPanel").classList.add("hidden");
  }
});

document.querySelectorAll(".sidebar-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".sidebar-tab").forEach((item) => item.classList.remove("active"));
    document.querySelectorAll(".sidebar-panel").forEach((panel) => panel.classList.add("hidden"));
    tab.classList.add("active");
    byId(tab.dataset.panel).classList.remove("hidden");
  });
});

wireChat("lobbyChatForm", "lobbyChatInput");
wireChat("gameChatForm", "gameChatInput");

byId("soundToggle").textContent = state.soundEnabled ? "Sound on" : "Sound off";
byId("soundToggle").addEventListener("click", () => {
  state.soundEnabled = !state.soundEnabled;
  localStorage.setItem("uno.sound", state.soundEnabled ? "on" : "off");
  byId("soundToggle").textContent = state.soundEnabled ? "Sound on" : "Sound off";
  playSound("notice");
});

const preferredTheme = localStorage.getItem("uno.theme")
  || (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
applyTheme(preferredTheme);
byId("themeToggle").addEventListener("click", () => {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
});

initializeSocket();
