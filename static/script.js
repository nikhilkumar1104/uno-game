"use strict";

const byId = (id) => document.getElementById(id);

const views = {
  welcome: byId("welcomeView"),
  lobby: byId("lobbyView"),
  game: byId("gameView"),
};

const invitedRoomCode = String(document.body.dataset.initialRoom || "").trim().toUpperCase();
const storedRoomCode = sessionStorage.getItem("uno.roomCode")
  || localStorage.getItem("uno.activeRoomCode")
  || "";
const initialRoomCode = invitedRoomCode || storedRoomCode;
const roomRecoveryPlayerId = initialRoomCode
  ? localStorage.getItem(`uno.rejoin.${initialRoomCode}`) || ""
  : "";
const storedPlayerId = sessionStorage.getItem("uno.playerId")
  || localStorage.getItem("uno.activePlayerId")
  || "";

const state = {
  socket: null,
  roomCode: initialRoomCode,
  playerId: roomRecoveryPlayerId || (initialRoomCode === storedRoomCode ? storedPlayerId : ""),
  username: sessionStorage.getItem("uno.username") || localStorage.getItem("uno.activeUsername") || "",
  avatar: sessionStorage.getItem("uno.avatar") || localStorage.getItem("uno.activeAvatar") || "ember",
  isHost: false,
  game: null,
  selectedWildCardId: null,
  selectedWildTargetId: null,
  selectedSwapCardId: null,
  previousTurnId: null,
  messageIds: new Set(),
  soundEnabled: localStorage.getItem("uno.sound") !== "off",
  effectsVolume: Math.min(1, Math.max(0, Number(localStorage.getItem("uno.effectsVolume") || 0.6))),
  musicEnabled: localStorage.getItem("uno.music") === "on",
  musicVolume: Math.min(1, Math.max(0, Number(localStorage.getItem("uno.musicVolume") || 0.2))),
  busy: false,
  rejoinPending: false,
  actionPending: false,
  countdownTimer: null,
  previousWinnerId: null,
  lastNotificationMessage: "",
  colorSymbols: localStorage.getItem("uno.colorSymbols") !== "off",
  highContrast: localStorage.getItem("uno.highContrast") === "on",
  reducedMotion: localStorage.getItem("uno.reducedMotion") === "on",
  installPrompt: null,
  tutorialStep: 0,
  voice: {
    joined: false,
    muted: false,
    deafened: false,
    stream: null,
    peers: new Map(),
    participants: [],
    analyserTimer: null,
    statsTimer: null,
    speaking: false,
  },
};

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

const COLOR_SYMBOLS = { red: "◆", yellow: "●", green: "▲", blue: "■", wild: "✦" };
const TUTORIAL_STEPS = [
  ["Match a card", "↔", "Play a card that matches the active color, number, or action symbol."],
  ["Use action cards", "+2", "Skip, Reverse, Draw Two, Wild, and Wild Draw Four can change the round instantly."],
  ["Draw when blocked", "＋", "Select the draw pile. A playable drawn card can be played, unless Forced Play handles it automatically."],
  ["Call UNO", "UNO", "Press UNO when exactly one card remains. Other players can catch a missed call for a two-card penalty."],
  ["Use quick controls", "⌨", "Arrow keys select cards. Enter plays, D draws, U calls UNO, C catches, and V joins voice."],
];

if ("scrollRestoration" in history) history.scrollRestoration = "manual";

function showView(name) {
  const isAlreadyVisible = !views[name].classList.contains("hidden");
  Object.values(views).forEach((view) => view.classList.add("hidden"));
  views[name].classList.remove("hidden");
  document.body.dataset.view = name;
  if (name !== "game") document.body.dataset.mobilePanel = "";
  if (!isAlreadyVisible) window.scrollTo({ top: 0, behavior: "auto" });
}

function persistSession() {
  sessionStorage.setItem("uno.roomCode", state.roomCode);
  sessionStorage.setItem("uno.playerId", state.playerId);
  sessionStorage.setItem("uno.username", state.username);
  sessionStorage.setItem("uno.avatar", state.avatar);
  localStorage.setItem("uno.activeRoomCode", state.roomCode);
  localStorage.setItem("uno.activePlayerId", state.playerId);
  localStorage.setItem("uno.activeUsername", state.username);
  localStorage.setItem("uno.activeAvatar", state.avatar);
  if (state.roomCode && state.playerId) {
    localStorage.setItem(`uno.rejoin.${state.roomCode}`, state.playerId);
  }
}

function clearSession({ keepRecovery = false } = {}) {
  const roomCode = state.roomCode;
  ["uno.roomCode", "uno.playerId", "uno.username", "uno.avatar"].forEach((key) => {
    sessionStorage.removeItem(key);
  });
  ["uno.activeRoomCode", "uno.activePlayerId", "uno.activeUsername", "uno.activeAvatar"].forEach((key) => {
    localStorage.removeItem(key);
  });
  if (roomCode && !keepRecovery) localStorage.removeItem(`uno.rejoin.${roomCode}`);
  state.roomCode = "";
  state.playerId = "";
  state.game = null;
  state.isHost = false;
  state.rejoinPending = false;
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
function toast(message, error = false, withSound = true) {
  const element = byId("toast");
  element.textContent = message;
  element.classList.toggle("error", error);
  element.classList.remove("hidden");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => element.classList.add("hidden"), 3000);
  if (withSound) playSound(error ? "error" : "notice");
}

