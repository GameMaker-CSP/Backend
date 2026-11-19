"""Event albums and discussions for the UESL site."""
from datetime import datetime, timezone
from io import BytesIO
from uuid import uuid4

from flask import Blueprint, g, jsonify, request, send_file, url_for
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.dialects.mysql import MEDIUMBLOB

from __init__ import db
from api.authorize import auth_required
from model import account_types


community_events = Blueprint("community_events", __name__, url_prefix="/api/events")
STAFF = account_types.staff_roles()
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_PIXELS = 24_000_000


class EventSeries(db.Model):
    __tablename__ = "uesl_series"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(160), nullable=False)


class Event(db.Model):
    __tablename__ = "uesl_event"
    __table_args__ = (db.Index("uesl_event_calendar", "state", "starts_at_utc", "id"),)
    id = db.Column(db.Integer, primary_key=True)
    series_id = db.Column(db.Integer, db.ForeignKey("uesl_series.id"))
    event_type = db.Column(db.String(40), nullable=False, default="gathering")
    title = db.Column(db.String(160), nullable=False)
    location = db.Column(db.String(200), nullable=False)
    timezone = db.Column(db.String(50), nullable=False, default="America/Los_Angeles")
    starts_at_utc = db.Column(db.String(30), nullable=False)
    ends_at_utc = db.Column(db.String(30), nullable=False)
    state = db.Column(db.String(12), nullable=False, default="draft")


class EventDetails(db.Model):
    __tablename__ = "uesl_event_details"
    event_id = db.Column(db.Integer, db.ForeignKey("uesl_event.id"), primary_key=True)
    description = db.Column(db.Text, nullable=False, default="")
    cover_id = db.Column(db.String(32), db.ForeignKey("uesl_event_photo.id"))


class EventPhoto(db.Model):
    __tablename__ = "uesl_event_photo"
    id = db.Column(db.String(32), primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("uesl_event.id"), nullable=False, index=True)
    caption = db.Column(db.String(400), nullable=False)
    alt = db.Column(db.String(240), nullable=False)
    credit = db.Column(db.String(160), nullable=False)
    permission = db.Column(db.String(400), nullable=False)
    position = db.Column(db.Integer, nullable=False)
    width = db.Column(db.Integer, nullable=False)
    height = db.Column(db.Integer, nullable=False)
    image = db.deferred(db.Column(db.LargeBinary().with_variant(MEDIUMBLOB(), "mysql"), nullable=False))
    thumbnail = db.deferred(db.Column(db.LargeBinary().with_variant(MEDIUMBLOB(), "mysql"), nullable=False))


class EventComment(db.Model):
    __tablename__ = "uesl_event_comment"
    __table_args__ = (db.Index("uesl_event_comment_page", "event_id", "id"),)
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("uesl_event.id"), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey("uesl_event_comment.id"))
    author_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    author = db.Column(db.String(160), nullable=False)
    text = db.Column(db.String(1000), nullable=False)
    created_at = db.Column(db.String(30), nullable=False)
    edited = db.Column(db.Boolean, nullable=False, default=False)
    removed = db.Column(db.Boolean, nullable=False, default=False)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def error(message, status=400):
    return {"message": message}, status


@community_events.before_request
def require_api_header():
    # In particular, a cross-origin HTML form must not be able to submit a
    # credentialed multipart upload without passing the existing CORS policy.
    if request.method in {"POST", "PUT", "DELETE"} and request.headers.get("X-Origin") != "client":
        return error("Missing API request header.", 403)


@community_events.errorhandler(413)
def too_large(_):
    return error("Use a JPEG, PNG, or WebP no larger than 4 MB.", 413)


def event_for(event_id, staff=False):
    event = db.session.get(Event, event_id)
    return event if event and (staff or event.state != "draft") else None


def detail_for(event_id):
    return db.session.get(EventDetails, event_id)


def photo_path(photo, size):
    return url_for(".image", event_id=photo.event_id, photo_id=photo.id, size=size)


