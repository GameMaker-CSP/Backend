"""Public event reads and role-checked community publishing."""
from datetime import datetime, timezone
from io import BytesIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, abort, current_app, g, jsonify, request, send_file
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.dialects.mysql import MEDIUMBLOB
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import HTTPException

from __init__ import db
from api.authorize import token_required

community = Blueprint("community", __name__, url_prefix="/api/community")
staff = token_required(["Admin", "Teacher"])


class Series(db.Model):
    __tablename__ = "uesl_series"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(180), nullable=False)


class Event(db.Model):
    __tablename__ = "uesl_event"
    id = db.Column(db.Integer, primary_key=True)
    series_id = db.Column(db.ForeignKey("uesl_series.id"))
    title = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    location = db.Column(db.String(255), nullable=False)
    timezone = db.Column(db.String(80), nullable=False)
    event_type = db.Column(db.String(80), nullable=False)
    starts_at_utc = db.Column(db.String(20), nullable=False, index=True)
    ends_at_utc = db.Column(db.String(20), nullable=False)
    state = db.Column(db.String(12), nullable=False, default="draft")


class Photo(db.Model):
    __tablename__ = "uesl_event_photo"
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.ForeignKey("uesl_event.id"), nullable=False, index=True)
    caption = db.Column(db.String(300), nullable=False)
    alt = db.Column(db.String(300), nullable=False)
    credit = db.Column(db.String(200), nullable=False)
    position = db.Column(db.Integer, nullable=False, default=0)
    image = db.deferred(db.Column(db.LargeBinary().with_variant(MEDIUMBLOB(), "mysql"), nullable=False))


class Cover(db.Model):
    __tablename__ = "uesl_event_cover"
    event_id = db.Column(db.ForeignKey("uesl_event.id"), primary_key=True)
    photo_id = db.Column(db.ForeignKey("uesl_event_photo.id"), nullable=False)


class Comment(db.Model):
    __tablename__ = "uesl_event_comment"
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.ForeignKey("uesl_event.id"), nullable=False, index=True)
    author_id = db.Column(db.ForeignKey("users.id"), nullable=False)
    author = db.relationship("User")
    parent_id = db.Column(db.ForeignKey("uesl_event_comment.id"))
    body = db.Column(db.Text, nullable=False)
    removed = db.Column(db.Boolean, default=False, nullable=False)
    created = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class Attendance(db.Model):
    __tablename__ = "uesl_event_attendance"
    event_id = db.Column(db.ForeignKey("uesl_event.id"), primary_key=True)
    user_id = db.Column(db.ForeignKey("users.id"), primary_key=True)
    attending = db.Column(db.Boolean, default=False, nullable=False)
    checked_in = db.Column(db.Boolean, default=False, nullable=False)


@community.before_request
def check_browser_origin():
    origin = request.headers.get("Origin")
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and origin:
        if origin != request.host_url.rstrip("/") and origin not in current_app.config["COMMUNITY_ORIGINS"]:
            abort(403, "Origin is not allowed to publish to UESL.")


@community.errorhandler(HTTPException)
def error(exc):
    return jsonify(message=exc.description), exc.code


def fields(obj, names):
    return {key: getattr(obj, key) for key in names.split()}


def event_data(event):
    data = fields(event, "id title description location timezone event_type series_id starts_at_utc ends_at_utc state")
    cover = db.session.get(Cover, event.id)
    data["cover_photo_id"] = cover.photo_id if cover else None
    return data


def visible(event_id):
    return Event.query.filter(Event.id == event_id, Event.state.in_(("published", "cancelled"))).first_or_404()


def string(value, limit):
    if not isinstance(value, str) or not 0 < len(value.strip()) <= limit:
        abort(400, f"Enter text between 1 and {limit} characters.")
    return value.strip()


def body():
    data = request.get_json()
    if not isinstance(data, dict):
        abort(400, "Expected an object.")
    return data


def timestamp(value):
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.utcoffset() is None:
            raise ValueError()
        return date.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, AttributeError, TypeError):
        abort(400, "Supply an ISO date and time with a UTC offset.")


def page(query):
    return query.paginate(page=request.args.get("page", 1, type=int), per_page=50, error_out=False).items


@community.get("/events")
def events():
    query = Event.query.filter(Event.state.in_(("published", "cancelled")))
    for key in ("event_type", "series_id"):
        if request.args.get(key):
            query = query.filter_by(**{key: request.args[key]})
    if request.args.get("from"):
        query = query.filter(Event.ends_at_utc >= timestamp(request.args["from"]))
    if request.args.get("until"):
        query = query.filter(Event.starts_at_utc < timestamp(request.args["until"]))
    return jsonify([event_data(e) for e in page(query.order_by(Event.starts_at_utc, Event.id))])