let actionPopupTimer;
function showActionPopup(type, message) {
  const popup = byId("actionPopup");
  const titles = { uno: "UNO!", catch: "CAUGHT!", win: "ROUND WON!", chat: "TABLE TALK", reaction: "REACTION" };
  byId("actionPopupTitle").textContent = titles[type] || "UNO!";
  byId("actionPopupMessage").textContent = message || "Table action announced.";
  popup.className = `action-popup ${type}`;
  window.clearTimeout(actionPopupTimer);
  actionPopupTimer = window.setTimeout(() => popup.classList.add("hidden"), type === "win" ? 3200 : 2200);
}

let sharedAudioContext = null;
let musicTimer = null;
let musicStep = 0;

function audioContext() {
  if (!sharedAudioContext) {
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) return null;
    sharedAudioContext = new AudioContext();
  }
  if (sharedAudioContext.state === "suspended") sharedAudioContext.resume().catch(() => {});
  return sharedAudioContext;
}

function tone(context, frequency, start, duration, volume, wave = "sine") {
  const oscillator = context.createOscillator();
  const gain = context.createGain();
  oscillator.type = wave;
  oscillator.frequency.setValueAtTime(frequency, start);
  gain.gain.setValueAtTime(0.0001, start);
  gain.gain.exponentialRampToValueAtTime(Math.max(0.0001, volume), start + 0.015);
  gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
  oscillator.connect(gain);
  gain.connect(context.destination);
  oscillator.start(start);
  oscillator.stop(start + duration + 0.02);
}

function playSound(type) {
  if (!state.soundEnabled || state.effectsVolume <= 0) return;
  try {
    const context = audioContext();
    if (!context) return;
    const patterns = {
      play: [[360, 0, 0.07, "triangle"], [520, 0.045, 0.09, "triangle"]],
      uno: [[523, 0, 0.1, "square"], [659, 0.09, 0.1, "square"], [784, 0.18, 0.16, "square"]],
      catch: [[320, 0, 0.09, "sawtooth"], [210, 0.08, 0.16, "sawtooth"]],
      win: [[523, 0, 0.14, "triangle"], [659, 0.12, 0.14, "triangle"], [784, 0.24, 0.14, "triangle"], [1047, 0.37, 0.28, "triangle"]],
      turn: [[620, 0, 0.13, "sine"]],
      error: [[170, 0, 0.18, "sawtooth"]],
      notice: [[350, 0, 0.11, "sine"]],
    };
    const now = context.currentTime;
    (patterns[type] || patterns.notice).forEach(([frequency, offset, duration, wave]) => {
      tone(context, frequency, now + offset, duration, 0.065 * state.effectsVolume, wave);
    });
  } catch (_) {
    // Audio is optional and may be blocked before the first user gesture.
  }
}

function scheduleMusicNote() {
  if (!state.musicEnabled || state.musicVolume <= 0) return;
  const context = audioContext();
  if (!context) return;
  const melody = [130.81, 164.81, 196, 164.81, 146.83, 174.61, 220, 174.61];
  const frequency = melody[musicStep % melody.length];
  const now = context.currentTime;
  tone(context, frequency, now, 1.35, 0.035 * state.musicVolume, "sine");
  tone(context, frequency * 2, now + 0.08, 0.8, 0.012 * state.musicVolume, "triangle");
  musicStep += 1;
}

function startMusic() {
  if (!state.musicEnabled || musicTimer) return;
  scheduleMusicNote();
  musicTimer = window.setInterval(scheduleMusicNote, 850);
}

function stopMusic() {
  window.clearInterval(musicTimer);
  musicTimer = null;
}

