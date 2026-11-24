"""HTTP-API per TestClient. Settings, Uploads und Outputs liegen in tmp_path, keine echten Projektdaten."""
import json
import os
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import kursliste, main, runner, settings as st, workflow
from app.jobs import JobManager


@pytest.fixture
def env(tmp_path, monkeypatch):
    repo = tmp_path / "tool"
    repo.mkdir()
    upload = tmp_path / "work" / "uploads"
    output = tmp_path / "output"
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"tool_repo": str(repo), "flex_token": "", "flex_queries": []}),
                             encoding="utf-8")
    monkeypatch.setattr(st, "SETTINGS_FILE", settings_file)
    monkeypatch.setattr(st, "UPLOAD_DIR", upload)
    monkeypatch.setattr(st, "OUTPUT_DIR", output)
    monkeypatch.setattr(main, "jobs", JobManager())
    assert settings_file.parent == tmp_path
    return {"client": TestClient(main.app), "tmp": tmp_path, "repo": repo,
            "upload": upload, "output": output, "settings": settings_file}


def _write_settings(env, **extra):
    data = {"tool_repo": str(env["repo"]), "flex_token": "", "flex_queries": []}
    data.update(extra)
    env["settings"].write_text(json.dumps(data), encoding="utf-8")


def test_status_without_venv(env):
    body = env["client"].get("/api/status").json()
    assert body["repo_exists"] is True
    assert body["python_exists"] is False
    assert body["ok"] is False
    assert body["running_job"] is None
    assert body["default_year"] < 2100


def test_status_with_faked_venv(env, monkeypatch):
    py = runner.tool_python(env["repo"])
    py.parent.mkdir(parents=True)
    py.write_bytes(b"")
    monkeypatch.setattr(runner, "run_capture", lambda cmd, cwd, timeout=60: (0, "ok"))
    body = env["client"].get("/api/status").json()
    assert body["ok"] is True and body["importable"] is True


def test_kursliste_without_venv_lists_years_none(env):
    body = env["client"].get("/api/kursliste").json()
    assert body["directory"] is None
    years = {y["year"]: y["state"] for y in body["years"]}
    assert years[2020] == "none"
    assert all(state == "none" for state in years.values())


def test_kursliste_with_directory_reports_state(env, monkeypatch):
    kdir = env["tmp"] / "kl"
    kdir.mkdir()
    (kdir / "kursliste_2025.xml").write_bytes(b"<k/>")
    (kdir / "kursliste_2025.sqlite").write_bytes(b"sq")
    py = runner.tool_python(env["repo"])
    py.parent.mkdir(parents=True)
    py.write_bytes(b"")
    monkeypatch.setattr(kursliste, "resolve_dir", lambda repo: kdir)
    body = env["client"].get("/api/kursliste").json()
    assert body["directory"] == str(kdir)
    years = {y["year"]: y["state"] for y in body["years"]}
    assert years[2025] == "xml+sqlite"


def test_upload_xml_and_list_and_delete(env):
    c = env["client"]
    r = c.post("/api/upload", files=[("files", ("konto.xml", b"<FlexQueryResponse/>", "application/xml"))])
    assert r.status_code == 200 and r.json()["saved"] == ["konto.xml"]
    assert (env["upload"] / "konto.xml").read_bytes() == b"<FlexQueryResponse/>"
    files = c.get("/api/files").json()
    assert [f["name"] for f in files] == ["konto.xml"]
    d = c.delete("/api/files/konto.xml")
    assert d.status_code == 200
    assert not (env["upload"] / "konto.xml").exists()
    assert c.delete("/api/files/konto.xml").status_code == 404


def test_upload_rejects_non_xml(env):
    r = env["client"].post("/api/upload", files=[("files", ("notiz.txt", b"hallo", "text/plain"))])
    assert r.status_code == 400
    assert not env["upload"].exists() or not any(env["upload"].iterdir())