def photo_data(photo):
    return {
        "id": photo.id, "caption": photo.caption, "alt": photo.alt, "credit": photo.credit,
        "width": photo.width, "height": photo.height,
        "thumbnail": photo_path(photo, "thumb"), "image": photo_path(photo, "view"),
    }


def event_data(event, include_description=False):
    result = {key: getattr(event, key) for key in
              ("id", "title", "location", "timezone", "starts_at_utc", "ends_at_utc", "state")}
    if include_description:
        detail = detail_for(event.id)
        result["description"] = detail.description if detail else ""
    return result


def event_list(staff=False):
    query = Event.query
    albums = request.args.get("albums") == "true"
    if not staff:
        query = query.filter(Event.state != "draft")
    if request.args.get("upcoming") == "true":
        query = query.filter(Event.state == "published", Event.ends_at_utc >= now())
    if albums:
        query = query.filter(db.exists().where(EventPhoto.event_id == Event.id))
    limit = max(1, min(request.args.get("limit", 50, type=int), 100))
    offset = max(0, request.args.get("offset", 0, type=int))
    events = query.order_by(Event.starts_at_utc, Event.id).offset(offset).limit(limit).all()
    result = [event_data(event) for event in events]
    if albums and not staff and result:
        # Load only the selected covers for this page; image bytes remain deferred.
        covers = {photo.event_id: photo_data(photo) for photo in EventPhoto.query
                  .join(EventDetails, (EventDetails.cover_id == EventPhoto.id)
                        & (EventDetails.event_id == EventPhoto.event_id))
                  .filter(EventPhoto.event_id.in_([event.id for event in events]))}
        for entry in result:
            entry["cover"] = covers.get(entry["id"])
    return jsonify(result)


@community_events.get("")
def events():
    return event_list()


@community_events.get("/manage")
@auth_required(STAFF)
def staff_events():
    return event_list(staff=True)


@community_events.post("")
@auth_required(STAFF)
def create_event():
    data = request.get_json(silent=True)
    try:
        title = required(data, "title", 160)
        location = required(data, "location", 200)
        description = required(data, "description", 2000)
        raw = [required(data, field, 40) for field in ("starts_at", "ends_at")]
        dates = [datetime.fromisoformat(value) for value in raw]
        if not all(value.utcoffset() is not None for value in dates) or dates[1] <= dates[0]:
            raise ValueError("Provide time zone-aware dates, with the end after the start.")
        start, end = [value.astimezone(timezone.utc).isoformat(timespec="seconds") for value in dates]
    except (ValueError, OverflowError) as exc:
        return error(str(exc))
    state = data.get("state", "draft")
    if state not in ("draft", "published"):
        return error("Choose draft or published.")
    event = Event(title=title, location=location, starts_at_utc=start, ends_at_utc=end, state=state)
    db.session.add(event)
    db.session.flush()
    db.session.add(EventDetails(event_id=event.id, description=description))
    db.session.commit()
    return event_data(event, True), 201


@community_events.put("/<int:event_id>/state")
@auth_required(STAFF)
def event_state(event_id):
    event = event_for(event_id, staff=True)
    if not event:
        return error("Event not found.", 404)
    data = request.get_json(silent=True)
    state = data.get("state") if isinstance(data, dict) else None
    if state not in ("published", "cancelled"):
        return error("Choose published or cancelled.")
    event.state = state
    db.session.commit()
    return event_data(event)


@community_events.get("/<int:event_id>")
def event_detail(event_id):
    event = event_for(event_id)
    return event_data(event, True) if event else error("Event not found.", 404)


def album(event):
    detail = detail_for(event.id)
    photos = EventPhoto.query.filter_by(event_id=event.id).order_by(EventPhoto.position, EventPhoto.id).all()
    cover_id = detail.cover_id if detail else None
    return {"cover_id": cover_id, "photos": [photo_data(photo) for photo in photos]}


@community_events.get("/<int:event_id>/photos")
def public_photos(event_id):
    event = event_for(event_id)
    return album(event) if event else error("Event not found.", 404)