function updateAudioControls() {
  byId("soundToggle").textContent = state.soundEnabled ? "Effects on" : "Effects off";
  byId("musicToggle").textContent = state.musicEnabled ? "Music on" : "Music off";
  byId("effectsVolume").value = String(Math.round(state.effectsVolume * 100));
  byId("musicVolume").value = String(Math.round(state.musicVolume * 100));
  byId("effectsVolumeValue").textContent = `${Math.round(state.effectsVolume * 100)}%`;
  byId("musicVolumeValue").textContent = `${Math.round(state.musicVolume * 100)}%`;
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
  if (player.isBot) name.appendChild(makeBadge("bot-badge", "CPU"));
  if (player.team === 0 || player.team === 1) {
    name.appendChild(makeBadge(`team-badge team-${player.team}`, player.team === 0 ? "Red" : "Blue"));
  }
  details.appendChild(name);

  const note = document.createElement("span");
  note.className = "player-note";
  note.textContent = player.isBot
    ? `${(player.botDifficulty || "medium")[0].toUpperCase()}${(player.botDifficulty || "medium").slice(1)} · ${(player.botPersonality || "balanced").replace("_", " ")}`
    : player.spectator
      ? "Spectator"
      : player.connected
        ? "Ready"
        : "Reconnecting";
  details.appendChild(note);
  card.appendChild(details);
  if (player.isBot && state.isHost) {
    const remove = document.createElement("button");
    remove.className = "remove-bot-button";
    remove.type = "button";
    remove.dataset.botId = player.id;
    remove.textContent = "Remove";
    remove.setAttribute("aria-label", `Remove ${player.username}`);
    card.appendChild(remove);
  }
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
  document.querySelectorAll(".format-option").forEach((button) => {
    button.classList.toggle("selected", button.dataset.format === room.playFormat);
    button.disabled = !state.isHost;
  });
  document.querySelectorAll(".room-rule-input").forEach((input) => {
    input.checked = Boolean(room.rules?.[input.dataset.rule]);
    input.disabled = !state.isHost;
  });
  byId("modeLockHint").textContent = state.isHost
    ? "Choose before starting"
    : `${room.mode === "wild" ? "Wild stacking" : "Classic"} selected by host`;

  const list = byId("lobbyPlayers");
  list.replaceChildren(...room.players.map(renderLobbyPlayer));
  byId("addBotBtn").disabled = !state.isHost || active.length >= 6;
  byId("botDifficulty").disabled = !state.isHost;
  byId("botPersonality").disabled = !state.isHost;

  const start = byId("startGameBtn");
  const validTeamTable = room.playFormat !== "teams" || connected.length === 4;
  start.disabled = !state.isHost || connected.length < 2 || !validTeamTable;
  byId("lobbyMessage").textContent = state.isHost
    ? room.playFormat === "teams" && connected.length !== 4
      ? `2v2 needs exactly 4 connected seats (${connected.length}/4 ready).`
      : connected.length >= 2
      ? "The table is ready. Start with friends, computers, or both."
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
  if (player.isBot) name.appendChild(makeBadge("bot-badge", "CPU"));
  if (player.id === currentPlayerId) name.appendChild(makeBadge("turn-badge", "Turn"));
  if (player.saidUno) name.appendChild(makeBadge("uno-badge", "UNO"));
  details.appendChild(name);

  const note = document.createElement("span");
  note.className = "player-note";
  note.textContent = player.isBot
    ? `${player.botDifficulty || "medium"} · ${(player.botPersonality || "balanced").replace("_", " ")}`
    : player.spectator
      ? "Spectating"
      : player.connected
        ? "At table"
        : "Offline";
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
    element.draggable = playable;
    element.setAttribute("aria-label", `${card.color} ${cardLabel(card)}${playable ? ", playable" : ""}`);
  }

  const top = document.createElement("span");
  top.className = "uno-card-corner";
  top.dataset.label = cardLabel(card);
  top.textContent = `${COLOR_SYMBOLS[card.color] || ""} ${cardLabel(card)}`.trim();
  const value = document.createElement("span");
  value.className = "uno-card-value";
  value.textContent = cardLabel(card);
  const bottom = document.createElement("span");
  bottom.className = "uno-card-corner bottom";
  bottom.dataset.label = cardLabel(card);
  bottom.textContent = `${COLOR_SYMBOLS[card.color] || ""} ${cardLabel(card)}`.trim();
  const symbol = document.createElement("span");
  symbol.className = "card-color-symbol";
  symbol.textContent = COLOR_SYMBOLS[card.color] || "";
  element.append(top, value, symbol, bottom);
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

function renderActiveRules(game) {
  const descriptions = [
    game.mode === "wild"
      ? ["Wild stacking", "+2 stacks only on +2, and +4 only on +4. Cross-stacking is blocked. Draw the full total when you cannot continue."]
      : ["Classic", "No stacking. A +4 may be challenged and is legal only when its player has no card matching the active color."],
  ];
  if (game.playFormat === "teams") {
    descriptions.push(["2v2 teams", "Four alternating seats form two teams. A team wins the round when either partner empties their hand."]);
  }
  if (game.rules?.seven_zero) {
    descriptions.push(["Seven-O", "A 7 swaps your remaining hand with a chosen player. A 0 rotates every hand in the current direction."]);
  }
  if (game.rules?.jump_in) {
    descriptions.push(["Jump-In", "Interrupt any turn with an exact color-and-symbol match. Play continues from the player who jumped in."]);
  }
  if (game.rules?.forced_play) {
    descriptions.push(["Forced Play", "A playable drawn card is played immediately. For a Wild, the server chooses your strongest remaining color."]);
  }

  byId("activeRulesList").replaceChildren(...descriptions.map(([title, copy]) => {
    const item = document.createElement("article");
    item.className = "active-rule";
    const heading = document.createElement("strong");
    heading.textContent = title;
    const text = document.createElement("span");
    text.textContent = copy;
    item.append(heading, text);
    return item;
  }));
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
  const preserveViewport = !views.game.classList.contains("hidden");
  const viewportTop = window.scrollY;
  const previousHandScroll = byId("playerHand").scrollLeft;
  if (byId("playerHand").contains(document.activeElement)) document.activeElement.blur();
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
    toast("Your turn.", false, false);
    playSound("turn");
  }
  state.previousTurnId = game.currentPlayerId;

  byId("gameRoomCode").textContent = game.code;
  const customCount = Object.values(game.rules || {}).filter(Boolean).length;
  byId("gameModeLabel").textContent = `${game.mode === "wild" ? "Wild stacking" : "Classic"}${game.playFormat === "teams" ? " · 2v2" : ""}${customCount ? ` · ${customCount} custom` : ""}`;
  byId("turnStatus").textContent = winner
    ? `${winner.teamLabel || winner.username} wins`
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
    ? `${COLOR_SYMBOLS[game.currentColor] || ""} ${game.currentColor[0].toUpperCase()}${game.currentColor.slice(1)}`
    : "No color";

  const teamBanner = byId("teamScoreBanner");
  teamBanner.classList.toggle("hidden", game.playFormat !== "teams");
  byId("teamRedScore").textContent = String(game.teamScores?.["0"] || 0);
  byId("teamBlueScore").textContent = String(game.teamScores?.["1"] || 0);

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
    const winnerName = winner.teamLabel || winner.username;
    byId("winnerText").textContent = `${winnerName} takes the round`;
    if (winner.isTeam) byId("winnerText").textContent += ` — ${winner.members.join(" & ")}`;
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
    if (state.previousWinnerId !== winner.id) {
      playSound("win");
      showActionPopup("win", `${winnerName} won ${winner.points} points.`);
    }
    state.previousWinnerId = winner.id;
  } else {
    window.clearInterval(state.countdownTimer);
    state.previousWinnerId = null;
  }

  renderLeaderboard(game.leaderboard || []);
  renderMatchHistory(game.matchHistory || []);
  renderEvents(game.events || []);
  renderActiveRules(game);
  renderChat(game.chat || []);
  renderVoiceParticipants();
  if (preserveViewport) {
    window.requestAnimationFrame(() => {
      window.scrollTo({ top: viewportTop, behavior: "auto" });
      byId("playerHand").scrollLeft = previousHandScroll;
    });
  }
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
    const distanceFromBottom = container.scrollHeight - container.scrollTop;
    const stayAtBottom = distanceFromBottom <= container.clientHeight + 40;
    if (!messages.length) {
      container.replaceChildren(emptyState("No messages yet. Say hello."));
      return;
    }
    container.replaceChildren(...messages.map(makeChatMessage));
    container.scrollTop = stayAtBottom
      ? container.scrollHeight
      : Math.max(0, container.scrollHeight - distanceFromBottom);
  });
}

