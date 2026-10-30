"""Fixed character avatars and bounded, decoded profile photos."""
from __future__ import annotations

import io
import uuid
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from game_engine import GameRuleError
from models import db

CHARACTER_AVATARS = ('robot', 'astronaut', 'ninja', 'fox', 'cat', 'owl', 'panda', 'alien')
INITIAL_AVATARS = ('ember', 'bolt', 'wave', 'leaf', 'star', 'nova')


class ProfilePhoto(db.Model):
    profile_id = db.Column(db.String(32), primary_key=True)
    image_id = db.Column(db.String(32), unique=True, nullable=False, index=True)
    content = db.Column(db.LargeBinary, nullable=False)


def photo_url(profile_id):
    photo = db.session.get(ProfilePhoto, profile_id)
    return f'/avatars/{photo.image_id}.jpg' if photo else None


def avatar_url(profile):
    if profile.avatar == 'photo':
        return photo_url(profile.id)
    if profile.avatar in CHARACTER_AVATARS:
        return f'/static/avatars/{profile.avatar}.svg'
    return None


def normalize_photo(content):
    if not content or len(content) > 2 * 1024 * 1024:
        raise GameRuleError('Choose a JPEG, PNG, or WebP photo under 2 MB after resizing.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if image.format not in {'JPEG', 'PNG', 'WEBP'} or getattr(image, 'n_frames', 1) != 1:
                    raise GameRuleError('Choose a still JPEG, PNG, or WebP photo.')
                if image.width * image.height > 16_000_000:
                    raise GameRuleError('This photo is too large. Choose an image under 16 megapixels.')
                image = ImageOps.exif_transpose(image)
                image = ImageOps.fit(image.convert('RGBA'), (256, 256), method=Image.Resampling.LANCZOS)
                flattened = Image.new('RGB', image.size, 'white')
                flattened.paste(image, mask=image.getchannel('A'))
                output = io.BytesIO()
                flattened.save(output, format='JPEG', quality=85, optimize=True)
                return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        if isinstance(exc, GameRuleError):
            raise
        raise GameRuleError('That file could not be read as a photo. Choose a JPEG, PNG, or WebP image.') from exc


def save_photo(profile, content):
    photo = db.session.get(ProfilePhoto, profile.id)
    if not photo:
        photo = ProfilePhoto(profile_id=profile.id)
        db.session.add(photo)
    photo.image_id = uuid.uuid4().hex
    photo.content = normalize_photo(content)
    profile.avatar = 'photo'
    db.session.commit()