def test_upload_rejects_pdf_disguised_name(env):
    r = env["client"].post("/api/upload", files=[("files", ("auszug.pdf", b"%PDF-1.4", "application/pdf"))])
    assert r.status_code == 400


def test_upload_traversal_name_is_sanitized_and_stays_inside(env):
    outside = env["tmp"] / "evil.xml"
    r = env["client"].post("/api/upload", files=[
        ("files", ("..\\..\\evil.xml", b"<FlexQueryResponse/>", "application/xml"))])
    assert r.status_code == 200
    assert not outside.exists()
    saved = r.json()["saved"]
    assert saved == ["evil.xml"]
    assert (env["upload"] / "evil.xml").is_file()


def test_upload_traversal_non_xml_rejected(env):
    r = env["client"].post("/api/upload", files=[
        ("files", ("..\\..\\..\\evil.txt", b"x", "text/plain"))])
    assert r.status_code == 400
    assert not (env["tmp"] / "evil.txt").exists()


@pytest.mark.parametrize("name", ["..%5C..%5Coutside.xml", "..%5Coutside.xml", "%2E%2E"])
def test_delete_traversal_rejected(env, name):
    outside = env["tmp"] / "outside.xml"
    outside.write_text("<keep/>", encoding="utf-8")
    env["upload"].mkdir(parents=True, exist_ok=True)
    r = env["client"].delete(f"/api/files/{name}")
    assert 400 <= r.status_code < 500
    assert outside.read_text(encoding="utf-8") == "<keep/>"


def test_output_download_valid(env):
    d = env["output"] / "2025"
    d.mkdir(parents=True)
    (d / "Steuerauszug_X_2025.pdf").write_bytes(b"%PDF-1.4 test")
    r = env["client"].get("/api/output/2025/Steuerauszug_X_2025.pdf")
    assert r.status_code == 200 and r.content == b"%PDF-1.4 test"


@pytest.mark.parametrize("name", ["..%5C..%5Csecret.txt", "..%5Csecret.txt", "..", "%2E%2E%2Fsecret.txt"])
def test_output_traversal_is_4xx(env, name):
    (env["tmp"] / "secret.txt").write_text("geheim", encoding="utf-8")
    d = env["output"] / "2025"
    d.mkdir(parents=True)
    r = env["client"].get(f"/api/output/2025/{name}")
    assert 400 <= r.status_code < 500
    assert "geheim" not in r.text


def test_output_missing_file_404(env):
    assert env["client"].get("/api/output/2025/gibtes.pdf").status_code == 404


def test_flex_fetch_without_token_400(env):
    r = env["client"].post("/api/flex/fetch", json={"year": 2025})
    assert r.status_code == 400


def test_flex_fetch_token_without_queries_400(env):
    _write_settings(env, flex_token="TOKEN-1234")
    r = env["client"].post("/api/flex/fetch", json={"year": 2025})
    assert r.status_code == 400


def test_workflow_all_without_token_400(env):
    assert env["client"].post("/api/workflow/all", json={"year": 2025}).status_code == 400


def test_generate_without_files_400(env):
    assert env["client"].post("/api/generate", json={"year": 2025}).status_code == 400


def test_invalid_year_400(env):
    assert env["client"].post("/api/generate", json={"year": 1800}).status_code == 400


def test_job_unknown_404(env):
    assert env["client"].get("/api/jobs/deadbeef").status_code == 404


def test_parallel_job_returns_409(env):
    env["upload"].mkdir(parents=True, exist_ok=True)
    (env["upload"] / "konto.xml").write_bytes(b"<x/>")
    release = threading.Event()

    def blocker(job):
        release.wait(10)
        return True

    running = main.jobs.start("test", "blockiert", blocker)
    assert running is not None
    try:
        r = env["client"].post("/api/generate", json={"year": 2025, "files": ["konto.xml"]})
        assert r.status_code == 409
        r2 = env["client"].post("/api/kursliste/2025/download")
        assert r2.status_code == 409
        status = env["client"].get("/api/status").json()
        assert status["running_job"] == running.id
    finally:
        release.set()