function appendChat(message) {
  if (state.messageIds.has(message.id)) return;
  state.messageIds.add(message.id);
  [byId("lobbyChatMessages"), byId("gameChatMessages")].forEach((container) => {
    // Keep every chat surface synchronized even while its tab is hidden.
    // Otherwise opening Chat after spending time in Voice/Activity would omit
    // messages received during that period.
    if (!container) return;
    const stayAtBottom = container.scrollHeight - container.scrollTop <= container.clientHeight + 40;
    container.querySelector(".empty-state")?.remove();
    container.appendChild(makeChatMessage(message));
    if (stayAtBottom) container.scrollTop = container.scrollHeight;
  });
  const emojiOnly = /^[\p{Extended_Pictographic}\u200d\ufe0f]+$/u.test(message.text);
  showActionPopup(emojiOnly ? "reaction" : "chat", `${message.username}: ${message.text}`);
  playSound("notice");
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

function sendSelectedCard(card, targetPlayerId = null) {
  if (card.color === "wild") {
    state.selectedWildCardId = card.id;
    state.selectedWildTargetId = targetPlayerId;
    byId("colorModal").classList.remove("hidden");
    byId("colorModal").querySelector("button[data-color]")?.focus();
    return;
  }
  send("playCard", { cardId: card.id, targetPlayerId }, true);
}

function requestCardPlay(card) {
  if (!card || !state.game?.playableCardIds.includes(card.id)) return;
  const needsSwap = state.game.rules?.seven_zero && card.value === "7" && state.game.hand.length > 1;
  if (!needsSwap) {
    sendSelectedCard(card);
    return;
  }
  state.selectedSwapCardId = card.id;
  const choices = state.game.players
    .filter((player) => !player.spectator && player.id !== state.playerId)
    .map((player) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "swap-player-button";
      button.dataset.swapPlayerId = player.id;
      button.append(makeAvatar(player), document.createTextNode(`${player.username} · ${player.cardCount} cards`));
      return button;
    });
  byId("swapPlayerChoices").replaceChildren(...choices);
  byId("swapModal").classList.remove("hidden");
  choices[0]?.focus();
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
    const recoveryPlayerId = localStorage.getItem(`uno.rejoin.${roomCode}`)
      || (state.roomCode === roomCode ? state.playerId : "");
    if (recoveryPlayerId) {
      state.roomCode = roomCode;
      state.playerId = recoveryPlayerId;
      state.rejoinPending = true;
      setBusy(true);
      byId("welcomeStatus").textContent = "Rejoining your reserved seat...";
      state.socket.emit("rejoinRoom", { roomCode, playerId: recoveryPlayerId });
      return;
    }
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

function requestLeaveConfirmation() {
  const playing = state.game?.status === "playing";
  byId("leaveConfirmTitle").textContent = playing
    ? "Are you sure you want to leave the game?"
    : "Are you sure you want to leave this table?";
  byId("leaveConfirmCopy").textContent = playing
    ? "Leaving intentionally removes you from this match. If the page closes or your network drops accidentally, do not press Leave game—you can reopen the site and continue with the same hand."
    : "You will leave this room and give up your reserved seat. You can join again later only if a seat is still available.";
  byId("leaveConfirmModal").classList.remove("hidden");
  byId("cancelLeaveBtn").focus();
}

function closeLeaveConfirmation() {
  byId("leaveConfirmModal").classList.add("hidden");
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

function applyAccessibility() {
  document.documentElement.dataset.colorSymbols = state.colorSymbols ? "on" : "off";
  document.documentElement.dataset.contrast = state.highContrast ? "high" : "normal";
  document.documentElement.dataset.motion = state.reducedMotion ? "reduced" : "normal";
  byId("colorBlindToggle").checked = state.colorSymbols;
  byId("highContrastToggle").checked = state.highContrast;
  byId("reducedMotionToggle").checked = state.reducedMotion;
}

function renderTutorial() {
  const [title, visual, copy] = TUTORIAL_STEPS[state.tutorialStep];
  byId("tutorialTitle").textContent = title;
  byId("tutorialVisual").textContent = visual;
  byId("tutorialCopy").textContent = copy;
  byId("tutorialStep").textContent = `${state.tutorialStep + 1} / ${TUTORIAL_STEPS.length}`;
  byId("tutorialBackBtn").disabled = state.tutorialStep === 0;
  byId("tutorialNextBtn").textContent = state.tutorialStep === TUTORIAL_STEPS.length - 1 ? "Done" : "Next";
}

function openTutorial() {
  state.tutorialStep = 0;
  renderTutorial();
  byId("tutorialModal").classList.remove("hidden");
  byId("tutorialNextBtn").focus();
}

function voiceSignal(targetPlayerId, signal) {
  send("voiceSignal", { targetPlayerId, signal });
}

function closeVoicePeer(playerId) {
  const peer = state.voice.peers.get(playerId);
  if (!peer) return;
  peer.pc.ontrack = null;
  peer.pc.onicecandidate = null;
  peer.pc.close();
  peer.audio?.remove();
  state.voice.peers.delete(playerId);
}

async function createVoicePeer(playerId, initiator = false) {
  if (state.voice.peers.has(playerId)) return state.voice.peers.get(playerId);
  const pc = new RTCPeerConnection({
    iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
  });
  const record = { pc, audio: null, pendingCandidates: [] };
  state.voice.peers.set(playerId, record);
  state.voice.stream?.getTracks().forEach((track) => pc.addTrack(track, state.voice.stream));
  pc.onicecandidate = ({ candidate }) => {
    if (candidate) voiceSignal(playerId, { type: "candidate", candidate: candidate.toJSON() });
  };
  pc.ontrack = ({ streams }) => {
    let audio = record.audio;
    if (!audio) {
      audio = document.createElement("audio");
      audio.autoplay = true;
      audio.playsInline = true;
      audio.dataset.playerId = playerId;
      byId("remoteAudio").appendChild(audio);
      record.audio = audio;
    }
    audio.srcObject = streams[0];
    audio.muted = state.voice.deafened;
  };
  pc.onconnectionstatechange = updateVoiceQuality;
  if (initiator) {
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    voiceSignal(playerId, { type: "offer", description: pc.localDescription });
  }
  return record;
}

async function handleVoiceSignal({ fromPlayerId, signal }) {
  if (!state.voice.joined || !fromPlayerId || !signal) return;
  try {
    const peer = await createVoicePeer(fromPlayerId, false);
    if (signal.type === "candidate") {
      if (peer.pc.remoteDescription) await peer.pc.addIceCandidate(signal.candidate);
      else peer.pendingCandidates.push(signal.candidate);
      return;
    }
    await peer.pc.setRemoteDescription(signal.description);
    for (const candidate of peer.pendingCandidates.splice(0)) {
      await peer.pc.addIceCandidate(candidate);
    }
    if (signal.type === "offer") {
      const answer = await peer.pc.createAnswer();
      await peer.pc.setLocalDescription(answer);
      voiceSignal(fromPlayerId, { type: "answer", description: peer.pc.localDescription });
    }
  } catch (_) {
    closeVoicePeer(fromPlayerId);
    toast("A voice connection could not be established.", true, false);
  }
}

function renderVoiceParticipants() {
  const container = byId("voiceParticipants");
  const members = state.voice.participants || [];
  if (!members.length) {
    container.replaceChildren(emptyState(state.voice.joined ? "Waiting for someone to join voice." : "Voice is optional."));
    return;
  }
  container.replaceChildren(...members.map((member) => {
    const chip = document.createElement("span");
    chip.className = `voice-chip${member.speaking ? " speaking" : ""}`;
    chip.textContent = `${member.speaking ? "◉" : "○"} ${member.username}${member.playerId === state.playerId ? " (you)" : ""}`;
    return chip;
  }));
}

async function updateVoiceQuality() {
  const badge = byId("voiceQuality");
  if (!state.voice.joined) {
    badge.className = "quality-badge offline";
    badge.textContent = "Offline";
    return;
  }
  const peers = [...state.voice.peers.values()];
  if (!peers.length) {
    badge.className = "quality-badge waiting";
    badge.textContent = "Waiting";
    return;
  }
  let worstRtt = 0;
  let connected = 0;
  for (const { pc } of peers) {
    if (pc.connectionState === "connected") connected += 1;
    try {
      const stats = await pc.getStats();
      stats.forEach((report) => {
        if (report.type === "candidate-pair" && report.state === "succeeded" && report.currentRoundTripTime) {
          worstRtt = Math.max(worstRtt, report.currentRoundTripTime);
        }
      });
    } catch (_) { /* Connection may be closing. */ }
  }
  const label = connected !== peers.length ? "Connecting" : worstRtt > 0.45 ? "Poor" : worstRtt > 0.18 ? "Fair" : "Good";
  badge.className = `quality-badge ${label.toLowerCase()}`;
  badge.textContent = label;
}

function startSpeakingDetection() {
  const context = audioContext();
  if (!context || !state.voice.stream) return;
  const analyser = context.createAnalyser();
  analyser.fftSize = 256;
  context.createMediaStreamSource(state.voice.stream).connect(analyser);
  const samples = new Uint8Array(analyser.frequencyBinCount);
  window.clearInterval(state.voice.analyserTimer);
  state.voice.analyserTimer = window.setInterval(() => {
    analyser.getByteFrequencyData(samples);
    const average = samples.reduce((sum, value) => sum + value, 0) / samples.length;
    const speaking = !state.voice.muted && average > 18;
    if (speaking !== state.voice.speaking) {
      state.voice.speaking = speaking;
      send("voiceSpeaking", { speaking });
    }
  }, 250);
}

async function joinVoice() {
  if (state.voice.joined) {
    leaveVoice();
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia || !window.RTCPeerConnection) {
    toast("Voice chat is not supported by this browser.", true);
    return;
  }
  try {
    state.voice.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      video: false,
    });
    state.voice.joined = true;
    state.voice.muted = false;
    state.voice.deafened = false;
    byId("voiceJoinBtn").textContent = "Leave voice";
    byId("voiceMuteBtn").classList.remove("hidden");
    byId("voiceDeafenBtn").classList.remove("hidden");
    send("voiceJoin");
    startSpeakingDetection();
    state.voice.statsTimer = window.setInterval(updateVoiceQuality, 5000);
    updateVoiceQuality();
  } catch (_) {
    toast("Microphone permission is required to join voice.", true);
  }
}