@community.get("/events/<int:event_id>")
def details(event_id):
    return jsonify(event_data(visible(event_id)))


@community.get("/staff/events")
@staff
def staff_events():
    return jsonify([event_data(e) for e in page(Event.query.order_by(Event.starts_at_utc.desc(), Event.id))])


@community.get("/series")
def series_list():
    return jsonify([fields(s, "id title") for s in Series.query.order_by(Series.title)])


@community.post("/series")
@staff
def new_series():
    series = Series(title=string(body().get("title"), 180))
    db.session.add(series)
    db.session.commit()
    return jsonify(fields(series, "id title")), 201


@community.post("/events")
@community.patch("/events/<int:event_id>")
@staff
def write_event(event_id=None):
    creating = event_id is None
    event = Event() if creating else Event.query.get_or_404(event_id)
    data = body()
    for key, limit in (("title",180), ("description",4000), ("location",255), ("event_type",80), ("timezone",80)):
        if creating or key in data:
            setattr(event, key, string(data.get(key), limit))
    try:
        ZoneInfo(event.timezone)
    except (ValueError, ZoneInfoNotFoundError):
        abort(400, "Choose a valid timezone.")
    for key in ("starts_at_utc", "ends_at_utc"):
        if creating or key in data:
            setattr(event, key, timestamp(data.get(key)))
    if event.ends_at_utc <= event.starts_at_utc:
        abort(400, "End time must follow the start.")
    if "series_id" in data:
        if data["series_id"] is not None and (type(data["series_id"]) is not int or not db.session.get(Series, data["series_id"])):
            abort(400, "Unknown event series.")
        event.series_id = data["series_id"]
    state = data.get("state", event.state or "draft")
    if state not in ("draft", "published", "cancelled"):
        abort(400, "Choose draft, published or cancelled.")
    event.state = state
    db.session.add(event)
    db.session.commit()
    return jsonify(event_data(event)), 201 if creating else 200


def photo_data(photo):
    return dict(**fields(photo, "id event_id caption alt credit position"), image=f"/api/community/photos/{photo.id}/image")


def photo_listing(query, staff_view=False):
    if "event_id" in request.args:
        query = query.filter(Photo.event_id == request.args["event_id"])
    records = []
    for photo in page(query.order_by(Photo.position, Photo.id)):
        record = photo_data(photo)
        if staff_view:
            record["image"] = f"/api/community/staff/photos/{photo.id}/image"
        records.append(record)
    return jsonify(records)


@community.get("/photos")
def photos():
    return photo_listing(Photo.query.join(Event).filter(Event.state.in_(("published", "cancelled"))))


@community.get("/staff/photos")
@staff
def staff_photos():
    return photo_listing(Photo.query, staff_view=True)


@community.get("/photos/<int:photo_id>/image")
def image(photo_id):
    photo = Photo.query.get_or_404(photo_id)
    visible(photo.event_id)
    return send_file(BytesIO(photo.image), mimetype="image/jpeg")


@community.get("/staff/photos/<int:photo_id>/image")
@staff
def staff_image(photo_id):
    photo = Photo.query.get_or_404(photo_id)
    response = send_file(BytesIO(photo.image), mimetype="image/jpeg")
    response.cache_control.no_store = True
    return response


@community.post("/events/<int:event_id>/photos")
@staff
def upload(event_id):
    Event.query.get_or_404(event_id)
    photo = Photo(event_id=event_id, caption=string(request.form.get("caption"),300),
                  alt=string(request.form.get("alt"),300), credit=string(request.form.get("credit"),200))
    if request.form.get("permission") != "yes":
        abort(400, "Confirm permission to publish the photo.")
    file = request.files.get("image")
    if not file:
        abort(400, "Choose a JPEG, PNG or WebP photo.")
    try:
        with Image.open(file.stream, formats=("JPEG","PNG","WEBP")) as incoming:
            if incoming.width * incoming.height > 16000000:
                abort(400, "Photo exceeds 16 megapixels.")
            clean = ImageOps.exif_transpose(incoming).convert("RGB")
            clean.thumbnail((1800,1800))
            result = BytesIO()
            clean.save(result, "JPEG", quality=85)
            photo.image = result.getvalue()
    except (OSError, ValueError, Image.DecompressionBombError, UnidentifiedImageError):
        abort(400, "Cannot read this image.")
    db.session.add(photo)
    db.session.commit()
    return jsonify(photo_data(photo)), 201


