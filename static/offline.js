"use strict";

const COLORS = ["red", "yellow", "green", "blue"];
const LABELS = { skip: "SKIP", reverse: "REV", draw2: "+2", wild: "WILD", wild4: "+4" };
const $ = (id) => document.getElementById(id);
const game = { deck: [], discard: [], hands: [[], []], turn: 0, color: null, drawn: null, pendingWild: null, over: false, saidUno: false };

function card(color, value) { return { id: crypto.randomUUID(), color, value }; }
function deck() {
  const cards = [];
  COLORS.forEach((color) => {
    cards.push(card(color, "0"));
    for (let n = 1; n <= 9; n += 1) cards.push(card(color, String(n)), card(color, String(n)));
    ["skip", "reverse", "draw2"].forEach((value) => cards.push(card(color, value), card(color, value)));
  });
  for (let n = 0; n < 4; n += 1) cards.push(card("wild", "wild"), card("wild", "wild4"));
  for (let i = cards.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [cards[i], cards[j]] = [cards[j], cards[i]];
  }
  return cards;
}

function canPlay(item) {
  const top = game.discard.at(-1);
  return item.color === "wild" || item.color === game.color || item.value === top.value;
}

function drawOne() {
  if (!game.deck.length) {
    const top = game.discard.pop();
    game.deck = game.discard.splice(0).sort(() => Math.random() - .5);
    game.discard = [top];
  }
  return game.deck.pop();
}

function makeCard(item, interactive = false) {
  const element = document.createElement(interactive ? "button" : "div");
  if (interactive) element.type = "button";
  element.className = `card ${item.color}`;
  const playable = interactive && game.turn === 0 && canPlay(item) && (!game.drawn || game.drawn === item.id) && !game.over;
  if (interactive) {
    element.disabled = !playable;
    element.classList.toggle("playable", playable);
    element.addEventListener("click", () => choosePlay(item));
  }
  const label = LABELS[item.value] || item.value;
  element.innerHTML = `<i>${label}</i><strong>${label}</strong><i>${label}</i>`;
  return element;
}

function status(text) { $("status").textContent = text; }
function render() {
  $("botCount").textContent = `${game.hands[1].length} card${game.hands[1].length === 1 ? "" : "s"}`;
  $("deckCount").textContent = String(game.deck.length);
  $("handCount").textContent = String(game.hands[0].length);
  $("discard").replaceChildren(makeCard(game.discard.at(-1)));
  $("hand").replaceChildren(...game.hands[0].map((item) => makeCard(item, true)));
  $("drawPile").disabled = game.turn !== 0 || Boolean(game.drawn) || game.over;
  $("passBtn").disabled = !game.drawn || game.over;
  $("unoBtn").disabled = game.hands[0].length !== 1 || game.saidUno || game.over;
}

function finish(winner) {
  game.over = true;
  $("winnerText").textContent = winner === 0 ? "You win the practice round!" : "Computer wins this round";
  $("winner").classList.remove("hidden");
  status(winner === 0 ? "Great game!" : "Computer wins");
  render();
}

function bestColor(hand) {
  return COLORS.sort((a, b) => hand.filter((item) => item.color === b).length - hand.filter((item) => item.color === a).length)[0];
}

function applyCard(player, item, chosenColor) {
  game.hands[player].splice(game.hands[player].findIndex((candidate) => candidate.id === item.id), 1);
  game.discard.push(item);
  game.color = item.color === "wild" ? chosenColor : item.color;
  game.drawn = null;
  if (!game.hands[player].length) { finish(player); return; }
  const other = 1 - player;
  if (item.value === "draw2" || item.value === "wild4") {
    const count = item.value === "draw2" ? 2 : 4;
    for (let i = 0; i < count; i += 1) game.hands[other].push(drawOne());
    game.turn = player;
  } else if (item.value === "skip" || item.value === "reverse") {
    game.turn = player;
  } else {
    game.turn = other;
  }
  if (game.turn === 1) window.setTimeout(botTurn, 650);
  else status("Your turn");
  render();
}

function choosePlay(item) {
  if (item.color === "wild") {
    game.pendingWild = item;
    $("colorPicker").classList.remove("hidden");
    return;
  }
  applyCard(0, item);
}

function botChoice(playable) {
  const level = $("difficulty").value;
  if (level === "easy") return playable[Math.floor(Math.random() * playable.length)];
  const score = (item) => ({ draw2: 40, skip: 34, reverse: 30, wild4: level === "hard" ? 5 : 20, wild: level === "hard" ? 2 : 14 }[item.value] ?? Number(item.value));
  return [...playable].sort((a, b) => score(b) - score(a))[0];
}

function botTurn() {
  if (game.over || game.turn !== 1) return;
  status("Computer is thinking…");
  const playable = game.hands[1].filter(canPlay);
  if (playable.length) {
    const choice = botChoice(playable);
    applyCard(1, choice, choice.color === "wild" ? bestColor(game.hands[1]) : null);
    return;
  }
  const drawn = drawOne();
  game.hands[1].push(drawn);
  if (canPlay(drawn)) applyCard(1, drawn, drawn.color === "wild" ? bestColor(game.hands[1]) : null);
  else {
    game.turn = 0;
    status("Your turn");
    render();
  }
}

function newGame() {
  Object.assign(game, { deck: deck(), discard: [], hands: [[], []], turn: 0, color: null, drawn: null, pendingWild: null, over: false, saidUno: false });
  for (let n = 0; n < 7; n += 1) { game.hands[0].push(drawOne()); game.hands[1].push(drawOne()); }
  let first = drawOne();
  while (first.color === "wild" || LABELS[first.value]) { game.deck.unshift(first); first = drawOne(); }
  game.discard.push(first);
  game.color = first.color;
  $("winner").classList.add("hidden");
  status("Your turn");
  render();
}

$("drawPile").addEventListener("click", () => {
  const item = drawOne();
  game.hands[0].push(item);
  game.saidUno = false;
  if (canPlay(item)) { game.drawn = item.id; status("Play the drawn card or keep it"); }
  else { game.turn = 1; status("Computer is thinking…"); window.setTimeout(botTurn, 650); }
  render();
});
$("passBtn").addEventListener("click", () => { game.drawn = null; game.turn = 1; status("Computer is thinking…"); render(); window.setTimeout(botTurn, 650); });
$("unoBtn").addEventListener("click", () => { game.saidUno = true; $("unoBtn").disabled = true; status("UNO called! Your turn continues."); });
$("colorPicker").addEventListener("click", (event) => {
  const choice = event.target.closest("button[data-color]");
  if (!choice || !game.pendingWild) return;
  const item = game.pendingWild; game.pendingWild = null; $("colorPicker").classList.add("hidden"); applyCard(0, item, choice.dataset.color);
});
$("newGame").addEventListener("click", newGame);
$("playAgain").addEventListener("click", newGame);
newGame();