function leaveVoice(notifyServer = true) {
  if (notifyServer && state.voice.joined && state.socket?.connected) send("voiceLeave");
  state.voice.stream?.getTracks().forEach((track) => track.stop());
  state.voice.stream = null;
  [...state.voice.peers.keys()].forEach(closeVoicePeer);
  window.clearInterval(state.voice.analyserTimer);
  window.clearInterval(state.voice.statsTimer);
  state.voice.joined = false;
  state.voice.speaking = false;
  state.voice.participants = [];
  byId("voiceJoinBtn").textContent = "Join voice";
  byId("voiceMuteBtn").classList.add("hidden");
  byId("voiceDeafenBtn").classList.add("hidden");
  renderVoiceParticipants();
  updateVoiceQuality();
}

function updateVoiceParticipants({ members = [] }) {
  state.voice.participants = members;
  renderVoiceParticipants();
  if (!state.voice.joined) return;
  const activeIds = new Set(members.map((member) => member.playerId));
  [...state.voice.peers.keys()].forEach((id) => {
    if (!activeIds.has(id)) closeVoicePeer(id);
  });
  members.forEach((member) => {
    if (member.playerId !== state.playerId && state.playerId.localeCompare(member.playerId) < 0) {
      createVoicePeer(member.playerId, true).catch(() => closeVoicePeer(member.playerId));
    }
  });
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
      state.rejoinPending = true;
      setBusy(true);
      byId("welcomeStatus").textContent = "Rejoining your reserved seat...";
      state.socket.emit("rejoinRoom", { roomCode: state.roomCode, playerId: state.playerId });
    }
  });
  state.socket.on("disconnect", () => {
    state.actionPending = false;
    if (state.voice.joined) leaveVoice(false);
    if (state.roomCode) byId("connectionBanner").classList.remove("hidden");
  });
  state.socket.on("roomJoined", (payload) => {
    setBusy(false);
    state.rejoinPending = false;
    state.roomCode = payload.roomCode;
    state.playerId = payload.playerId;
    state.username = payload.username || state.username;
    state.avatar = payload.avatar || state.avatar;
    state.isHost = payload.isHost;
    persistSession();
    history.replaceState({}, "", `/room/${state.roomCode}`);
    if (payload.rejoined) toast("Seat restored. You can continue with the same hand.");
    if (payload.spectator && !payload.rejoined) toast("The match is active. You joined as a spectator.");
  });
  state.socket.on("lobbyState", renderLobby);
  state.socket.on("gameState", renderGame);
  state.socket.on("chatMessage", appendChat);
  state.socket.on("notification", ({ message }) => {
    state.lastNotificationMessage = message;
    toast(message, false, false);
  });
  state.socket.on("soundEffect", ({ type }) => {
    playSound(type);
    if (type === "uno" || type === "catch") {
      showActionPopup(type, state.lastNotificationMessage);
    }
  });
  state.socket.on("challengeReveal", showChallengeReveal);
  state.socket.on("voiceParticipants", updateVoiceParticipants);
  state.socket.on("voiceSignal", handleVoiceSignal);
  state.socket.on("voicePeerLeft", ({ playerId }) => closeVoicePeer(playerId));
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
    const keepRecovery = state.game?.status === "playing";
    leaveVoice(false);
    clearSession({ keepRecovery });
    showView("welcome");
    history.replaceState({}, "", "/");
    document.title = "UNO Live";
    if (keepRecovery) {
      toast("Your seat is reserved. Enter the same room code on this browser to rejoin.");
    }
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
byId("addBotBtn").addEventListener("click", () => send("addBot", {
  difficulty: byId("botDifficulty").value,
  personality: byId("botPersonality").value,
}, true));
byId("lobbyPlayers").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-bot-id]");
  if (button) send("removeBot", { botId: button.dataset.botId }, true);
});
byId("leaveLobbyBtn").addEventListener("click", requestLeaveConfirmation);
byId("leaveGameBtn").addEventListener("click", requestLeaveConfirmation);
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
byId("winnerLeaveBtn").addEventListener("click", requestLeaveConfirmation);
byId("cancelLeaveBtn").addEventListener("click", closeLeaveConfirmation);
byId("confirmLeaveBtn").addEventListener("click", () => {
  closeLeaveConfirmation();
  leaveRoom();
});
function toggleMobilePanel(name) {
  document.body.dataset.mobilePanel = document.body.dataset.mobilePanel === name ? "" : name;
}
byId("mobileSeatsBtn").addEventListener("click", () => toggleMobilePanel("players"));