def test_settings_api_never_returns_token(env):
    c = env["client"]
    r = c.put("/api/settings", json={"flex_token": "GEHEIM-TOKEN-5678"})
    assert r.status_code == 200
    assert "GEHEIM" not in r.text
    assert r.json()["flex_token_masked"].endswith("5678")
    assert "GEHEIM" not in c.get("/api/settings").text
    # Token wurde tatsaechlich in der tmp-Datei gespeichert, nicht maskiert
    assert "GEHEIM-TOKEN-5678" in env["settings"].read_text(encoding="utf-8")


def test_settings_api_empty_and_masked_do_not_overwrite(env):
    c = env["client"]
    c.put("/api/settings", json={"flex_token": "ALT-TOKEN-0001"})
    c.put("/api/settings", json={"flex_token": ""})
    c.put("/api/settings", json={"flex_token": "*************0001"})
    assert "ALT-TOKEN-0001" in env["settings"].read_text(encoding="utf-8")


def test_settings_api_clear_token(env):
    c = env["client"]
    c.put("/api/settings", json={"flex_token": "ALT-TOKEN-0001"})
    r = c.put("/api/settings", json={"clear_token": True})
    assert r.json()["flex_token_set"] is False
    assert "ALT-TOKEN" not in env["settings"].read_text(encoding="utf-8")


def test_index_served(env):
    r = env["client"].get("/")
    assert r.status_code == 200 and "<html" in r.text.lower()


# ---------------------------------------------------------------------------
# Upload-Validierung: alles oder nichts, Inhaltspruefung, Groessenlimit
# ---------------------------------------------------------------------------

VALID_XML = b"<FlexQueryResponse><FlexStatements/></FlexQueryResponse>"


def _upload(env, files):
    return env["client"].post("/api/upload", files=[
        ("files", (name, data, "application/xml")) for name, data in files])


def _nothing_saved(env):
    return not env["upload"].exists() or not any(env["upload"].iterdir())


@pytest.mark.parametrize("order", ["gueltig_zuerst", "ungueltig_zuerst"])
def test_mixed_upload_rejected_and_nothing_saved(env, order):
    files = [("gueltig.xml", VALID_XML), ("ungueltig.txt", b"hallo")]
    if order == "ungueltig_zuerst":
        files.reverse()
    r = _upload(env, files)
    assert r.status_code == 400
    assert "ungueltig.txt" in r.json()["detail"]
    assert _nothing_saved(env)


@pytest.mark.parametrize("label,data", [
    ("nicht_xml.xml", b"hallo welt"),
    ("leer.xml", b""),
    ("falsche_wurzel.xml", b"<Foo><FlexStatements/></Foo>"),
    ("doctype.xml", b'<?xml version="1.0"?><!DOCTYPE FlexQueryResponse [<!ENTITY x "y">]>'
                    b"<FlexQueryResponse/>"),
    ("doctype_klein.xml", b"<!doctype FlexQueryResponse><FlexQueryResponse/>"),
    ("entity.xml", b'<!ENTITY x "y"><FlexQueryResponse>&x;</FlexQueryResponse>'),
])
def test_invalid_xml_content_rejected(env, label, data):
    r = _upload(env, [(label, data)])
    assert r.status_code == 400
    assert label in r.json()["detail"]
    assert _nothing_saved(env)


def test_bad_content_next_to_valid_file_saves_nothing(env):
    r = _upload(env, [("gueltig.xml", VALID_XML), ("falsch.xml", b"<Foo/>")])
    assert r.status_code == 400
    assert _nothing_saved(env)