@community.route("/photos/<int:photo_id>", methods=["PATCH","DELETE"])
@staff
def manage_photo(photo_id):
    photo = Photo.query.get_or_404(photo_id)
    if request.method == "DELETE":
        Cover.query.filter_by(photo_id=photo.id).delete()
        db.session.delete(photo)
        db.session.commit()
        return "", 204
    data = body()
    for key, limit in (("caption",300),("alt",300),("credit",200)):
        if key in data:
            setattr(photo, key, string(data[key],limit))
    if "cover" in data:
        if type(data["cover"]) is not bool:
            abort(400, "Cover must be true or false.")
        if data["cover"]:
            db.session.merge(Cover(event_id=photo.event_id, photo_id=photo.id))
        else:
            Cover.query.filter_by(photo_id=photo.id).delete()
    if "position" in data:
        if type(data["position"]) is not int or not 0 <= data["position"] <= 100000:
            abort(400, "Choose a valid display position.")
        photo.position = data["position"]
    db.session.commit()
    return jsonify(photo_data(photo))


@community.get("/events/<int:event_id>/comments")
def comments(event_id):
    visible(event_id)
    return jsonify([dict(**fields(c, "id parent_id"), author=c.author.name if not c.removed else None,
                         body=c.body if not c.removed else None, created=c.created.isoformat()+"Z")
                    for c in page(Comment.query.filter_by(event_id=event_id).order_by(Comment.id))])


@community.post("/events/<int:event_id>/comments")
@community.route("/events/<int:event_id>/comments/<int:comment_id>",methods=["PATCH","DELETE"])
@token_required()
def write_comment(event_id, comment_id=None):
    visible(event_id)
    user = g.current_user
    if comment_id is None:
        data = body()
        parent = data.get("parent_id")
        if parent is not None and (type(parent) is not int or not Comment.query.filter_by(id=parent,event_id=event_id,removed=False).first()):
            abort(400,"Choose a reply in this event.")
        comment = Comment(event_id=event_id,author_id=user.id,parent_id=parent,body=string(data.get("body"),2000))
        db.session.add(comment)
    else:
        comment = Comment.query.filter_by(id=comment_id,event_id=event_id).first_or_404()
        if comment.author_id != user.id and not (request.method=="DELETE" and user.is_staff()):
            abort(403)
        if request.method == "DELETE":
            comment.removed = True
            comment.body = ""
        elif not comment.removed:
            comment.body = string(body().get("body"),2000)
        else:
            abort(409)
    db.session.commit()
    return jsonify(id=comment.id), 201 if comment_id is None else 200


@community.put("/events/<int:event_id>/rsvp")
@token_required()
def rsvp(event_id):
    event = visible(event_id)
    attending = body().get("attending")
    if type(attending) is not bool:
        abort(400, "Choose whether you are attending.")
    if attending and event.state != "published":
        abort(409, "This event is cancelled.")
    key = (event_id, g.current_user.id)
    row = db.session.get(Attendance,key)
    if not row:
        row = Attendance(event_id=event_id,user_id=key[1])
        db.session.add(row)
    row.attending = attending
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        row = db.session.get(Attendance,key)
        if not row:
            raise
        row.attending = attending
        db.session.commit()
    return jsonify(fields(row,"event_id attending checked_in"))


@community.get("/my-events")
@token_required()
def my_events():
    return jsonify([dict(event=event_data(e), **fields(a,"attending checked_in"))
                    for a,e in db.session.query(Attendance,Event).join(Event).filter(
                        Attendance.user_id==g.current_user.id, Event.state!="draft",
                        db.or_(Attendance.attending,Attendance.checked_in)).order_by(Event.starts_at_utc.desc())])


@community.get("/events/<int:event_id>/roster")
@staff
def roster(event_id):
    Event.query.get_or_404(event_id)
    from model.user import User
    return jsonify([dict(name=u.name, **fields(a,"user_id attending checked_in"))
                    for a,u in db.session.query(Attendance,User).join(User).filter(Attendance.event_id==event_id)])


@community.put("/events/<int:event_id>/roster/<int:user_id>")
@staff
def check_in(event_id,user_id):
    value = body().get("checked_in")
    if type(value) is not bool:
        abort(400)
    row = Attendance.query.filter_by(event_id=event_id,user_id=user_id).first_or_404()
    row.checked_in = value
    db.session.commit()
    return jsonify(fields(row,"attending checked_in"))
