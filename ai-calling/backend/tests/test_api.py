"""Run: python -m pytest ai-calling/backend/tests  (or python this file)"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["AI_CALLING_DATA"] = str(Path(tempfile.mkdtemp()) / "data.json")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from dev_main import app  # noqa: E402

c = TestClient(app)


def test_flow():
    ucs = c.get("/ai-calling/usecases").json()
    assert [u["usecaseId"] for u in ucs][0] == "complaint"  # sorted by priority

    new = {**ucs[0], "usecaseId": "refund", "name": "Refund", "priority": 9}
    assert c.post("/ai-calling/usecases", json=new).status_code == 201
    assert c.post("/ai-calling/usecases", json=new).status_code == 409

    ids = ["refund", "order", "complaint", "appointment"]
    assert [u["usecaseId"] for u in c.put("/ai-calling/usecases-order", json=ids).json()] == ids
    assert c.put("/ai-calling/usecases-order", json=["nope"]).status_code == 422

    s0 = c.get("/ai-calling/settings").json()
    assert s0["defaultUsecaseId"] == ""  # no catch-all use case by default
    assert c.put("/ai-calling/settings", json={**s0, "defaultUsecaseId": "appointment"}).status_code == 200
    assert c.delete("/ai-calling/usecases/appointment").status_code == 409  # the default cannot be deleted
    assert c.put("/ai-calling/settings", json={**s0, "defaultUsecaseId": ""}).status_code == 200  # none is allowed
    assert c.delete("/ai-calling/usecases/refund").status_code == 204

    # voice / sound / language validation
    base = {**c.get("/ai-calling/usecases/order").json(), "usecaseId": "v1", "name": "V"}
    assert c.post("/ai-calling/usecases", json={**base, "voice": "aparna_hi_customer"}).status_code == 201
    assert c.post("/ai-calling/usecases", json={**base, "usecaseId": "v2", "voice": "nope"}).status_code == 422
    assert c.post("/ai-calling/usecases", json={**base, "usecaseId": "v3", "voice": "ritu_en_edtech"}).status_code == 422  # en voice on hi use case
    assert c.post("/ai-calling/usecases", json={**base, "usecaseId": "v4", "language": "en", "voice": "ritu_en_edtech"}).status_code == 201
    assert c.post("/ai-calling/usecases", json={**base, "usecaseId": "v5", "backgroundSound": "dubstep"}).status_code == 422
    assert c.post("/ai-calling/usecases", json={**base, "usecaseId": "v6", "channels": []}).status_code == 422
    assert c.post("/ai-calling/usecases", json={**base, "usecaseId": "v7", "gender": "other"}).status_code == 422
    vs = c.get("/ai-calling/voices").json()
    assert len(vs) == 191 and {v["language"] for v in vs} == {"hi", "en", "enhi"} and any(v["recommended"] for v in vs)
    for u in ("v1", "v4"):
        assert c.delete(f"/ai-calling/usecases/{u}").status_code == 204

    s = c.get("/ai-calling/settings").json()
    assert c.put("/ai-calling/settings", json={**s, "defaultUsecaseId": "ghost"}).status_code == 422
    assert c.put("/ai-calling/settings", json={**s, "maxIntentsPerCall": 3}).json()["maxIntentsPerCall"] == 3
    assert len(c.get("/ai-calling/conversations").json()) == 2


if __name__ == "__main__":
    test_flow()
    print("ok")
