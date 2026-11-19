"""Exercise the live blueprint with the real account guard and an isolated database."""
import os
import subprocess
import sys
import unittest
from io import BytesIO
from pathlib import Path


class CommunityEvents(unittest.TestCase):
    def test_workflow(self):
        # Flask-SQLAlchemy reads DATABASE_URL at import time. The child keeps
        # the test database isolated from other suites and every local account.
        subprocess.run([sys.executable, __file__, "--isolated"],
                       cwd=Path(__file__).resolve().parents[1],
                       env={**os.environ, "DATABASE_URL": "sqlite:///:memory:"}, check=True)


def run_isolated():
    import jwt
    from PIL import Image
    from __init__ import app, db, login_manager
    from api.community_events import community_events
    from model import persona  # Registers User's existing many-to-many table.
    from model.user import User

    app.config.update(TESTING=True, SECRET_KEY="isolated-testing-secret-only-not-for-deployment")
    app.register_blueprint(community_events)
    login_manager.user_loader(lambda user_id: db.session.get(User, int(user_id)))
    with app.app_context():
        db.create_all()
        for name, uid, role in (("Test Staff", "staff", "Teacher"), ("Test Member", "member", "User"),
                                ("Other Member", "other", "User")):
            db.session.add(User(name, uid, role=role, totp_enabled=False))
        db.session.commit()

    def client(uid=None):
        result = app.test_client()
        if uid:
            token = jwt.encode({"_uid": uid}, app.config["SECRET_KEY"], algorithm="HS256")
            result.set_cookie(app.config["JWT_TOKEN_NAME"], token)
        return result

    staff, member, other, guest = [client(uid) for uid in ("staff", "member", "other", None)]
    headers = {"X-Origin": "client"}

    def create(title):
        response = staff.post("/api/events", headers=headers, json={
            "title": title, "location": "Fixture only", "description": "Synthetic test event",
            "starts_at": "2030-01-15T10:00:00-08:00", "ends_at": "2030-01-15T12:00:00-08:00",
        })
        assert response.status_code == 201, response.get_json()
        return f"/api/events/{response.get_json()['id']}"

    def photo(caption="Fixture frame", content=None, approve="yes"):
        if content is None:
            content = BytesIO()
            Image.new("RGB", (1800, 1000), "navy").save(content, "JPEG")
            content.seek(0)
        return {"image": (content, "original.jpg"), "caption": caption, "alt": "Navy test image",
                "credit": "Test author", "permission": "Private fixture reference", "approved": approve}

    a, b = create("Event A"), create("Event B")
    assert guest.get(a).status_code == 404 and guest.get("/api/events").get_json() == []
    assert guest.get("/api/events/manage").status_code == 401
    assert member.get("/api/events/manage").status_code == 403
    assert member.post(a + "/photos", headers=headers, data=photo()).status_code == 403
    assert staff.post(a + "/photos", data=photo()).status_code == 403
    assert staff.post(a + "/photos", headers=headers, data=photo(content=BytesIO(b"<svg/>"))).status_code == 400
    assert staff.post(a + "/photos", headers=headers, data=photo(approve="no")).status_code == 400
    assert staff.post(a + "/photos", headers=headers, data=photo(content=BytesIO(b"x" * (5 * 1024 * 1024)))).status_code == 413
    assert staff.get(a + "/photos/manage").get_json()["photos"] == []
    first = staff.post(a + "/photos", headers=headers, data=photo()).get_json()
    second = staff.post(a + "/photos", headers=headers, data=photo("Second frame")).get_json()
    unrelated = staff.post(b + "/photos", headers=headers, data=photo("Another event")).get_json()
    assert guest.get(first["image"]).status_code == 404
    staff_image = staff.get(a + "/photos/manage").get_json()["photos"][0]["thumbnail"]
    assert staff.get(staff_image).status_code == 200
    assert member.get(staff_image).status_code == 403
    assert guest.get("/api/events?albums=true").get_json() == []
    assert staff.put(a + "/photos", headers=headers, json={"order": [first["id"], unrelated["id"]]}).status_code == 409
    assert staff.delete(a + f"/photos/{unrelated['id']}", headers=headers).status_code == 404
    for route in (a, b):
        assert staff.put(route + "/state", headers=headers, json={"state": "published"}).status_code == 200
    assert staff.put(a + "/photos", headers=headers,
                     json={"order": [second["id"], first["id"]], "cover_id": second["id"]}).status_code == 200
    public = guest.get(a + "/photos").get_json()
    assert public["cover_id"] == second["id"]
    assert [p["id"] for p in public["photos"]] == [second["id"], first["id"]]
    assert all("permission" not in p for p in public["photos"])
    covers = guest.get("/api/events?albums=true").get_json()
    assert covers[0]["cover"] == public["photos"][0] and covers[1]["cover"] is None
    response = guest.get(first["thumbnail"])
    assert response.mimetype == "image/jpeg"
    assert Image.open(BytesIO(response.data)).width == 640
    assert staff.delete(a + f"/photos/{second['id']}", headers=headers).status_code == 204
    assert guest.get(a + "/photos").get_json()["cover_id"] is None
    assert guest.get("/api/events?albums=true").get_json()[0]["cover"] is None
    assert guest.get(second["image"]).status_code == 404

    path = a + "/comments"
    assert guest.post(path, headers=headers, json={"text": "Anonymous"}).status_code == 401
    assert member.post(path, headers=headers, json={"text": " "}).status_code == 400
    post = member.post(path, headers=headers, json={"text": "<b>Plain text</b>", "author": "Impostor"}).get_json()["id"]
    assert guest.get(path).get_json()["comments"][0]["author"] == "Test Member"
    assert not guest.get(path).get_json()["comments"][0]["can_edit"]
    assert member.get(path + "/member").get_json()["comments"][0]["can_edit"]
    assert other.put(f"{path}/{post}", headers=headers, json={"text": "Not mine"}).status_code == 403
    assert staff.put(f"{path}/{post}", headers=headers, json={"text": "Not mine"}).status_code == 403
    assert member.post(b + "/comments", headers=headers, json={"text": "Cross event", "parent_id": post}).status_code == 400
    assert other.post(path, headers=headers, json={"text": "Reply", "parent_id": post}).status_code == 201
    assert member.put(f"{path}/{post}", headers=headers, json={"text": "Updated"}).status_code == 204
    assert other.delete(f"{path}/{post}", headers=headers).status_code == 403
    assert staff.delete(f"{path}/{post}", headers=headers).status_code == 204
    comments = guest.get(path).get_json()["comments"]
    assert comments[0]["removed"] and comments[0]["text"] == "" and comments[1]["parent_id"] == post
    assert guest.get(b + "/comments").get_json()["comments"] == []
    for index in range(32):
        assert member.post(path, headers=headers, json={"text": f"Page test {index}"}).status_code == 201
    page = guest.get(path).get_json()
    assert len(page["comments"]) == 30 and page["next"]
    assert len(guest.get(f'{path}?after={page["next"]}').get_json()["comments"]) == 4


if __name__ == "__main__":
    if "--isolated" in sys.argv:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        run_isolated()
    else:
        unittest.main()
