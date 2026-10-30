"""Browser-owned player profiles and server-earned achievements."""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timezone

from game_engine import GameRuleError
from models import PlayerStat, db
from rooms import clean_avatar, clean_username
from avatars import avatar_url, photo_url

TABLE_THEMES = ('emerald', 'ocean', 'midnight', 'plum')
CARD_BACKS = ('classic', 'aurora', 'carbon', 'royal')
ACHIEVEMENTS = (
    ('first_round', 'First Deal', 'Complete your first round.', 'games', 1, '♠'),
    ('first_win', 'First Victory', 'Win your first round.', 'wins', 1, '★'),
    ('regular', 'Table Regular', 'Complete 10 rounds.', 'games', 10, '♦'),
    ('five_wins', 'Winning Hand', 'Win 5 rounds.', 'wins', 5, '♛'),
    ('points_500', 'Point Collector', 'Earn 500 total points.', 'points', 500, '◆'),
    ('hat_trick', 'Hat Trick', 'Win 3 rounds in a row.', 'best_streak', 3, 'Ⅲ'),
    ('uno_call', 'Loud and Clear', 'Make a valid UNO declaration.', 'uno_calls', 1, '!'),
    ('catch', 'Sharp Eyes', 'Successfully catch a missed UNO.', 'catches', 1, '◎'),
    ('challenge', 'Bluff Buster', 'Successfully challenge a Wild Draw Four.', 'challenges', 1, '?'),
)


class PlayerProfile(db.Model):
    id = db.Column(db.String(32), primary_key=True)
    token_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(18), nullable=False)
    avatar = db.Column(db.String(12), nullable=False, default='ember')
    table_theme = db.Column(db.String(12), nullable=False, default='emerald')
    card_back = db.Column(db.String(12), nullable=False, default='classic')
    counters_json = db.Column(db.Text, nullable=False, default='{}')
    achievements_json = db.Column(db.Text, nullable=False, default='{}')


class ProfileRound(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    profile_id = db.Column(db.String(32), nullable=False, index=True)
    game_id = db.Column(db.String(32), nullable=False)
    won = db.Column(db.Boolean, nullable=False)
    points = db.Column(db.Integer, nullable=False)
    mode = db.Column(db.String(12), nullable=False)
    finished_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (db.UniqueConstraint('profile_id', 'game_id'),)


def resolve_profile(token) -> PlayerProfile:
    if not isinstance(token, str) or not 32 <= len(token) <= 128:
        raise GameRuleError('Your profile session has expired. Reopen My profile to create a new one.')
    digest = hashlib.sha256(token.encode()).hexdigest()
    profile = db.session.execute(db.select(PlayerProfile).where(PlayerProfile.token_hash == digest)).scalar_one_or_none()
    if not profile:
        raise GameRuleError('Your profile session has expired. Reopen My profile to create a new one.')
    return profile


def create_profile(name='Player', avatar='ember'):
    token = secrets.token_urlsafe(32)
    profile = PlayerProfile(
        id=uuid.uuid4().hex, token_hash=hashlib.sha256(token.encode()).hexdigest(),
        display_name=clean_username(name), avatar=clean_avatar(avatar),
    )
    if profile.avatar == "photo":
        profile.avatar = "ember"
    db.session.add(profile)
    db.session.commit()
    return profile, token


def update_profile(profile, data, *, commit=True, photo_pending=False):
    name = clean_username(data.get('displayName', profile.display_name))
    theme = data.get('tableTheme', profile.table_theme)
    back = data.get('cardBack', profile.card_back)
    if theme not in TABLE_THEMES or back not in CARD_BACKS:
        raise GameRuleError('Choose a supported table theme and card back.')
    avatar = clean_avatar(data.get("avatar", profile.avatar))
    if avatar == "photo" and not photo_pending and not photo_url(profile.id):
        raise GameRuleError("Upload a photo first, or choose a character icon.")
    profile.display_name = name
    profile.avatar = avatar
    profile.table_theme = theme
    profile.card_back = back
    if commit:
        db.session.commit()


def profile_payload(profile):
    stat = db.session.execute(db.select(PlayerStat).where(PlayerStat.player_key == profile.id)).scalar_one_or_none()
    values = {'games': stat.games if stat else 0, 'wins': stat.wins if stat else 0, 'points': stat.points if stat else 0}
    counters = json.loads(profile.counters_json)
    earned = json.loads(profile.achievements_json)
    values.update(counters)
    history = db.session.execute(db.select(ProfileRound).where(ProfileRound.profile_id == profile.id).order_by(ProfileRound.finished_at.desc(), ProfileRound.id.desc()).limit(10)).scalars()
    return {
        'id': profile.id, 'displayName': profile.display_name, 'avatar': profile.avatar,
        'avatarUrl': avatar_url(profile), 'photoUrl': photo_url(profile.id),
        'tableTheme': profile.table_theme, 'cardBack': profile.card_back,
        'games': values['games'], 'wins': values['wins'], 'points': values['points'],
        'winRate': round(100 * values['wins'] / values['games']) if values['games'] else 0,
        'bestStreak': counters.get('best_streak', 0),
        'achievements': [
            {'id': key, 'title': title, 'description': description, 'icon': icon,
             'progress': min(values.get(metric, 0), target), 'target': target, 'earnedAt': earned.get(key)}
            for key, title, description, metric, target, icon in ACHIEVEMENTS
        ],
        'history': [{'won': item.won, 'points': item.points, 'mode': item.mode, 'finishedAt': item.finished_at.isoformat() + 'Z'} for item in history],
    }


def record_profile_results(room):
    """Called in record_match's transaction; never trust client-supplied totals."""
    winner = room['winner']
    for player in room['players']:
        if player['spectator'] or player.get('is_bot') or not player.get('profile_id'):
            continue
        profile = db.session.get(PlayerProfile, player['profile_id'])
        if not profile:
            continue
        stat = db.session.execute(db.select(PlayerStat).where(PlayerStat.player_key == profile.id)).scalar_one()
        won = player['id'] == winner['id'] or (winner.get('isTeam') and player.get('team') == winner.get('team'))
        counters = json.loads(profile.counters_json)
        for key, value in room.get('round_stats', {}).get(player['id'], {}).items():
            if key in {'uno_calls', 'catches', 'challenges'}:
                counters[key] = counters.get(key, 0) + value
        counters['streak'] = counters.get('streak', 0) + 1 if won else 0
        counters['best_streak'] = max(counters.get('best_streak', 0), counters['streak'])
        profile.counters_json = json.dumps(counters)
        values = {**counters, 'games': stat.games, 'wins': stat.wins, 'points': stat.points}
        earned = json.loads(profile.achievements_json)
        now = datetime.now(timezone.utc).isoformat()
        for key, _, _, metric, target, _ in ACHIEVEMENTS:
            if values.get(metric, 0) >= target:
                earned.setdefault(key, now)
        profile.achievements_json = json.dumps(earned)
        db.session.add(ProfileRound(profile_id=profile.id, game_id=room['game_id'], won=won,
                                    points=int(winner['points']) if won else 0, mode=room.get('mode', 'classic')))