function selectGameAsidePanel(panelId) {
  document.querySelectorAll(".game-aside-tab").forEach((tab) => {
    const selected = tab.dataset.gameAsidePanel === panelId;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", String(selected));
  });
  document.querySelectorAll(".game-aside-panel").forEach((panel) => {
    panel.classList.toggle("hidden", panel.id !== panelId);
  });
}

document.querySelectorAll(".game-aside-tab").forEach((tab) => {
  tab.addEventListener("click", () => selectGameAsidePanel(tab.dataset.gameAsidePanel));
});
byId("mobileTalkBtn").addEventListener("click", () => {
  selectGameAsidePanel("gameChatPanel");
  if (window.matchMedia("(orientation: landscape) and (max-height: 520px)").matches) {
    toggleMobilePanel("talk");
  } else {
    document.body.dataset.mobilePanel = "";
    byId("gameChatInput").focus({ preventScroll: true });
  }
});

document.querySelectorAll(".mode-option").forEach((button) => {
  button.addEventListener("click", () => send("setGameMode", { mode: button.dataset.mode }, true));
});

function sendRoomOptions(playFormat = state.game?.playFormat) {
  const selectedFormat = playFormat
    || document.querySelector(".format-option.selected")?.dataset.format
    || "individual";
  const rules = {};
  document.querySelectorAll(".room-rule-input").forEach((input) => {
    rules[input.dataset.rule] = input.checked;
  });
  send("setRoomOptions", { playFormat: selectedFormat, rules }, true);
}

