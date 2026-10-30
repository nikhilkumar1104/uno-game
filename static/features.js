"use strict";

state.profileToken = localStorage.getItem('uno.profileToken') || '';
state.profile = null;
state.profileReady = false;
let profileReturnFocus = null;
let reconnectTimer = null;

const TABLE_THEMES = [ ['emerald', 'Emerald'], ['ocean', 'Ocean'], ['midnight', 'Midnight'], ['plum', 'Plum'] ];
const CARD_BACKS = [ ['classic', 'Classic'], ['aurora', 'Aurora'], ['carbon', 'Carbon'], ['royal', 'Royal'] ];

function applyPersonalStyle(theme, back, persist = false) {
  const validTheme = TABLE_THEMES.some(([id]) => id === theme) ? theme : 'emerald';
  const validBack = CARD_BACKS.some(([id]) => id === back) ? back : 'classic';
  document.documentElement.dataset.tableTheme = validTheme;
  document.documentElement.dataset.cardBack = validBack;
  if (persist) {
    localStorage.setItem('uno.tableTheme', validTheme);
    localStorage.setItem('uno.cardBack', validBack);
  }
}

function styleChoices(container, choices, name, isBack) {
  byId(container).replaceChildren(...choices.map(([id, title]) => {
    const label = document.createElement('label'); label.className = 'style-choice';
    const input = document.createElement('input'); input.type = 'radio'; input.name = name; input.value = id;
    input.checked = id === (isBack ? document.documentElement.dataset.cardBack : document.documentElement.dataset.tableTheme);
    const swatch = document.createElement('span'); swatch.className = `style-swatch ${id}${isBack ? ' back-preview' : ''}`;
    swatch.setAttribute('aria-hidden', 'true'); if (isBack) swatch.textContent = 'UNO';
    const text = document.createElement('span'); text.textContent = title;
    label.append(input, swatch, text);
    input.addEventListener('change', () => applyPersonalStyle(
      document.querySelector('input[name="tableTheme"]:checked')?.value,
      document.querySelector('input[name="cardBack"]:checked')?.value,
    ));
    return label;
  }));
}

function renderProfile(profile) {
  const previous = state.profile;
  state.profile = profile;
  state.profileReady = true;
  byId('saveProfileBtn').disabled = false;
  applyPersonalStyle(profile.tableTheme, profile.cardBack, true);
  byId('profileStatus').textContent = `${profile.displayName} · ${profile.achievements.filter(a => a.earnedAt).length} / ${profile.achievements.length} achievements unlocked`;
  byId('profileName').value = profile.displayName;
  byId('profileAvatar').value = profile.avatar;
  byId('profileStats').replaceChildren(...[
    [profile.games, 'Rounds'], [profile.wins, 'Wins'], [profile.points, 'Points'], [`${profile.winRate}%`, 'Win rate'],
  ].map(([value, label]) => {
    const tile = document.createElement('div');tile.className = 'profile-stat';
    const strong = document.createElement('strong'); strong.textContent = value;
    const span = document.createElement('span'); span.textContent = label;
    tile.append(strong, span); return tile;
  }));
  const history = profile.history.map(item => {
    const row = document.createElement('div'); row.className = 'profile-history-row';
    const result = document.createElement('span');result.textContent = item.won ? 'Won' : 'Completed';result.className = item.won ? 'round-won' : '';
    const detail = document.createElement('span');detail.textContent = `${item.mode === 'wild' ? 'Wild' : 'Classic'} · ${item.points} points`;
    const date = document.createElement('time'); date.dateTime = item.finishedAt;date.textContent = new Date(item.finishedAt).toLocaleDateString();
    row.append(result, detail, date);return row;
  });
  byId('profileHistory').replaceChildren(...(history.length ? history : [emptyState('Complete a round to start your history.')]));
  byId('achievementGrid').replaceChildren(...profile.achievements.map(achievement => {
    const tile = document.createElement('div');tile.className = `achievement${achievement.earnedAt ? ' earned' : ''}`;
    const icon = document.createElement('span');icon.className = 'achievement-icon';icon.textContent = achievement.icon;icon.setAttribute('aria-hidden', 'true');
    const content = document.createElement('div');
    const title = document.createElement('strong');title.textContent = achievement.title;
    const description = document.createElement('small');description.textContent = achievement.description;
    const status = document.createElement('small');status.textContent = achievement.earnedAt ? 'Unlocked' : `${achievement.progress} / ${achievement.target}`;
    const progress = document.createElement('progress');progress.max = achievement.target; progress.value = achievement.progress;progress.setAttribute('aria-label', achievement.title);
    content.append(title, description, status, progress);tile.append(icon, content);return tile;
  }));
  styleChoices('tableThemeChoices', TABLE_THEMES, 'tableTheme', false);
  styleChoices('cardBackChoices', CARD_BACKS, 'cardBack', true);
  if (previous && previous.id === profile.id) {
    const unlocked = profile.achievements.filter(a => a.earnedAt && !previous.achievements.find(old => old.id === a.id)?.earnedAt);
    if (unlocked.length) toast(`Achievement unlocked: ${unlocked.map(a => a.title).join(', ')}`);
  }
}