def test_oversize_file_rejected_and_nothing_saved(env, monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 100)
    big = b"<FlexQueryResponse>" + b"<x/>" * 100 + b"</FlexQueryResponse>"
    r = _upload(env, [("klein.xml", VALID_XML), ("gross.xml", big)])
    assert r.status_code == 400
    assert "gross.xml" in r.json()["detail"] and "zu gross" in r.json()["detail"]
    assert _nothing_saved(env)


def test_file_exactly_at_limit_is_accepted(env, monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 100)
    exact = VALID_XML + b" " * (100 - len(VALID_XML))
    assert len(exact) == 100
    r = _upload(env, [("genau.xml", exact)])
    assert r.status_code == 200 and r.json()["saved"] == ["genau.xml"]


def test_chunked_reading_still_accepts_valid_file(env, monkeypatch):
    monkeypatch.setattr(main, "CHUNK_SIZE", 7)
    r = _upload(env, [("gechunkt.xml", VALID_XML)])
    assert r.status_code == 200
    assert (env["upload"] / "gechunkt.xml").read_bytes() == VALID_XML


def test_valid_multi_file_upload_saves_all(env):
    second = b"<FlexStatements><FlexStatement/></FlexStatements>"
    r = _upload(env, [("a.xml", VALID_XML), ("b.xml", second)])
    assert r.status_code == 200
    assert sorted(r.json()["saved"]) == ["a.xml", "b.xml"]
    assert (env["upload"] / "a.xml").read_bytes() == VALID_XML
    assert (env["upload"] / "b.xml").read_bytes() == second
    assert sorted(f["name"] for f in env["client"].get("/api/files").json()) == ["a.xml", "b.xml"]


def test_upload_files_history_and_output_share_directories(env):
    c = env["client"]
    assert _upload(env, [("konto.xml", VALID_XML)]).status_code == 200
    out = env["output"] / "2025"
    out.mkdir(parents=True)
    (out / "Steuerauszug_U1_2025.pdf").write_bytes(b"%PDF-1.4 x")

    assert [f["name"] for f in c.get("/api/files").json()] == ["konto.xml"]
    history = c.get("/api/history").json()
    assert [(y["year"], [f["name"] for f in y["files"]]) for y in history] == \
        [(2025, ["Steuerauszug_U1_2025.pdf"])]
    assert c.get("/api/output/2025/Steuerauszug_U1_2025.pdf").status_code == 200
    assert workflow.upload_files() == [env["upload"] / "konto.xml"]
    assert env["tmp"] in st.UPLOAD_DIR.parents and env["tmp"] in st.OUTPUT_DIR.parents


def test_utf16_doctype_with_entity_rejected(env):
    doc = ('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE FlexQueryResponse [<!ENTITY a "xxx">]>'
           "<FlexQueryResponse>&a;</FlexQueryResponse>").encode("utf-16")
    r = _upload(env, [("utf16.xml", doc)])
    assert r.status_code == 400
    assert _nothing_saved(env)


# ---------------------------------------------------------------------------
# Erweiterte Pruefung: expat-basierte DTD-Erkennung, Wurzelelement, Kodierungen,
# Duplikate, atomares Speichern, /api/files mit verwaisten .tmp-Dateien
# ---------------------------------------------------------------------------

DOCTYPE_BODY = '<!DOCTYPE FlexQueryResponse [<!ENTITY x "y">]><FlexQueryResponse/>'
DOCTYPE_NO_SUBSET = "<!DOCTYPE FlexQueryResponse><FlexQueryResponse/>"


def _encode(text: str, encoding: str) -> bytes:
    """Kodiert mit BOM, wie Windows-Tools es typischerweise schreiben."""
    if encoding == "utf-8":
        return b"\xef\xbb\xbf" + text.encode("utf-8")
    if encoding == "utf-16-le":
        return b"\xff\xfe" + text.encode("utf-16-le")
    if encoding == "utf-16-be":
        return b"\xfe\xff" + text.encode("utf-16-be")
    raise ValueError(encoding)


