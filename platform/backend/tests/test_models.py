from app.models import Module, Project


def test_create_project(db):
    p = Project(name="Test")
    db.add(p)
    db.commit()
    assert p.id is not None
    assert p.name == "Test"


def test_create_module(db):
    p = Project(name="Test")
    db.add(p)
    db.commit()
    m = Module(project_id=p.id, name="Auth")
    db.add(m)
    db.commit()
    assert m.id is not None