@community_events.get("/<int:event_id>/photos/manage")
@auth_required(STAFF)
def staff_photos(event_id):
    event = event_for(event_id, staff=True)
    if not event:
        return error("Event not found.", 404)
    result = album(event)
    notes = dict(db.session.query(EventPhoto.id, EventPhoto.permission).filter_by(event_id=event_id).all())
    for photo in result["photos"]:
        photo["permission"] = notes[photo["id"]]
        if event.state == "draft":
            for key, size in (("thumbnail", "thumb"), ("image", "view")):
                photo[key] = url_for(".staff_image", event_id=event_id, photo_id=photo["id"], size=size)
    return result


def required(data, key, limit):
    value = data.get(key) if hasattr(data, "get") else None
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError(f"{key.replace('_', ' ').capitalize()} is required (maximum {limit} characters).")
    return value.strip()


def encode_photo(upload):
    if not upload:
        raise ValueError("Choose an image file.")
    raw = upload.stream.read(MAX_IMAGE_BYTES + 1)
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("The image must be 4 MB or smaller.")
    try:
        with Image.open(BytesIO(raw), formats=["JPEG", "PNG", "WEBP"]) as original:
            if original.width * original.height > MAX_PIXELS or getattr(original, "is_animated", False):
                raise ValueError("Use a still image up to 24 megapixels.")
            original.load()
            image = ImageOps.exif_transpose(original).convert("RGB")
            image.info.clear()  # Do not republish camera, location, or author metadata.
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError) as exc:
        raise ValueError("The file is not a readable JPEG, PNG, or WebP.") from exc
    image.thumbnail((1600, 1600))
    width, height = image.size
    full, thumb = BytesIO(), BytesIO()
    image.save(full, "JPEG", quality=86)
    image.thumbnail((640, 640))
    image.save(thumb, "JPEG", quality=82)
    return full.getvalue(), thumb.getvalue(), width, height


@community_events.post("/<int:event_id>/photos")
@auth_required(STAFF)
def upload_photo(event_id):
    event = event_for(event_id, staff=True)
    if not event:
        return error("Event not found.", 404)
    if EventPhoto.query.filter_by(event_id=event_id).count() >= 40:
        return error("An event can have up to 40 photos.")
    try:
        metadata = {key: required(request.form, key, length) for key, length in
                    (("caption", 400), ("alt", 240), ("credit", 160), ("permission", 400))}
        if request.form.get("approved") != "yes":
            raise ValueError("Confirm that this photo is approved for public display.")
        full, thumb, width, height = encode_photo(request.files.get("image"))
    except ValueError as exc:
        return error(str(exc))
    position = db.session.query(db.func.max(EventPhoto.position)).filter_by(event_id=event_id).scalar() or 0
    photo = EventPhoto(id=uuid4().hex, event_id=event_id, position=position + 1,
                       image=full, thumbnail=thumb, width=width, height=height, **metadata)
    db.session.add(photo)
    db.session.commit()
    return photo_data(photo), 201


@community_events.put("/<int:event_id>/photos")
@auth_required(STAFF)
def arrange_photos(event_id):
    event = event_for(event_id, staff=True)
    if not event:
        return error("Event not found.", 404)
    data = request.get_json(silent=True)
    order = data.get("order") if isinstance(data, dict) else None
    photos = {photo.id: photo for photo in EventPhoto.query.filter_by(event_id=event_id)}
    cover = data.get("cover_id") if isinstance(data, dict) else None
    if (not isinstance(order, list) or not all(isinstance(item, str) for item in order)
            or len(order) != len(photos) or set(order) != set(photos)
            or (cover is not None and (not isinstance(cover, str) or cover not in photos))):
        return error("Gallery changed or contains another event’s photo. Reload and try again.", 409)
    for position, photo_id in enumerate(order, 1):
        photos[photo_id].position = position
    detail = detail_for(event.id)
    if not detail:
        detail = EventDetails(event_id=event.id, description="")
        db.session.add(detail)
    detail.cover_id = cover
    db.session.commit()
    return album(event)