def _billion_laughs() -> str:
    lines = ['<!ENTITY lol "lol">']
    for i in range(1, 10):
        lines.append(f'<!ENTITY lol{i} "' + f"&lol{i - 1};" * 10 + '">')
    return '<?xml version="1.0"?>\n<!DOCTYPE lolz [\n' + "\n".join(lines) + "\n]>\n<FlexQueryResponse>&lol9;</FlexQueryResponse>"


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16-le", "utf-16-be"])
@pytest.mark.parametrize("prolog", ["", "  \r\n\t", "<!-- kommentar -->", "  <!-- k -->\n"])
@pytest.mark.parametrize("body", [DOCTYPE_BODY, DOCTYPE_NO_SUBSET])
def test_doctype_rejected_in_all_encodings_and_prologs(env, encoding, prolog, body):
    r = _upload(env, [("doc.xml", _encode(prolog + body, encoding))])
    assert r.status_code == 400
    assert "DOCTYPE/ENTITY" in r.json()["detail"]
    assert _nothing_saved(env)


def test_billion_laughs_rejected_without_hanging(env):
    doc = _billion_laughs().encode("utf-8")
    result: dict = {}

    def run():
        result["r"] = _upload(env, [("bomb.xml", doc)])

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(15)
    assert not worker.is_alive(), "Upload mit Billion-Laughs-Beispiel haengt"
    assert result["r"].status_code == 400
    assert "DOCTYPE/ENTITY" in result["r"].json()["detail"]
    assert _nothing_saved(env)


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be"])
def test_valid_flex_xml_utf16_accepted(env, encoding):
    text = ('<?xml version="1.0" encoding="UTF-16"?>\n'
            "<FlexQueryResponse><FlexStatements/></FlexQueryResponse>")
    data = _encode(text, encoding)
    r = _upload(env, [("utf16.xml", data)])
    assert r.status_code == 200 and r.json()["saved"] == ["utf16.xml"]
    assert (env["upload"] / "utf16.xml").read_bytes() == data


def test_wrong_root_element_400(env):
    r = _upload(env, [("falsch.xml", b"<Foo><FlexStatements/></Foo>")])
    assert r.status_code == 400
    assert "unerwartetes Wurzelelement <Foo>" in r.json()["detail"]
    assert _nothing_saved(env)


@pytest.mark.parametrize("data", [
    b"<FlexQueryResponse><FlexStatements></FlexQueryResponse>",
    b"",
    b"<FlexQueryResponse/>junk",
])
def test_broken_xml_400_with_message(env, data):
    r = _upload(env, [("kaputt.xml", data)])
    assert r.status_code == 400
    assert "kein wohlgeformtes XML" in r.json()["detail"]
    assert _nothing_saved(env)


@pytest.mark.parametrize("names", [
    ("a.xml", "..\\a.xml"),
    ("a.xml", "a.xml"),
    ("..\\a.xml", "sub\\a.xml"),
])
def test_duplicate_names_after_safe_name_400(env, names):
    r = _upload(env, [(n, VALID_XML) for n in names])
    assert r.status_code == 400
    assert "doppelter Dateiname" in r.json()["detail"]
    assert _nothing_saved(env)


def test_successful_upload_leaves_no_tmp(env):
    r = _upload(env, [("a.xml", VALID_XML), ("b.xml", VALID_XML)])
    assert r.status_code == 200
    assert sorted(p.name for p in env["upload"].iterdir()) == ["a.xml", "b.xml"]


def test_write_error_returns_500_and_cleans_up(env, monkeypatch):
    real_write = Path.write_bytes
    calls = {"n": 0}

    def flaky_write(self, data):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("Platte voll (simuliert)")
        return real_write(self, data)

    monkeypatch.setattr(Path, "write_bytes", flaky_write)
    r = _upload(env, [("a.xml", VALID_XML), ("b.xml", VALID_XML)])
    assert r.status_code == 500
    assert "nichts gespeichert" in r.json()["detail"]
    assert calls["n"] == 2
    assert _nothing_saved(env)