document.querySelectorAll(".format-option").forEach((button) => {
  button.addEventListener("click", () => sendRoomOptions(button.dataset.format));
});
document.querySelectorAll(".room-rule-input").forEach((input) => {
  input.addEventListener("change", () => sendRoomOptions());
});

byId("playerHand").addEventListener("click", (event) => {
  const button = event.target.closest("button.uno-card.playable");
  if (!button || !state.game) return;
  button.blur();
  const card = state.game.hand.find((item) => item.id === button.dataset.cardId);
  if (!card) return;
  requestCardPlay(card);
});

byId("colorModal").addEventListener("click", (event) => {
  const choice = event.target.closest("button[data-color]");
  if (!choice || !state.selectedWildCardId) return;
  send("playCard", {
    cardId: state.selectedWildCardId,
    chosenColor: choice.dataset.color,
    targetPlayerId: state.selectedWildTargetId,
  }, true);
  state.selectedWildCardId = null;
  state.selectedWildTargetId = null;
  byId("colorModal").classList.add("hidden");
});

byId("cancelColorBtn").addEventListener("click", () => {
  state.selectedWildCardId = null;
  state.selectedWildTargetId = null;
  byId("colorModal").classList.add("hidden");
});

byId("swapPlayerChoices").addEventListener("click", (event) => {
  const choice = event.target.closest("button[data-swap-player-id]");
  const card = state.game?.hand.find((item) => item.id === state.selectedSwapCardId);
  if (!choice || !card) return;
  byId("swapModal").classList.add("hidden");
  state.selectedSwapCardId = null;
  sendSelectedCard(card, choice.dataset.swapPlayerId);
});
byId("cancelSwapBtn").addEventListener("click", () => {
  state.selectedSwapCardId = null;
  byId("swapModal").classList.add("hidden");
});