@community_events.delete("/<int:event_id>/photos/<photo_id>")
@auth_required(STAFF)
def delete_photo(event_id, photo_id):
    photo = EventPhoto.query.filter_by(event_id=event_id, id=photo_id).first()
    if not photo:
        return error("Photo not found.", 404)
    detail = detail_for(event_id)
    if detail and detail.cover_id == photo_id:
        detail.cover_id = None
        db.session.flush()
    db.session.delete(photo)
    db.session.commit()
    return "", 204


@community_events.get("/<int:event_id>/photos/<photo_id>/<size>")
def image(event_id, photo_id, size):
    return send_photo(event_id, photo_id, size)


@community_events.get("/<int:event_id>/photos/<photo_id>/manage/<size>")
@auth_required(STAFF)
def staff_image(event_id, photo_id, size):
    return send_photo(event_id, photo_id, size, staff=True)


def send_photo(event_id, photo_id, size, staff=False):
    photo = EventPhoto.query.filter_by(event_id=event_id, id=photo_id).first() if event_for(event_id, staff) else None
    if not photo or size not in {"thumb", "view"}:
        return error("Photo not found.", 404)
    response = send_file(BytesIO(photo.thumbnail if size == "thumb" else photo.image),
                         mimetype="image/jpeg", max_age=0)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@community_events.after_request
def protect_private_responses(response):
    if "/manage" in request.path or request.path.endswith("/member"):
        response.headers["Cache-Control"] = "private, no-store"
    return response


def comment_page(event_id, viewer=None):
    if not event_for(event_id):
        return error("Event not found.", 404)
    cursor = max(0, request.args.get("after", 0, type=int))
    comments = EventComment.query.filter_by(event_id=event_id).filter(EventComment.id > cursor) \
        .order_by(EventComment.id).limit(31).all()
    more = len(comments) > 30
    items = []
    for comment in comments[:30]:
        own = bool(viewer and viewer.id == comment.author_id)
        staff = bool(viewer and viewer.role in STAFF)
        items.append({
            "id": comment.id, "parent_id": comment.parent_id, "created_at": comment.created_at,
            "author": "" if comment.removed else comment.author, "text": comment.text,
            "removed": comment.removed, "edited": comment.edited,
            "can_edit": own and not comment.removed, "can_remove": (own or staff) and not comment.removed,
        })
    return {"comments": items, "next": items[-1]["id"] if more else None}


@community_events.get("/<int:event_id>/comments")
def comments(event_id):
    return comment_page(event_id)


@community_events.get("/<int:event_id>/comments/member")
@auth_required()
def member_comments(event_id):
    return comment_page(event_id, g.current_user)


@community_events.post("/<int:event_id>/comments")
@auth_required()
def post_comment(event_id):
    if not event_for(event_id):
        return error("Event not found.", 404)
    data = request.get_json(silent=True)
    try:
        text = required(data, "text", 1000)
    except ValueError as exc:
        return error(str(exc))
    parent = data.get("parent_id")
    if parent is not None and (type(parent) is not int or not EventComment.query.filter_by(
            event_id=event_id, id=parent, parent_id=None, removed=False).first()):
        return error("Choose a current comment in this event to reply to.")
    user = g.current_user
    comment = EventComment(event_id=event_id, parent_id=parent, author_id=user.id,
                           author=user.name[:160], text=text, created_at=now())
    db.session.add(comment)
    db.session.commit()
    return {"id": comment.id}, 201


@community_events.route("/<int:event_id>/comments/<int:comment_id>", methods=["PUT", "DELETE"])
@auth_required()
def change_comment(event_id, comment_id):
    if not event_for(event_id):
        return error("Event not found.", 404)
    comment = EventComment.query.filter_by(event_id=event_id, id=comment_id, removed=False).first()
    if not comment:
        return error("Comment not found.", 404)
    owner = comment.author_id == g.current_user.id
    if request.method == "PUT":
        if not owner:
            return error("Only the author can edit this comment.", 403)
        try:
            comment.text = required(request.get_json(silent=True), "text", 1000)
        except ValueError as exc:
            return error(str(exc))
        comment.edited = True
    else:
        if not owner and g.current_user.role not in STAFF:
            return error("Only the author or staff can remove this comment.", 403)
        comment.removed, comment.text = True, ""
    db.session.commit()
    return "", 204