def test_replace_error_restores_existing_files(env, monkeypatch):
    """Vorhandene Dateien werden bei einem Replace-Fehler aus der Sicherung wiederhergestellt."""
    old = VALID_XML + b"<!-- alt -->"
    env["upload"].mkdir(parents=True, exist_ok=True)
    (env["upload"] / "a.xml").write_bytes(old)
    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] == 3:  # 1: a->bak, 2: a.tmp->a, 3: b.tmp->b scheitert
            raise OSError("gesperrt (simuliert)")
        return real_replace(src, dst)

    monkeypatch.setattr(main.os, "replace", flaky_replace)
    r = _upload(env, [("a.xml", VALID_XML), ("b.xml", VALID_XML)])
    assert r.status_code == 500
    assert "nichts gespeichert" in r.json()["detail"]
    assert (env["upload"] / "a.xml").read_bytes() == old
    assert sorted(p.name for p in env["upload"].iterdir()) == ["a.xml"]
    assert [f["name"] for f in env["client"].get("/api/files").json()] == ["a.xml"]


def test_successful_overwrite_leaves_no_bak(env):
    env["upload"].mkdir(parents=True, exist_ok=True)
    (env["upload"] / "a.xml").write_bytes(VALID_XML + b"<!-- alt -->")
    r = _upload(env, [("a.xml", VALID_XML)])
    assert r.status_code == 200
    assert sorted(p.name for p in env["upload"].iterdir()) == ["a.xml"]
    assert (env["upload"] / "a.xml").read_bytes() == VALID_XML
    assert [f["name"] for f in env["client"].get("/api/files").json()] == ["a.xml"]


def test_replace_error_is_all_or_nothing(env, monkeypatch):
    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("gesperrt (simuliert)")
        return real_replace(src, dst)

    monkeypatch.setattr(main.os, "replace", flaky_replace)
    r = _upload(env, [("a.xml", VALID_XML), ("b.xml", VALID_XML)])
    assert r.status_code == 500
    assert _nothing_saved(env)


def test_case_only_duplicate_rejected(env):
    r = _upload(env, [("b.xml", VALID_XML), ("B.xml", VALID_XML)])
    assert r.status_code == 400


def test_api_files_ignores_orphan_tmp(env):
    env["upload"].mkdir(parents=True, exist_ok=True)
    (env["upload"] / ".verwaist.xml.tmp").write_bytes(VALID_XML)
    (env["upload"] / ".a.xml.tmp").write_bytes(VALID_XML)
    (env["upload"] / "echt.xml").write_bytes(VALID_XML)
    names = [f["name"] for f in env["client"].get("/api/files").json()]
    assert names == ["echt.xml"]


# ---------------------------------------------------------------------------
# Regression mit echten Daten. Nur lesend, Pfad per Umgebungsvariable
# OPENSTEUER_REAL_DATA_DIR. Ohne Variable werden diese Tests uebersprungen.
# Es werden keine Inhalte ausgegeben.
# ---------------------------------------------------------------------------

REAL_DIR = os.environ.get("OPENSTEUER_REAL_DATA_DIR", "")
REAL_FILES = (sorted(p.name for p in Path(REAL_DIR).glob("*.xml"))
              if REAL_DIR and Path(REAL_DIR).is_dir() else [])
real_data = pytest.mark.skipif(not REAL_DIR or not Path(REAL_DIR).is_dir(),
                               reason="OPENSTEUER_REAL_DATA_DIR nicht gesetzt")
real_files = pytest.mark.skipif(not REAL_FILES, reason="keine XML-Dateien in OPENSTEUER_REAL_DATA_DIR")


@real_data
@real_files
@pytest.mark.parametrize("name", REAL_FILES, ids=[f"datei{i}" for i in range(len(REAL_FILES))])
def test_real_file_check_flex_xml(name):
    data = (Path(REAL_DIR) / name).read_bytes()
    assert main._check_flex_xml(data) is None