let draggedCardId = null;
let touchedCard = null;
byId("playerHand").addEventListener("dragstart", (event) => {
  const card = event.target.closest("button.uno-card.playable");
  if (!card) return;
  draggedCardId = card.dataset.cardId;
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData("text/plain", draggedCardId);
  byId("discardPile").classList.add("drop-ready");
});
byId("playerHand").addEventListener("dragend", () => {
  draggedCardId = null;
  byId("discardPile").classList.remove("drop-ready");
});
byId("discardPile").addEventListener("dragover", (event) => {
  if (draggedCardId) event.preventDefault();
});
byId("discardPile").addEventListener("drop", (event) => {
  event.preventDefault();
  const id = draggedCardId || event.dataTransfer.getData("text/plain");
  const card = state.game?.hand.find((item) => item.id === id);
  draggedCardId = null;
  byId("discardPile").classList.remove("drop-ready");
  requestCardPlay(card);
});
byId("playerHand").addEventListener("touchstart", (event) => {
  const card = event.target.closest("button.uno-card.playable");
  if (!card || event.touches.length !== 1) return;
  touchedCard = { id: card.dataset.cardId, x: event.touches[0].clientX, y: event.touches[0].clientY };
}, { passive: true });
byId("playerHand").addEventListener("touchend", (event) => {
  if (!touchedCard || !event.changedTouches.length) return;
  const touch = event.changedTouches[0];
  const vertical = touchedCard.y - touch.clientY;
  const horizontal = Math.abs(touchedCard.x - touch.clientX);
  const id = touchedCard.id;
  touchedCard = null;
  if (vertical > 45 && vertical > horizontal) {
    requestCardPlay(state.game?.hand.find((item) => item.id === id));
  }
}, { passive: true });

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    state.selectedWildCardId = null;
    byId("colorModal").classList.add("hidden");
    byId("challengeRevealPanel").classList.add("hidden");
    byId("swapModal").classList.add("hidden");
    byId("tutorialModal").classList.add("hidden");
    byId("leaveConfirmModal").classList.add("hidden");
    byId("audioSettingsPanel").classList.add("hidden");
    byId("audioSettingsBtn").setAttribute("aria-expanded", "false");
    document.body.dataset.mobilePanel = "";
    return;
  }
  const typing = event.target.matches("input, select, textarea, [contenteditable='true']");
  if (typing || views.game.classList.contains("hidden")) return;
  const cards = [...byId("playerHand").querySelectorAll("button.uno-card")];
  if (["ArrowRight", "ArrowLeft"].includes(event.key) && cards.length) {
    event.preventDefault();
    const currentIndex = cards.indexOf(document.activeElement);
    const direction = event.key === "ArrowRight" ? 1 : -1;
    const nextIndex = currentIndex < 0
      ? (direction > 0 ? 0 : cards.length - 1)
      : (currentIndex + direction + cards.length) % cards.length;
    cards[nextIndex].focus();
  } else if (event.key.toLowerCase() === "d" && !byId("drawPile").disabled) {
    byId("drawPile").click();
  } else if (event.key.toLowerCase() === "u" && !byId("unoBtn").disabled) {
    byId("unoBtn").click();
  } else if (event.key.toLowerCase() === "c" && !byId("catchUnoBtn").classList.contains("hidden")) {
    byId("catchUnoBtn").click();
  } else if (event.key.toLowerCase() === "v") {
    joinVoice();
  } else if (event.key.toLowerCase() === "m" && state.voice.joined) {
    byId("voiceMuteBtn").click();
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
[byId("lobbyChatInput"), byId("gameChatInput")].forEach((input) => {
  input.addEventListener("focus", () => {
    document.body.dataset.chatFocus = "true";
    byId("audioSettingsPanel").classList.add("hidden");
  });
  input.addEventListener("blur", () => {
    window.setTimeout(() => { document.body.dataset.chatFocus = "false"; }, 120);
  });
});
document.querySelectorAll("button[data-reaction]").forEach((button) => {
  button.addEventListener("click", () => send("chatMessage", { text: button.dataset.reaction }));
});

function toggleAudioSettings() {
  const panel = byId("audioSettingsPanel");
  const opening = panel.classList.contains("hidden");
  panel.classList.toggle("hidden", !opening);
  byId("audioSettingsBtn").setAttribute("aria-expanded", String(opening));
  byId("gameSettingsBtn").setAttribute("aria-expanded", String(opening));
}

byId("audioSettingsBtn").addEventListener("click", toggleAudioSettings);
byId("gameSettingsBtn").addEventListener("click", toggleAudioSettings);
byId("closeAudioSettingsBtn").addEventListener("click", () => {
  byId("audioSettingsPanel").classList.add("hidden");
  byId("audioSettingsBtn").setAttribute("aria-expanded", "false");
  byId("gameSettingsBtn").setAttribute("aria-expanded", "false");
});

byId("soundToggle").addEventListener("click", () => {
  state.soundEnabled = !state.soundEnabled;
  localStorage.setItem("uno.sound", state.soundEnabled ? "on" : "off");
  updateAudioControls();
  playSound("notice");
});
byId("effectsVolume").addEventListener("input", (event) => {
  state.effectsVolume = Number(event.target.value) / 100;
  localStorage.setItem("uno.effectsVolume", String(state.effectsVolume));
  updateAudioControls();
});
byId("effectsVolume").addEventListener("change", () => playSound("notice"));
byId("musicToggle").addEventListener("click", () => {
  state.musicEnabled = !state.musicEnabled;
  localStorage.setItem("uno.music", state.musicEnabled ? "on" : "off");
  if (state.musicEnabled) startMusic();
  else stopMusic();
  updateAudioControls();
});
byId("musicVolume").addEventListener("input", (event) => {
  state.musicVolume = Number(event.target.value) / 100;
  localStorage.setItem("uno.musicVolume", String(state.musicVolume));
  updateAudioControls();
});

byId("colorBlindToggle").addEventListener("change", (event) => {
  state.colorSymbols = event.target.checked;
  localStorage.setItem("uno.colorSymbols", state.colorSymbols ? "on" : "off");
  applyAccessibility();
});
byId("highContrastToggle").addEventListener("change", (event) => {
  state.highContrast = event.target.checked;
  localStorage.setItem("uno.highContrast", state.highContrast ? "on" : "off");
  applyAccessibility();
});
byId("reducedMotionToggle").addEventListener("change", (event) => {
  state.reducedMotion = event.target.checked;
  localStorage.setItem("uno.reducedMotion", state.reducedMotion ? "on" : "off");
  applyAccessibility();
});
byId("tutorialBtn").addEventListener("click", () => {
  byId("audioSettingsPanel").classList.add("hidden");
  openTutorial();
});
byId("tutorialBackBtn").addEventListener("click", () => {
  state.tutorialStep = Math.max(0, state.tutorialStep - 1);
  renderTutorial();
});
byId("tutorialNextBtn").addEventListener("click", () => {
  if (state.tutorialStep === TUTORIAL_STEPS.length - 1) {
    byId("tutorialModal").classList.add("hidden");
    localStorage.setItem("uno.tutorialSeen", "yes");
    return;
  }
  state.tutorialStep += 1;
  renderTutorial();
});

byId("voiceJoinBtn").addEventListener("click", joinVoice);
byId("voiceMuteBtn").addEventListener("click", () => {
  state.voice.muted = !state.voice.muted;
  state.voice.stream?.getAudioTracks().forEach((track) => { track.enabled = !state.voice.muted; });
  byId("voiceMuteBtn").textContent = state.voice.muted ? "Unmute" : "Mute";
  if (state.voice.muted && state.voice.speaking) {
    state.voice.speaking = false;
    send("voiceSpeaking", { speaking: false });
  }
});
byId("voiceDeafenBtn").addEventListener("click", () => {
  state.voice.deafened = !state.voice.deafened;
  byId("remoteAudio").querySelectorAll("audio").forEach((audio) => { audio.muted = state.voice.deafened; });
  byId("voiceDeafenBtn").textContent = state.voice.deafened ? "Undeafen" : "Deafen";
});

document.addEventListener("pointerdown", () => {
  if (state.musicEnabled) startMusic();
}, { once: true });
document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopMusic();
  else if (state.musicEnabled) startMusic();
});
updateAudioControls();
applyAccessibility();

const preferredTheme = localStorage.getItem("uno.theme")
  || (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
applyTheme(preferredTheme);
byId("themeToggle").addEventListener("click", () => {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
});

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  state.installPrompt = event;
  byId("installAppBtn").classList.remove("hidden");
});
byId("installAppBtn").addEventListener("click", async () => {
  if (!state.installPrompt) return;
  state.installPrompt.prompt();
  await state.installPrompt.userChoice;
  state.installPrompt = null;
  byId("installAppBtn").classList.add("hidden");
});
window.addEventListener("appinstalled", () => {
  state.installPrompt = null;
  byId("installAppBtn").classList.add("hidden");
  toast("UNO Live is installed and ready.");
});

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}

initializeSocket();