function openProfile() {
  profileReturnFocus = document.activeElement;
  byId('audioSettingsPanel').classList.add('hidden');
  byId('profileModal').classList.remove('hidden');
  byId('closeProfileBtn').focus();
  if (state.socket?.connected && state.profileToken) state.socket.emit('getProfile', {profileToken: state.profileToken});
  else if (state.socket?.connected) state.socket.emit('ensureProfile', {displayName: state.username || 'Player', avatar: state.avatar});
  else byId('profileStatus').textContent = 'Connect to the server to load your profile.';
}

function closeProfile() {
  byId('profileModal').classList.add('hidden');
  if (state.profile) applyPersonalStyle(state.profile.tableTheme, state.profile.cardBack);
  profileReturnFocus?.focus();
}

function wireProfileSocket(socket) {
  const bindProfile = () => {
    if (state.profileToken && state.roomCode && state.playerId && !state.rejoinPending) {
      socket.emit('bindProfile', {roomCode: state.roomCode, profileToken: state.profileToken});
    }
  };
  socket.on('roomJoined', bindProfile);
  socket.on('connect', () => {
    state.profileReady = false;
    socket.emit('ensureProfile', {profileToken: state.profileToken || undefined, displayName: state.username || 'Player', avatar: state.avatar});
  });
  socket.on('profileSession', payload => {
    state.profileToken = payload.profileToken;
    localStorage.setItem('uno.profileToken', state.profileToken);
    renderProfile(payload.profile);
    bindProfile();
    if (!state.roomCode) {
      if (!byId('usernameInput').value) byId('usernameInput').value = payload.profile.displayName === 'Player' ? '' : payload.profile.displayName;
      byId('avatarInput').value = payload.profile.avatar;
    }
  });
  socket.on('profileUpdated', renderProfile);
  socket.on('profileSaved', () => {
    closeProfile();
    if (!state.roomCode && state.profile) {
      byId('usernameInput').value = state.profile.displayName;byId('avatarInput').value = state.profile.avatar;
    }
    toast('Profile and table style saved.');
  });
  socket.on('profileExpired', () => {
    state.profileToken = '';state.profileReady = false;localStorage.removeItem('uno.profileToken');
    socket.emit('ensureProfile', {displayName: state.username || 'Player', avatar: state.avatar});
    toast('Your saved profile was unavailable. A new profile has been created.', true);
  });
}

function renderReconnectCountdown(game) {
  window.clearInterval(reconnectTimer);
  const wait = game.reconnectWait;
  byId('reconnectNotice').classList.toggle('hidden', !wait);
  if (!wait) return;
  const clockOffset = game.serverTime * 1000 - Date.now();
  byId('reconnectPlayer').textContent = wait.username;
  const update = () => {
    const seconds = Math.max(0, Math.ceil((wait.deadline * 1000 - Date.now() - clockOffset) / 1000));
    byId('reconnectSeconds').textContent = seconds;
    byId('turnStatus').textContent = seconds ? `Waiting for ${wait.username} · ${seconds}s` : 'Resuming the table…';
    if (!seconds) window.clearInterval(reconnectTimer);
  };
  update();reconnectTimer = window.setInterval(update, 250);
}

document.querySelectorAll('[data-open-profile]').forEach(button => button.addEventListener('click', openProfile));
byId('closeProfileBtn').addEventListener('click', closeProfile);
byId('profileModal').addEventListener('click', event => {if (event.target === byId('profileModal')) closeProfile();});
byId('profileModal').addEventListener('keydown', event => {
  if (event.key === 'Escape') {event.stopPropagation();closeProfile();}
  if (event.key !== 'Tab') return;
  const focusable = [...byId('profileModal').querySelectorAll('button:not(:disabled), input, select')].filter(el => el.getClientRects().length);
  const first = focusable[0], last = focusable.at(-1);
  if (event.shiftKey && document.activeElement === first) {event.preventDefault();last.focus();}
  else if (!event.shiftKey && document.activeElement === last) {event.preventDefault();first.focus();}
});
document.querySelectorAll('[data-profile-tab]').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('[data-profile-tab]').forEach(tab => {tab.classList.toggle('active', tab === button);tab.setAttribute('aria-selected', String(tab === button));});
  document.querySelectorAll('.profile-section').forEach(section => section.classList.toggle('hidden', section.id !== button.dataset.profileTab));
}));
byId('profileForm').addEventListener('submit', event => {
  event.preventDefault();
  if (!state.profileReady || !state.socket?.connected) {toast('Reconnect before saving your profile.', true);return;}
  state.socket.emit('updateProfile', {
    profileToken: state.profileToken, displayName: byId('profileName').value, avatar: byId('profileAvatar').value,
    tableTheme: document.querySelector('input[name="tableTheme"]:checked').value,
    cardBack: document.querySelector('input[name="cardBack"]:checked').value,
  });
});
applyPersonalStyle(localStorage.getItem('uno.tableTheme'), localStorage.getItem('uno.cardBack'));
styleChoices('tableThemeChoices', TABLE_THEMES, 'tableTheme', false);
styleChoices('cardBackChoices', CARD_BACKS, 'cardBack', true);
initializeSocket();
if (state.socket) wireProfileSocket(state.socket);