@real_data
@real_files
@pytest.mark.parametrize("name", REAL_FILES, ids=[f"datei{i}" for i in range(len(REAL_FILES))])
def test_real_file_upload(env, name):
    data = (Path(REAL_DIR) / name).read_bytes()
    r = _upload(env, [(name, data)])
    assert r.status_code == 200
    assert r.json()["saved"] == [name]
    assert (env["upload"] / name).read_bytes() == data


@real_data
@real_files
def test_real_files_all_in_one_upload(env):
    files = [(name, (Path(REAL_DIR) / name).read_bytes()) for name in REAL_FILES]
    r = _upload(env, files)
    assert r.status_code == 200
    assert sorted(r.json()["saved"]) == sorted(REAL_FILES)


# ---------------------------------------------------------------- App-Update / Neustart

from app import selfupdate  # noqa: E402


@pytest.fixture
def exits(monkeypatch):
    got = []
    done = threading.Event()

    def fake_exit(code):
        got.append(code)
        done.set()

    monkeypatch.setattr(main, "exit_process", fake_exit)
    monkeypatch.setattr(main, "RESTART_DELAY", 0)
    selfupdate.reset_state()
    monkeypatch.delenv("STEUERAUSZUG_LAUNCHER", raising=False)
    return got, done


def test_app_update_status_endpoint(env, monkeypatch):
    monkeypatch.setattr(selfupdate, "status", lambda deps=None: {"is_git": False, "reason": "kein Git-Klon"})
    body = env["client"].get("/api/app/update").json()
    assert body == {"is_git": False, "reason": "kein Git-Klon"}


def test_app_update_check_endpoint(env, monkeypatch):
    monkeypatch.setattr(selfupdate, "check", lambda deps=None: {"ok": True, "update_available": True, "behind": 2})
    assert env["client"].post("/api/app/update/check").json()["behind"] == 2


def test_app_update_apply_is_job(env, monkeypatch):
    monkeypatch.setattr(selfupdate, "job_apply", lambda deps=None: (lambda job: (job.log("Update installiert"), True)[1]))
    r = env["client"].post("/api/app/update/apply")
    assert r.status_code == 200
    jid = r.json()["job_id"]
    for _ in range(100):
        snap = env["client"].get(f"/api/jobs/{jid}").json()
        if snap["status"] != "running":
            break
        threading.Event().wait(0.05)
    assert snap["status"] == "done" and "Update installiert" in snap["lines"]


def test_app_update_apply_409_when_job_running(env):
    gate = threading.Event()
    main.jobs.start("x", "x", lambda job: gate.wait(5) or True)
    try:
        assert env["client"].post("/api/app/update/apply").status_code == 409
    finally:
        gate.set()


def test_restart_400_without_launcher(env, exits):
    got, _ = exits
    r = env["client"].post("/api/app/restart")
    assert r.status_code == 400 and "start.command" in r.json()["detail"]
    assert got == []


def test_restart_409_when_job_running(env, exits, monkeypatch):
    monkeypatch.setenv("STEUERAUSZUG_LAUNCHER", "1")
    gate = threading.Event()
    main.jobs.start("x", "x", lambda job: gate.wait(5) or True)
    try:
        assert env["client"].post("/api/app/restart").status_code == 409
    finally:
        gate.set()
    assert exits[0] == []


def test_restart_exits_with_code_3(env, exits, monkeypatch):
    monkeypatch.setenv("STEUERAUSZUG_LAUNCHER", "1")
    got, done = exits
    r = env["client"].post("/api/app/restart")
    assert r.status_code == 200 and r.json() == {"restarting": True}
    assert done.wait(2) and got == [3]


def test_status_has_app_version(env, monkeypatch):
    monkeypatch.setattr(selfupdate, "app_version", lambda deps=None: "v1.2-3-gabc")
    assert env["client"].get("/api/status").json()["app_version"] == "v1.2-3-gabc"
